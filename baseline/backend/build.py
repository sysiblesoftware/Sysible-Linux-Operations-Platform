"""Sysible Baseline — what a host must be, as a list of things rather than YAML.

A state used to be one textarea of raw Salt SLS. That is the right thing to
SHIP — the host runs `salt-call --local` on real SLS and nothing here changes
that — but it is the wrong thing to ASK FOR. "Declare what this host must be"
should not begin with remembering whether the key is `pkg.installed` or
`pkg.install`, that `service.running` takes `enable` and `service.dead` takes
it too, or which of them wants a list and which a string.

So a state is now a list of ITEMS, each one a kind plus some fields, and this
module turns that list into SLS. The builder is the source; the SLS is the
output. A state that was written as YAML by hand STAYS YAML — see store.py's
`source` column — because converting it back into items would be a lossy guess,
and guessing at the file that configures someone's fleet is not a feature.

Three rules this module follows:

  * The SLS is generated HERE, on the server, not in the browser. It is the
    thing that runs as root on every host the state is assigned to, so it is
    the thing that gets tested.
  * YAML is emitted by PyYAML, never by string formatting. A path containing a
    colon, a value that looks like a boolean, a file body with a line starting
    `- ` — each of those silently produces a different document when a template
    builds the text, and the first anyone knows about it is a state that fails
    on the fleet.
  * Every field an operator can type reaches the host. They are validated here
    and nowhere else matters.
"""
from __future__ import annotations

import re

import yaml

# Field types the console knows how to render. Kept deliberately small.
#   text      one line
#   lines     a textarea split into a list on newlines/commas
#   body      a textarea kept verbatim (file contents)
#   bool      a checkbox
#   choice    a select, from `options`
TEXT, LINES, BODY, BOOL, CHOICE = "text", "lines", "body", "bool", "choice"


def _f(key, label, type_=TEXT, required=False, placeholder="", help_="", options=()):
    return {"key": key, "label": label, "type": type_, "required": required,
            "placeholder": placeholder, "help": help_, "options": list(options)}


# Ownership and permissions, shared by the file and directory kinds.
_OWNER = (_f("user", "Owner", TEXT, placeholder="root"),
          _f("group", "Group", TEXT, placeholder="root"),
          _f("mode", "Mode", TEXT, placeholder="0644"))

# ---------------------------------------------------------------------------
# The kinds. Each one: what it is called, what it asks for, and what it emits.
# `emit` returns (state_id_hint, {module.function: [args]}).
# ---------------------------------------------------------------------------


def _emit_package(it):
    names = it["names"]
    args = [{"pkgs": names}]
    if it.get("version"):
        # One version for one package: Salt's `pkgs` takes {name: version}
        # pairs, and applying one version to a list is never what was meant.
        args = [{"name": names[0], "version": it["version"]}]
    return names[0], {"pkg.installed": args}


def _emit_package_absent(it):
    return it["names"][0], {"pkg.removed": [{"pkgs": it["names"]}]}


def _emit_service(it):
    return it["name"], {"service.running": [{"name": it["name"]},
                                            {"enable": bool(it.get("enable", True))}]}


def _emit_service_off(it):
    return it["name"], {"service.dead": [{"name": it["name"]},
                                         {"enable": False}]}


def _emit_file(it):
    args = [{"name": it["path"]}, {"contents": it.get("contents", "")}]
    for key in ("user", "group", "mode"):
        if it.get(key):
            args.append({key: it[key]})
    args.append({"makedirs": True})
    return it["path"], {"file.managed": args}


def _emit_dir(it):
    args = [{"name": it["path"]}]
    for key in ("user", "group", "mode"):
        if it.get(key):
            args.append({key: it[key]})
    args.append({"makedirs": True})
    return it["path"], {"file.directory": args}


def _emit_line(it):
    return f"{it['path']}-line", {"file.line": [
        {"name": it["path"]}, {"content": it["line"]}, {"mode": "ensure"},
        {"after": it["after"]} if it.get("after") else {"location": "end"}]}


def _emit_user(it):
    args = [{"name": it["name"]}]
    if it.get("groups"):
        args.append({"groups": it["groups"]})
    for key, arg in (("shell", "shell"), ("home", "home"), ("fullname", "fullname")):
        if it.get(key):
            args.append({arg: it[key]})
    if it.get("system"):
        args.append({"system": True})
    return it["name"], {"user.present": args}


