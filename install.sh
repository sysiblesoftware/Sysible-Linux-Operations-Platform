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
# Requirements: git and Docker Engine + the compose plugin — this installer sets
# up both automatically if they're missing (Docker via its official script, with
# a distro-package fallback). Nothing is re-hosted — every app is cloned from its
# own official repo and built from source.
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

# Best-effort package install across the common distro package managers. Returns
# non-zero if it can't find a supported one (callers decide how to handle that).
_pm_install() {  # _pm_install <pkg>...
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  elif command -v dnf >/dev/null 2>&1; then dnf install -y "$@"
  elif command -v yum >/dev/null 2>&1; then yum install -y "$@"
  elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install "$@"
  elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm "$@"
  elif command -v apk >/dev/null 2>&1; then apk add "$@"
  else return 1
  fi
}

# git is needed to clone the apps — install it if it's missing.
if ! command -v git >/dev/null 2>&1; then
  say "== git not found — installing it =="
  _pm_install git || true
  command -v git >/dev/null 2>&1 || die "git is required and could not be installed automatically. Install git and re-run."
fi

# Docker Engine + the compose plugin run everything. If Docker is absent, install
# it (we're already root here). IMPORTANT ORDERING: prefer the apt/distro path
# FIRST. Sysible ISOs already configure the official Docker repo (via
# sysible-release, keyring /usr/share/keyrings/docker.gpg), and Debian ships
# docker.io — so a plain apt install works. The get.docker.com convenience script
# is only a LAST resort, because it adds its OWN docker source with a different
# Signed-By keyring, which collides with the pre-configured one and breaks apt
# with "Conflicting values set for option Signed-By".
if ! command -v docker >/dev/null 2>&1; then
  say "== Docker not found — installing Docker Engine + the compose plugin =="
  # Heal a prior get.docker.com run: if it left a docker.list next to a
  # pre-configured docker source (docker.sources), remove the stray so apt can
  # read its lists again (the two disagree on the keyring).
  if [ -f /etc/apt/sources.list.d/docker.list ] && [ -f /etc/apt/sources.list.d/docker.sources ]; then
    say "  removing a conflicting docker.list left by a previous run"
    rm -f /etc/apt/sources.list.d/docker.list
  fi
  # 1) The official engine from an already-configured repo (Sysible ISO / manual).
  _pm_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null \
    || _pm_install docker.io docker-compose-v2 2>/dev/null \
    || _pm_install docker.io docker-compose 2>/dev/null || true
  # 2) Only if apt couldn't provide docker at all, the vendor convenience script —
  #    but that pipes remote code straight into a ROOT shell with no chance to
  #    inspect it, so it is NOT run automatically. Require an explicit operator
  #    opt-in (SYSIBLE_ALLOW_DOCKER_CONVENIENCE_SCRIPT=1); otherwise fall through to
  #    the die below, which prints the official manual-install URL.
  if ! command -v docker >/dev/null 2>&1; then
    if [ "${SYSIBLE_ALLOW_DOCKER_CONVENIENCE_SCRIPT:-0}" = 1 ]; then
      say "  no docker package via apt — running the vendor install script (opt-in via SYSIBLE_ALLOW_DOCKER_CONVENIENCE_SCRIPT=1)"
      if command -v curl >/dev/null 2>&1; then curl -fsSL https://get.docker.com | sh || true
      elif command -v wget >/dev/null 2>&1; then wget -qO- https://get.docker.com | sh || true
      else _pm_install curl >/dev/null 2>&1 && { curl -fsSL https://get.docker.com | sh || true; }
      fi
    else
      say "  no docker package available via apt. The get.docker.com convenience"
      say "  script (curl | sh as root) is NOT run automatically. Re-run with"
      say "  SYSIBLE_ALLOW_DOCKER_CONVENIENCE_SCRIPT=1 to allow it, or install Docker"
      say "  manually (https://docs.docker.com/engine/install/) and re-run."
    fi
  fi
  command -v docker >/dev/null 2>&1 || die "Docker could not be installed automatically. Install Docker Engine + the compose plugin (https://docs.docker.com/engine/install/) and re-run."
  # Enable + start the daemon so the rest of the install can use it immediately.
  systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
  say "  Docker installed: $(docker --version 2>/dev/null || echo present)"
fi

# Make sure the Compose v2 plugin is present (the apps + gateway are compose stacks).
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  say "== installing the Docker Compose plugin =="
  _pm_install docker-compose-plugin 2>/dev/null || _pm_install docker-compose 2>/dev/null || true
  docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 \
    || die "the Docker Compose plugin is required and could not be installed automatically. Install it (https://docs.docker.com/compose/install/) and re-run."
fi

# Let the human who ran this (via sudo) drive Docker — and therefore sysible_ctl —
# WITHOUT sudo going forward, the standard Docker post-install step. The
# membership only takes effect on their next login; until then, sysible_ctl
# transparently re-runs itself with sudo when it hits the root-owned socket.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  getent group docker >/dev/null 2>&1 || groupadd docker 2>/dev/null || true
  if id -nG "$SUDO_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then :; else
    usermod -aG docker "$SUDO_USER" 2>/dev/null \
      && say "Added '$SUDO_USER' to the docker group — log out/in (or run 'newgrp docker') to use docker/sysible_ctl without sudo."
  fi
fi

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
  # Create the secret store 0600 from the start — independent of the caller's
  # umask — so the SSO shared secret is never even briefly world-readable.
  if [ ! -f "$ENV_FILE" ]; then
    ( umask 077; touch "$ENV_FILE" )
  fi
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  if grep -q "^$1=" "$ENV_FILE" 2>/dev/null; then
    # Rewrite through a PRIVATE temp file, never `sed -i.bak`: an .env.bak is
    # created 0644 and would leak the secret to any local reader before removal.
    # The temp is created 0600, then atomically renamed over the original.
    _tmp="$ENV_FILE.tmp.$$"
    ( umask 077; : > "$_tmp" )
    if sed "s|^$1=.*|$1=$2|" "$ENV_FILE" > "$_tmp"; then
      mv "$_tmp" "$ENV_FILE"
    else
      rm -f "$_tmp"
    fi
    chmod 600 "$ENV_FILE" 2>/dev/null || true
  else
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
  fi
}

