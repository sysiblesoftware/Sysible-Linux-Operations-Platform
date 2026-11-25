"""Administration → TLS: the certificate the platform's front door presents.

SLOP terminates TLS for the whole platform. A browser only ever talks to the
gateway — the apps behind it are reached by path on one origin — so the
gateway's certificate is the only one anybody's browser ever judges, and it is
the one that produces the warning on every first visit.

Until now it was also the only one with no way to replace it. Caddy mints a
self-signed certificate under a fixed internal name (`tls internal` on the
sysible.slop site, served for every raw-IP request via default_sni), and the
Caddyfile's advice was to "drop a real cert in front" — i.e. put something else
in front of the front door. Meanwhile the Controller, whose certificate no
browser sees, has had an install/regenerate/trust-bundle API for ages. That was
backwards, and this is the half that was missing.

HOW IT APPLIES. The gateway reads /etc/caddy/tls/*.caddy from a volume this
service writes. An empty directory is a valid import in Caddy (verified against
a real caddy: a glob matching no files adapts cleanly), so with nothing
installed the gateway simply uses its internal certificate and the file does not
need to exist.

ORDER MATTERS, AND IT IS A FRONT-DOOR OUTAGE IF IT IS WRONG. A snippet naming
files that are not there does not degrade — Caddy refuses to start:

    Error: loading http app module: ... loading certificates:
    open /etc/caddy/tls/cert.pem: no such file or directory

That is :443 for the entire platform, including the page you would use to undo
it. So the certificate and key are written and PROVEN loadable first, and the
snippet that points at them is written last; on the way out the snippet goes
first. Nothing here ever leaves the gateway pointed at a file that is not there.

STDLIB ONLY. The IdP ships fastapi, uvicorn and python-multipart and nothing
else, on purpose, so the identity provider stays small enough to audit. That
rules out `cryptography`, which is what the apps use. It costs nothing here:
ssl.SSLContext.load_cert_chain is the same OpenSSL that Caddy will use, so it
rejects a key that does not match its certificate ([X509: KEY_VALUES_MISMATCH])
and a swapped cert/key pair, which is most of what an operator gets wrong.
"""
from __future__ import annotations

import datetime
import os
import re
import ssl
import tempfile

# Where THIS service writes. The gateway mounts the same volume read-only.
DIR = os.environ.get("SLOP_TLS_DIR", "/tls")
# Where the GATEWAY sees them — what the snippet must say. Writing our own paths
# into it would name files that exist here and not there, which is the outage
# described above.
GATEWAY_DIR = os.environ.get("SLOP_TLS_GATEWAY_DIR", "/etc/caddy/tls")

CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
# Sorts after anything a future default might add, so an operator's certificate
# wins if both are ever present.
SNIPPET_NAME = "10-operator.caddy"

_PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def _path(name: str) -> str:
    return os.path.join(DIR, name)


def installed() -> bool:
    """True when the gateway is being told to use an operator certificate."""
    return os.path.isfile(_path(SNIPPET_NAME))


def _decode(path: str) -> dict:
    """Subject / issuer / validity of a certificate on disk, best effort."""
    try:
        return ssl._ssl._test_decode_cert(os.path.abspath(path))  # noqa: SLF001
    except Exception:  # noqa: BLE001 — display only; never block on it
        return {}


def _flatten(rdn) -> str:
    """OpenSSL's nested subject tuples -> one line.

    The names come out long -- "commonName=front.example", not "CN=..." --
    because that is what the stdlib binding returns, and it reads better on a
    page than the abbreviations do.
    """
    out = []
    for part in rdn or ():
        for pair in part:
            if len(pair) == 2:
                out.append(f"{pair[0]}={pair[1]}")
    return ", ".join(out)


def describe() -> dict:
    """What is installed, for the page to show. Empty dict when nothing is."""
    if not installed():
        return {}
    cert = _path(CERT_NAME)
    if not os.path.isfile(cert):
        # The snippet is here and the certificate is not. The gateway cannot
        # start in this state, so say so rather than render a tidy blank.
        return {"broken": True}
    d = _decode(cert)
    return {
        "subject": _flatten(d.get("subject")),
        "issuer": _flatten(d.get("issuer")),
        "not_after": d.get("notAfter", ""),
        "expired": _expired(d.get("notAfter", "")),
        "names": [v for k, v in d.get("subjectAltName", ()) if k == "DNS" or k == "IP Address"],
    }


def _parse_openssl_date(text: str):
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def _expired(not_after: str) -> bool:
    when = _parse_openssl_date(not_after)
    return bool(when and when < datetime.datetime.utcnow())


