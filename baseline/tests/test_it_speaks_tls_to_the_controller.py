"""Baseline addresses the Controller the way the Controller actually listens.

Reported: "Baseline can't connect to controller", over a console reading

    could not reach the Controller (RemoteProtocolError), so this is the fleet
    it knows about rather than a list — type the target instead

The Controller's own Dockerfile says what :8800 is: `uvicorn webgui.server:app
on :8800 (React web console BFF, TLS)`, and its healthcheck probes it with
`curl -fsk https://127.0.0.1:8800/api/health`. The SLOP gateway proxies to it as
`https://...` with tls_insecure_skip_verify, and Flashback's client defaults to
https for the same reason.

Baseline's did not. It read:

    https:// if ":443" in upstream else http://

and the configured upstream is `host.docker.internal:8800`, which contains no
":443" — so every request went out as PLAIN HTTP to a TLS listener. uvicorn
closes such a connection without a response, and httpx raises
RemoteProtocolError. It reads like a network fault. Nothing was wrong with the
network: the Controller was up, and answering the gateway on that very port.

It cost both calls this module makes, so Apply was broken too — reported there
as "The Controller is unreachable (RemoteProtocolError)".

These tests pin the scheme, and then prove it against a real uvicorn TLS server,
because the bug lived precisely in the gap between "the code builds a url" and
"something on the other end accepts it".

Run: pytest baseline/tests/
"""
import os
import shutil
import ssl
import subprocess
import sys
import time
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from backend import controller


class Ident:
    user, role = "alice", "operator"


@pytest.fixture
def upstream(monkeypatch):
    """Set the configured Controller address for one test."""
    def set_to(value):
        monkeypatch.setattr(controller, "_CONTROLLER", value)
        # Without a secret the client refuses before it builds a url at all, so
        # a test with no secret proves the refusal rather than the scheme.
        monkeypatch.setattr(controller, "_SSO_SECRET", "s" * 32)
        return controller
    return set_to


# ---- the scheme ------------------------------------------------------------
@pytest.mark.parametrize("configured,expected", [
    # The default, and the one that was broken.
    ("host.docker.internal:8800", "https://host.docker.internal:8800"),
    ("10.0.0.5:8800",             "https://10.0.0.5:8800"),
    # A bare name with no port is still TLS.
    ("controller",                "https://controller"),
    # An explicit scheme is an instruction, not a hint: an operator who has put
    # the Controller behind something that terminates TLS says so, and gets it.
    ("http://controller:8800",    "http://controller:8800"),
    ("https://controller:8800",   "https://controller:8800"),
    # Tidied, so a stray slash or space does not produce a doubled path.
    ("  controller:8800/  ",      "https://controller:8800"),
    ("https://controller:8800/",  "https://controller:8800"),
])
def test_the_controller_is_addressed_as_tls(upstream, configured, expected):
    upstream(configured)
    assert controller._url() == expected


def test_the_port_number_does_not_decide_the_scheme(upstream):
    """The old rule keyed off ":443" being in the string. The Controller's TLS
    port is 8800, so the one address that mattered took the http branch."""
    upstream("host.docker.internal:8800")
    assert controller._url().startswith("https://"), \
        "the default upstream is addressed as plain http — this is the bug"