# _upsert_kv FILE KEY VALUE — set KEY=VALUE in an arbitrary env file (0600),
# replace-or-append, same private-temp handling as _upsert_env. Used to PERSIST
# the SSO secret + trust flag into each app's compose-directory .env.
_upsert_kv() {
  _f="$1"; _k="$2"; _v="$3"
  if [ ! -f "$_f" ]; then ( umask 077; touch "$_f" ); fi
  chmod 600 "$_f" 2>/dev/null || true
  if grep -q "^$_k=" "$_f" 2>/dev/null; then
    _t="$_f.tmp.$$"; ( umask 077; : > "$_t" )
    if sed "s|^$_k=.*|$_k=$_v|" "$_f" > "$_t"; then mv "$_t" "$_f"; else rm -f "$_t"; fi
    chmod 600 "$_f" 2>/dev/null || true
  else
    printf '%s=%s\n' "$_k" "$_v" >> "$_f"
  fi
}

# _app_compose_dir DIR — echo the directory that holds the app's compose file
# (its own root, or a deploy/ subdir), matching how sysible_ctl finds it. That is
# the directory docker compose loads .env from, so it's where the SSO env must live.
_app_compose_dir() {
  for _d in "$1" "$1/deploy"; do
    for _f in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
      [ -f "$_d/$_f" ] && { printf '%s\n' "$_d"; return 0; }
    done
  done
  return 1
}
# NOTE: must ALWAYS return 0. Under dash (Debian's /bin/sh) with `set -e`, a
# `VAR="${X:-$(fn)}"` assignment whose command substitution returns non-zero aborts
# the whole script — silently. On a first run there is no .env yet, so a function
# that returned non-zero here killed install.sh right after installing the CLI,
# before it ever cloned the apps. Return 0 explicitly (and belt-and-suspenders the
# call site with `|| true`).
_secret_from_env() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n 's/^SYSIBLE_SSO_SHARED_SECRET=\(..*\)$/\1/p' "$ENV_FILE" | tail -n1
}
SSO_SECRET="${SYSIBLE_SSO_SHARED_SECRET:-$(_secret_from_env || true)}"
if [ -z "$SSO_SECRET" ]; then
  SSO_SECRET="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  _upsert_env SYSIBLE_SSO_SHARED_SECRET "$SSO_SECRET"
  say "Generated a unified-SSO shared secret into $ENV_FILE"
fi
export SYSIBLE_SSO_SHARED_SECRET="$SSO_SECRET"

# ---- the three apps (best-effort: one failing never stops the rest) ------
FAILED=""
if [ "$WANT_APPS" -eq 1 ]; then
  # The host's LAN IP — where the Controller publishes its backend/agent API on :9000.
  # Same detection sysible_ctl uses when it seeds the Controller's advertised address.
  # Connect (all-in-one SLOP host) auto-attaches to the local Controller at this address
  # over SSO, so the operator never has to "log in to the Controller" from Connect.
  HOST_ADDR="${SYSIBLE_CONTROLLER_ADDR:-}"
  [ -n "$HOST_ADDR" ] || HOST_ADDR="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
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
    # PERSIST the SSO secret + trust flag + base path into the app's OWN compose
    # .env, so they survive EVERY future recreate. Without this the values only
    # existed in the ambient env of the single install-time `up` below; any later
    # `docker compose up -d` / `sysible_ctl up`/`update`/rebuild brought the app up
    # with an empty secret and trust=0 (docker resolves ${VAR:-default}), so it
    # failed closed and fell back to its OWN login — the SSO "stopped working after
    # a redeploy" bug. docker compose auto-loads this .env from the compose dir.
    # Connect auto-attaches to the LOCAL Controller over SSO — hand it that URL
    # (host LAN IP + the Controller's published :9000). No machine API key needed:
    # Connect authenticates to the Controller with the shared secret. Persisted into
    # the app's .env (which docker compose auto-loads), so it survives every recreate.
    if _cdir="$(_app_compose_dir "$_dir")"; then
      _aenv="$_cdir/.env"
      _upsert_kv "$_aenv" SYSIBLE_SSO_SHARED_SECRET "$SSO_SECRET"
      _upsert_kv "$_aenv" "$trust" 1
      _upsert_kv "$_aenv" SYSIBLE_BASE_PATH "/$p/"
      if [ "$p" = "connect" ] && [ -n "$HOST_ADDR" ]; then
        _upsert_kv "$_aenv" SYSIBLE_CONNECT_CONTROLLER_URL "https://$HOST_ADDR:9000"
      fi
    fi
    # Pass the app dir, turn its SSO trust flag on, hand it the shared secret, and
    # build its front end under the gateway path prefix (/controller/ etc.) so its
    # assets + API calls resolve on the one shared origin (see gateway/Caddyfile).
    if env "$var=$_dir" "$trust=1" SYSIBLE_SSO_SHARED_SECRET="$SSO_SECRET" \
           SYSIBLE_BASE_PATH="/$p/" sysible_ctl "$p" up; then
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

if [ -n "$FAILED" ]; then
  say
  say "Finished WITH PROBLEMS — these did not come up:$FAILED"
  say "Inspect with 'sysible_ctl status'; the errors above are usually network, DNS, or a Docker build."
fi
