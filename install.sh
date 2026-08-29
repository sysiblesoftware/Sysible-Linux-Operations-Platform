#!/bin/sh
# install.sh — stand up the whole CE SLOP stack from THIS repo, in one command.
#
# SLOP is the single front door for the three Sysible apps. This installer brings
# up all of them so a standalone `git clone` of this repo is all you need:
#
#   1. Controller, SLEP and Connect are cloned to /opt/sysible-src/<repo> and
#      brought up as containers via the suite's unified `sysible_ctl` CLI (which
#      this script installs, from the Controller checkout, so you can manage
#      everything afterward: sysible_ctl status | update all | logs …).
#   2. The SLOP gateway (Caddy + portal) is brought up FROM THIS CHECKOUT, in
#      front of the three apps.
#
# Usage (run from the repo root):
#   sudo ./install.sh              # apps + gateway (the whole stack)
#   sudo ./install.sh gateway      # ONLY the gateway (apps already running)
#   sudo ./install.sh apps         # ONLY the three apps (no gateway)
#
# Requirements: git, Docker Engine + the compose plugin. Nothing is re-hosted —
# every app is cloned from its own official repo and built from source.
set -eu

CTL_REPO="https://github.com/sysiblesoftware/sysible-controller"
SLEP_REPO="https://github.com/sysiblesoftware/sysible-linux-engineering-platform"
CONNECT_REPO="https://github.com/sysiblesoftware/sysible-connect"
SRC_DIR="${SYSIBLE_SRC_DIR:-/opt/sysible-src}"

# This SLOP checkout (resolve through a symlinked invocation too).
_SRC="$0"; command -v readlink >/dev/null 2>&1 && _SRC="$(readlink -f "$0" 2>/dev/null || echo "$0")"
HERE="$(cd "$(dirname "$_SRC")" && pwd)"

say()  { printf '%s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# What to do (default: everything).
WANT_APPS=1; WANT_GW=1
case "${1:-all}" in
  all|"")   WANT_APPS=1; WANT_GW=1 ;;
  apps)     WANT_APPS=1; WANT_GW=0 ;;
  gateway|gw) WANT_APPS=0; WANT_GW=1 ;;
  *) die "unknown argument '$1' (use: all | apps | gateway)" ;;
esac

[ "$(id -u)" -eq 0 ] || exec sudo -- "$0" "$@"
command -v git >/dev/null 2>&1 || die "git is required."
command -v docker >/dev/null 2>&1 || die "docker is required (Docker Engine + the compose plugin)."

dirname_for() { basename "$1"; }
clone_one() {  # clone_one <repo-url>
  _d="$SRC_DIR/$(dirname_for "$1")"
  if [ -d "$_d/.git" ]; then
    say "== updating $(dirname_for "$1") =="; git -C "$_d" pull --ff-only || true
  else
    say "== cloning $(dirname_for "$1") =="; git clone --depth 1 "$1" "$_d"
  fi
}

mkdir -p "$SRC_DIR"

# The Controller checkout ships the unified sysible_ctl — clone it regardless (we
# need the CLI to drive every product, including this gateway) and put it on PATH.
CTL_DIR="$SRC_DIR/$(dirname_for "$CTL_REPO")"
clone_one "$CTL_REPO" || die "could not clone the Controller repo (check network/DNS) — nothing was installed."
if [ -x "$CTL_DIR/deploy/sysible_ctl" ]; then
  ln -sf "$CTL_DIR/deploy/sysible_ctl" /usr/local/bin/sysible_ctl
  say "Installed sysible_ctl -> /usr/local/bin/sysible_ctl"
else
  die "the Controller checkout has no deploy/sysible_ctl (older main?) — update and retry."
fi

# Everything below runs in containers, so the Docker daemon MUST be up. Start it
# and wait briefly, with a clear error (not a silent abort) if it never comes up.
if ! docker info >/dev/null 2>&1; then
  say "== starting the Docker daemon =="
  systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true
  _i=0; while [ "$_i" -lt 10 ] && ! docker info >/dev/null 2>&1; do sleep 2; _i=$((_i+1)); done
fi
docker info >/dev/null 2>&1 || die "the Docker daemon is not running and could not be started (try: sudo systemctl start docker). Nothing can run in containers until it is up."