def test_baseline_and_flashback_agree(upstream):
    """They talk to the SAME service on the same port. Two SLOP apps disagreeing
    about its scheme means one of them is always wrong."""
    fb = os.path.join(os.path.dirname(HERE), "flashback")
    if not os.path.isdir(fb):
        pytest.skip("the flashback app is not in this checkout")
    addrs = ["host.docker.internal:8800", "10.0.0.5:8800", "controller",
             "http://controller:8800", "https://controller:8800/"]
    # In its own process: Flashback's module is part of ITS backend package, and
    # this one has already bound `backend` to Baseline's. Importing both in one
    # interpreter is the exact name collision that makes a suite lie.
    src = ("import sys, json\n"
           "sys.path.insert(0, sys.argv[1])\n"
           "from backend import controller as c\n"
           "out = []\n"
           "for a in sys.argv[2:]:\n"
           "    c._CONTROLLER = a\n"
           "    out.append(c._url())\n"
           "print(json.dumps(out))\n")
    r = subprocess.run([sys.executable, "-c", src, fb, *addrs],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip(f"could not ask Flashback: {r.stderr.strip()[:200]}")
    import json
    theirs = json.loads(r.stdout)
    for addr, their in zip(addrs, theirs, strict=True):
        upstream(addr)
        assert controller._url() == their, \
            f"for {addr!r}: Baseline says {controller._url()}, Flashback says {their}"


# ---- against a real TLS listener -------------------------------------------
@pytest.fixture
def tls_controller(tmp_path):
    """A uvicorn TLS server, which is the Controller's actual stack.

    Not a mock: the failure was a protocol mismatch, and a fake that answers
    whatever scheme it is asked cannot reproduce a protocol mismatch. The old
    code raises RemoteProtocolError against this and the new code gets its JSON.
    """
    if not shutil.which("openssl"):
        pytest.skip("no openssl to make a certificate with")
    try:
        import uvicorn  # noqa: F401
        from fastapi import FastAPI  # noqa: F401
    except ImportError:
        pytest.skip("uvicorn/fastapi not available")

    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    rc = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                         "-keyout", str(key), "-out", str(cert), "-days", "1",
                         "-nodes", "-subj", "/CN=localhost"],
                        capture_output=True)
    if rc.returncode != 0:
        pytest.skip("openssl could not make a certificate")

    app_file = tmp_path / "ctl_stub.py"
    app_file.write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/hosts')\n"
        "def hosts():\n"
        "    return {'hosts': [{'id': 'web-01', 'label': 'web-01',\n"
        "                       'environment': 'prod', 'kind': 'agent'}]}\n"
        "@app.post('/api/host/{h}/baseline-apply')\n"
        "def apply(h: str):\n"
        "    return {'detail': 'queued for ' + h}\n")

    port = 18890
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--app-dir", str(tmp_path),
         "ctl_stub:app", "--host", "127.0.0.1", "--port", str(port),
         "--ssl-keyfile", str(key), "--ssl-certfile", str(cert),
         "--log-level", "critical"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait for the TLS port to accept, rather than sleeping a guess.
    deadline = time.time() + 30
    ctx = ssl._create_unverified_context()
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.skip("the stub Controller did not start")
        try:
            import socket
            with socket.create_connection(("127.0.0.1", port), timeout=1) as sk, \
                    ctx.wrap_socket(sk, server_hostname="localhost"):
                break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.skip("the stub Controller never came up")
    try:
        yield f"127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:            # pragma: no cover
            proc.kill()


def test_the_host_list_arrives_from_a_tls_controller(upstream, tls_controller):
    upstream(tls_controller)
    hosts, envs, note = controller.list_targets(Ident())
    assert note is None, f"it could not read the fleet: {note}"
    assert [h["id"] for h in hosts] == ["web-01"], hosts
    assert envs == ["prod"], envs


def test_apply_reaches_a_tls_controller(upstream, tls_controller):
    """The same url, so the same bug: Apply reported "The Controller is
    unreachable" for a Controller that was running."""
    upstream(tls_controller)
    ok, message = controller.request_apply(Ident(), "web-01")
    assert ok, message
    assert "web-01" in message, message


def test_plain_http_to_that_server_is_what_the_operator_saw(tls_controller):
    """The other half of the proof: the OLD scheme really does produce the error
    from the report, against this same server. Without this, the two tests above
    would pass just as happily if the bug had never existed."""
    httpx = pytest.importorskip("httpx")
    with pytest.raises(Exception) as caught, \
            httpx.Client(timeout=10, verify=False) as c:
        c.get(f"http://{tls_controller}/api/hosts")
    assert "Protocol" in type(caught.value).__name__ or \
           "Read" in type(caught.value).__name__, \
        f"expected a protocol-level failure, got {type(caught.value).__name__}"