def _emit_group(it):
    return it["name"], {"group.present": [{"name": it["name"]}]}


def _emit_sysctl(it):
    return it["name"], {"sysctl.present": [{"name": it["name"]},
                                           {"value": it["value"]}]}


def _emit_cron(it):
    args = [{"name": it["command"]}, {"user": it.get("user") or "root"}]
    for key in ("minute", "hour", "daymonth", "month", "dayweek"):
        if it.get(key):
            args.append({key: it[key]})
    return it["command"], {"cron.present": args}


def _emit_command(it):
    args = [{"name": it["command"]}]
    # Without a guard a cmd.run is applied on EVERY run, which makes every run
    # report a change and makes "changed" meaningless for the whole state.
    if it.get("unless"):
        args.append({"unless": it["unless"]})
    if it.get("creates"):
        args.append({"creates": it["creates"]})
    return it["command"], {"cmd.run": args}


KINDS = {
    "package": {
        "label": "Packages installed",
        "help": "The packages that must be present. One per line.",
        "fields": (_f("names", "Packages", LINES, required=True, placeholder="nginx"),
                   _f("version", "Version", TEXT,
                      help_="Optional, and only for a single package.")),
        "emit": _emit_package,
    },
    "package_absent": {
        "label": "Packages removed",
        "help": "The packages that must NOT be present.",
        "fields": (_f("names", "Packages", LINES, required=True, placeholder="telnet"),),
        "emit": _emit_package_absent,
    },
    "service": {
        "label": "Service running",
        "help": "Started now, and on by default after a reboot.",
        "fields": (_f("name", "Service", TEXT, required=True, placeholder="nginx"),
                   _f("enable", "Also start at boot", BOOL)),
        "defaults": {"enable": True},
        "emit": _emit_service,
    },
    "service_off": {
        "label": "Service stopped",
        "help": "Stopped now, and off at boot.",
        "fields": (_f("name", "Service", TEXT, required=True, placeholder="telnet.socket"),),
        "emit": _emit_service_off,
    },
    "file": {
        "label": "File with this content",
        "help": "The file is created or rewritten to match exactly.",
        "fields": (_f("path", "Path", TEXT, required=True, placeholder="/etc/motd"),
                   _f("contents", "Contents", BODY, required=True),) + _OWNER,
        "emit": _emit_file,
    },
    "dir": {
        "label": "Directory exists",
        "fields": (_f("path", "Path", TEXT, required=True, placeholder="/opt/app"),) + _OWNER,
        "emit": _emit_dir,
    },
    "line": {
        "label": "Line present in a file",
        "help": "The rest of the file is left alone.",
        "fields": (_f("path", "File", TEXT, required=True, placeholder="/etc/hosts"),
                   _f("line", "Line", TEXT, required=True,
                      placeholder="10.0.0.5 db.internal"),
                   _f("after", "Put it after this line", TEXT,
                      help_="Optional. Left empty, the line goes at the end.")),
        "emit": _emit_line,
    },
    "user": {
        "label": "User account",
        "fields": (_f("name", "Username", TEXT, required=True, placeholder="deploy"),
                   _f("fullname", "Full name", TEXT),
                   _f("groups", "Groups", LINES, placeholder="sudo"),
                   _f("shell", "Shell", TEXT, placeholder="/bin/bash"),
                   _f("home", "Home directory", TEXT),
                   _f("system", "System account", BOOL)),
        "emit": _emit_user,
    },
    "group": {
        "label": "Group",
        "fields": (_f("name", "Group", TEXT, required=True, placeholder="developers"),),
        "emit": _emit_group,
    },
    "sysctl": {
        "label": "Kernel parameter (sysctl)",
        "fields": (_f("name", "Parameter", TEXT, required=True,
                      placeholder="net.ipv4.ip_forward"),
                   _f("value", "Value", TEXT, required=True, placeholder="0")),
        "emit": _emit_sysctl,
    },
    "cron": {
        "label": "Scheduled command (cron)",
        "help": "Any field left empty means every — an empty minute is every minute.",
        "fields": (_f("command", "Command", TEXT, required=True,
                      placeholder="/usr/local/bin/backup.sh"),
                   _f("user", "As user", TEXT, placeholder="root"),
                   _f("minute", "Minute", TEXT, placeholder="0"),
                   _f("hour", "Hour", TEXT, placeholder="3"),
                   _f("daymonth", "Day of month", TEXT),
                   _f("month", "Month", TEXT),
                   _f("dayweek", "Day of week", TEXT)),
        "emit": _emit_cron,
    },
    "command": {
        "label": "Run a command",
        "help": ("Runs as root. Give it a guard, or it runs on every apply and "
                 "reports a change every time — which makes 'changed' mean nothing "
                 "for the whole state."),
        "fields": (_f("command", "Command", TEXT, required=True),
                   _f("unless", "Skip when this command succeeds", TEXT,
                      placeholder="test -f /etc/app/.provisioned"),
                   _f("creates", "Skip when this path exists", TEXT,
                      placeholder="/etc/app/.provisioned")),
        "emit": _emit_command,
    },
}

