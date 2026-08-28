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
clone_one "$CTL_REPO"
if [ -x "$CTL_DIR/deploy/sysible_ctl" ]; then
  ln -sf "$CTL_DIR/deploy/sysible_ctl" /usr/local/bin/sysible_ctl
  say "Installed sysible_ctl -> /usr/local/bin/sysible_ctl"
else
  die "the Controller checkout has no deploy/sysible_ctl (older main?) — update and retry."
fi

# ---- the three apps ------------------------------------------------------
if [ "$WANT_APPS" -eq 1 ]; then
  # controller (already cloned above) + slep + connect.
  for entry in "controller|$CTL_REPO|SYSIBLE_CONTROLLER_DIR" \
               "slep|$SLEP_REPO|SYSIBLE_SLEP_DIR" \
               "connect|$CONNECT_REPO|SYSIBLE_CONNECT_DIR"; do
    p="${entry%%|*}"; rest="${entry#*|}"; repo="${rest%%|*}"; var="${rest##*|}"
    [ "$p" = "controller" ] || clone_one "$repo"
    _dir="$SRC_DIR/$(dirname_for "$repo")"
    say
    say "============================================================"
    say " Bringing up: $p"
    say "============================================================"
    env "$var=$_dir" sysible_ctl "$p" up
  done
fi

# ---- the gateway (FROM THIS CHECKOUT) ------------------------------------
if [ "$WANT_GW" -eq 1 ]; then
  say
  say "============================================================"
  say " Bringing up: SLOP gateway (this repo: $HERE)"
  say "============================================================"
  env SYSIBLE_SLOP_DIR="$HERE" sysible_ctl slop up
fi

say
say "Done."
if [ "$WANT_GW" -eq 1 ]; then
  say "One front door is up: https://slop.lan/  (point slop.lan + the"
  say "controller./slep./connect. subdomains at this host — DNS or /etc/hosts;"
  say "set SLOP_DOMAIN + upstreams in this repo's .env to change the defaults)."
fi
say "Manage everything with:  sysible_ctl status   |   sysible_ctl update all"