# ---- unified SSO shared secret (one per install; persisted in this repo's .env) ----
# The gateway stamps this secret on requests it proxies; each app trusts the
# gateway-asserted identity ONLY when the secret matches. Generate it once and
# reuse it, so the same value reaches the gateway AND all three apps.
ENV_FILE="$HERE/.env"
_upsert_env() {  # _upsert_env KEY VALUE — set KEY=VALUE in $ENV_FILE (replace or append)
  touch "$ENV_FILE"
  if grep -q "^$1=" "$ENV_FILE" 2>/dev/null; then
    sed -i.bak "s|^$1=.*|$1=$2|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
  fi
}
_secret_from_env() { [ -f "$ENV_FILE" ] && sed -n 's/^SYSIBLE_SSO_SHARED_SECRET=\(..*\)$/\1/p' "$ENV_FILE" | tail -n1; }
SSO_SECRET="${SYSIBLE_SSO_SHARED_SECRET:-$(_secret_from_env)}"
if [ -z "$SSO_SECRET" ]; then
  SSO_SECRET="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  _upsert_env SYSIBLE_SSO_SHARED_SECRET "$SSO_SECRET"
  say "Generated a unified-SSO shared secret into $ENV_FILE"
fi
export SYSIBLE_SSO_SHARED_SECRET="$SSO_SECRET"

# ---- the three apps (best-effort: one failing never stops the rest) ------
FAILED=""
if [ "$WANT_APPS" -eq 1 ]; then
  # controller (already cloned above) + slep + connect. The 4th field is each
  # app's "trust the SLOP gateway identity" flag — set to 1 here so the app trusts
  # the gateway-asserted identity (guarded by the shared secret above).
  for entry in "controller|$CTL_REPO|SYSIBLE_CONTROLLER_DIR|SYSIBLE_WEBGUI_TRUST_SSO" \
               "slep|$SLEP_REPO|SYSIBLE_SLEP_DIR|SLEP_TRUST_GATEWAY_AUTH" \
               "connect|$CONNECT_REPO|SYSIBLE_CONNECT_DIR|SYSIBLE_CONNECT_TRUST_GATEWAY_AUTH"; do
    p="${entry%%|*}"; r1="${entry#*|}"; repo="${r1%%|*}"; r2="${r1#*|}"
    var="${r2%%|*}"; trust="${r2#*|}"
    say
    say "============================================================"
    say " $p — cloning the code, then building + starting its container(s)"
    say "============================================================"
    if [ "$p" != "controller" ] && ! clone_one "$repo"; then
      FAILED="$FAILED $p(clone)"; say "  WARNING: could not clone $p — skipping it."; continue
    fi
    _dir="$SRC_DIR/$(dirname_for "$repo")"
    # Pass the app dir, turn its SSO trust flag on, and hand it the shared secret.
    if env "$var=$_dir" "$trust=1" SYSIBLE_SSO_SHARED_SECRET="$SSO_SECRET" sysible_ctl "$p" up; then
      say "  $p is up."
    else
      FAILED="$FAILED $p"; say "  WARNING: $p did not come up — continuing (scroll up for the error)."
    fi
  done
fi

# ---- the gateway (FROM THIS CHECKOUT) ------------------------------------
if [ "$WANT_GW" -eq 1 ]; then
  say
  say "============================================================"
  say " SLOP gateway — the single front door (this repo: $HERE)"
  say "============================================================"
  if env SYSIBLE_SLOP_DIR="$HERE" sysible_ctl slop up; then
    say "  SLOP gateway is up."
  else
    FAILED="$FAILED slop-gateway"; say "  WARNING: the SLOP gateway did not come up (scroll up for the error)."
  fi
fi

say
if [ -n "$FAILED" ]; then
  say "Finished WITH PROBLEMS — these did not come up:$FAILED"
  say "Inspect with 'sysible_ctl status'; the errors above are usually network, DNS, or a Docker build."
else
  say "All done — the code is cloned under $SRC_DIR and everything is running in containers."
fi
if [ "$WANT_GW" -eq 1 ] && [ -z "$FAILED" ]; then
  say "One front door is up: https://slop.lan/  (point slop.lan + the"
  say "controller./slep./connect. subdomains at this host — DNS or /etc/hosts;"
  say "set SLOP_DOMAIN + upstreams in this repo's .env to change the defaults)."
fi
say "Manage everything with:  sysible_ctl status   |   sysible_ctl update all"