def install(cert_pem: str, key_pem: str) -> tuple[bool, str]:
    """Validate a certificate and key, then put the gateway on them.

    Returns (ok, message). Nothing is written unless the pair loads, and the
    snippet is written last.
    """
    cert_pem = (cert_pem or "").strip()
    key_pem = (key_pem or "").strip()
    if not cert_pem or not key_pem:
        return False, "Both a certificate and its private key are required."
    if not _PEM_CERT.search(cert_pem):
        return False, ("That does not look like a PEM certificate — it should begin "
                       "with -----BEGIN CERTIFICATE-----.")
    if "PRIVATE KEY" not in key_pem:
        return False, ("That does not look like a PEM private key — it should contain "
                       "-----BEGIN PRIVATE KEY----- (or BEGIN RSA/EC PRIVATE KEY).")
    # Two spellings, and the first one is NOT in the BEGIN line: PKCS#8 says
    # "BEGIN ENCRYPTED PRIVATE KEY", while a traditional PEM key keeps an
    # ordinary BEGIN line and carries "Proc-Type: 4,ENCRYPTED" underneath it.
    # Looking only at the header missed the second, and OpenSSL then reported
    # the unhelpful "[SSL] PEM lib" instead of the thing to do about it.
    if "ENCRYPTED PRIVATE KEY" in key_pem or re.search(r"Proc-Type:\s*\d+,\s*ENCRYPTED", key_pem):
        return False, ("That private key is passphrase-protected. The gateway starts "
                       "unattended and cannot be prompted for one — decrypt it first "
                       "(openssl rsa -in key.pem -out decrypted.pem).")

    # The directory is the SHARED VOLUME, and it must already be there: the
    # mount creates it. Creating it ourselves would make a private one inside
    # this container, and then every install would report success while the
    # gateway went on serving its own certificate with nothing to explain why.
    # A stack that predates this feature is exactly that case, so say what to do
    # about it rather than silently do nothing.
    if not os.path.isdir(DIR):
        return False, ("The TLS volume is not mounted, so there is nowhere the gateway "
                       "would read this from. Update the platform and re-create the "
                       "stack (sysiblectl slop up) so the gateway and this service "
                       "share it, then try again.")
    tmpdir = tempfile.mkdtemp(prefix=".staging-", dir=DIR)
    tcert, tkey = os.path.join(tmpdir, CERT_NAME), os.path.join(tmpdir, KEY_NAME)
    try:
        with open(tcert, "w", encoding="utf-8") as fh:
            fh.write(cert_pem + "\n")
        # The key is readable by this service and the gateway and nobody else.
        with open(os.open(tkey, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640), "w",
                  encoding="utf-8") as fh:
            fh.write(key_pem + "\n")

        # The same OpenSSL the gateway will use. A key that does not match its
        # certificate fails here rather than at the gateway's next start.
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(tcert, tkey)
        except ssl.SSLError as e:
            if "KEY_VALUES_MISMATCH" in str(e):
                return False, ("That private key does not belong to that certificate. "
                               "Check you pasted the matching pair.")
            return False, f"OpenSSL would not load that pair: {e}"
        except Exception as e:  # noqa: BLE001
            return False, f"OpenSSL would not load that pair: {e}"

        info = _decode(tcert)
        if _expired(info.get("notAfter", "")):
            return False, (f"That certificate expired on {info.get('notAfter')}. "
                           "Browsers will refuse it, so it is not installed.")

        # REPLACING one that is already live: take the snippet down first.
        # Between the two renames below there is a moment with a new
        # certificate and the old key. It takes a full disk or a dying
        # filesystem to stop a rename inside one directory, but if it happens
        # while a snippet is live the gateway is pointed at a mismatched pair
        # and will not start. Without the snippet it falls back to its own
        # certificate: the front door stays up, with the warning it had before
        # anyone touched this. Up and warning beats down.
        if os.path.exists(_path(SNIPPET_NAME)):
            os.unlink(_path(SNIPPET_NAME))
        # Files into place FIRST, snippet LAST — see the module docstring.
        os.replace(tcert, _path(CERT_NAME))
        os.replace(tkey, _path(KEY_NAME))
        snippet = (f"# Written by SLOP Administration -> TLS. Remove this file to go\n"
                   f"# back to the gateway's own self-signed certificate.\n"
                   f"tls {GATEWAY_DIR}/{CERT_NAME} {GATEWAY_DIR}/{KEY_NAME}\n")
        tmp_snip = _path(SNIPPET_NAME + ".new")
        with open(tmp_snip, "w", encoding="utf-8") as fh:
            fh.write(snippet)
        os.replace(tmp_snip, _path(SNIPPET_NAME))
    except OSError as e:
        # A full disk, a read-only mount. Report it instead of a 500 — and note
        # that the snippet was taken down above, so whatever half-written state
        # this leaves, the gateway is not pointed at it.
        return False, (f"Could not write the certificate: {e}. The gateway is using "
                       "its own certificate until this succeeds.")
    finally:
        for p in (tcert, tkey):
            if os.path.exists(p):
                os.unlink(p)
        if os.path.isdir(tmpdir):
            os.rmdir(tmpdir)

    subject = _flatten(info.get("subject")) or "the certificate"
    return True, (f"Installed {subject}, valid until {info.get('notAfter', 'an unstated date')}. "
                  "Restart the gateway to start serving it.")


def revert() -> tuple[bool, str]:
    """Go back to the gateway's own self-signed certificate.

    Snippet first: while it exists the gateway is told to load files, so
    removing them in the other order is a window in which a restart fails.
    """
    if not installed() and not os.path.exists(_path(CERT_NAME)):
        return False, "No certificate is installed — the gateway is already using its own."
    try:
        if os.path.exists(_path(SNIPPET_NAME)):
            os.unlink(_path(SNIPPET_NAME))
        for name in (CERT_NAME, KEY_NAME):
            if os.path.exists(_path(name)):
                os.unlink(_path(name))
    except OSError as e:
        return False, f"Could not remove the installed certificate: {e}"
    return True, ("Removed. The gateway goes back to its own self-signed certificate "
                  "when it restarts.")