# Fields that are a list in the item but are typed as one box of lines.
_LIST_FIELDS = {f["key"] for k in KINDS.values() for f in k["fields"]
                if f["type"] == LINES}


def catalog() -> list:
    """The kinds, for the console to render its picker and its forms from.

    Served rather than duplicated in JavaScript: the fields the browser draws
    and the fields this module reads are then the same list by construction.
    """
    return [{"kind": key, "label": spec["label"], "help": spec.get("help", ""),
             "fields": [dict(f) for f in spec["fields"]],
             "defaults": dict(spec.get("defaults", {}))}
            for key, spec in KINDS.items()]


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in re.split(r"[\n,]", str(value or "")) if p.strip()]


def normalise(items) -> list:
    """Items as this module will read them: known kinds, known fields, lists
    split, booleans actual booleans. Anything else is dropped rather than
    passed through — an unknown key would reach a host's state file."""
    out = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        spec = KINDS.get(kind)
        if spec is None:
            continue
        item = {"kind": kind}
        for field in spec["fields"]:
            key, ftype = field["key"], field["type"]
            if ftype == LINES:
                item[key] = _as_list(raw.get(key))
            elif ftype == BOOL:
                item[key] = bool(raw.get(key, spec.get("defaults", {}).get(key, False)))
            elif ftype == BODY:
                item[key] = str(raw.get(key) or "")
            else:
                item[key] = str(raw.get(key) or "").strip()
        out.append(item)
    return out


def problems(items) -> list:
    """Everything wrong with these items, in operator words, all at once.

    All at once on purpose: a form that reports one error per save makes you
    discover a four-field mistake four round-trips at a time.
    """
    msgs = []
    if not items:
        return ["Add at least one thing this state must make true."]
    for n, item in enumerate(items, 1):
        spec = KINDS[item["kind"]]
        where = f"{spec['label']} (#{n})"
        for field in spec["fields"]:
            if not field["required"]:
                continue
            value = item.get(field["key"])
            if not value:
                msgs.append(f"{where}: {field['label'].lower()} is required.")
        if item["kind"] == "package" and item.get("version") and len(item["names"]) > 1:
            msgs.append(f"{where}: a version applies to ONE package, and "
                        f"{len(item['names'])} are listed.")
        for key in ("path", "name", "command"):
            value = item.get(key)
            if isinstance(value, str) and "\n" in value:
                msgs.append(f"{where}: {key} cannot contain a line break.")
    return msgs


_SLUG = re.compile(r"[^a-z0-9]+")


def _state_id(kind: str, hint: str, used: set) -> str:
    base = f"{kind}_{_SLUG.sub('_', str(hint).lower()).strip('_')}" or kind
    base = base[:60].rstrip("_") or kind
    sid, n = base, 1
    while sid in used:
        n += 1
        sid = f"{base}_{n}"
    used.add(sid)
    return sid


class _Dumper(yaml.SafeDumper):
    """Block style for anything with a newline in it.

    Without this a file's contents come back as one enormous double-quoted
    scalar full of \\n, which is valid and unreadable — and the YAML view is a
    feature, not a debug aid.
    """


def _str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def to_sls(items) -> str:
    """The Salt SLS these items describe.

    Built as a Python structure and dumped ONCE. Nothing here formats YAML as
    text: a path with a colon in it, a value that reads as a boolean, a file
    body whose first line starts with "- " — each of those produces a different
    document when a template builds the string, and the first anyone knows
    about it is a state that fails on the fleet.
    """
    items = normalise(items)
    doc, used = {}, set()
    for item in items:
        hint, body = KINDS[item["kind"]]["emit"](item)
        doc[_state_id(item["kind"], hint, used)] = body
    if not doc:
        return ""
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False,
                     default_flow_style=False, allow_unicode=True, width=10000)
