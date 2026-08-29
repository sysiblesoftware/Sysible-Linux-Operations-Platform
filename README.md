# Sysible Linux Operations Platform (SLOP)

**One front door for the Sysible suite.** SLOP unifies the three Sysible apps —
**Controller** (fleet management), the **Engineering Platform / SLEP** (infra
automation), and **Connect** (browser terminals) — behind a single domain, a
single TLS story, and a branded portal, without changing the apps themselves.

It's the **CE SLOP** download: instead of standing up and bookmarking three
separate consoles on three ports, you run the apps and put SLOP in front.

```
                      ┌─────────────────────────────┐
   https://slop.lan → │   SLOP gateway (Caddy) +     │
                      │   portal landing / nav       │
                      └──────────────┬──────────────┘
        controller.slop.lan  slep.slop.lan  connect.slop.lan
                 │                │                │
          Controller :8800   SLEP :8810     Connect :8700   (unchanged)
```

## What it gives you

- **One URL + one portal** — a branded landing page with a card per app and a
  **live health dot** for each, so you see at a glance what's up.
- **One TLS front door** — Caddy terminates HTTPS for the apex and all three app
  subdomains (internal CA by default, or bring real certs).
- **Real unified single sign-on** — SLOP ships its own identity provider: you sign
  in **once** at the portal and reach all three apps with no second login. SLOP
  owns the accounts, roles, and **password resets for all three** (self-service at
  `/account`, superuser management at `/admin`). The gateway proves your identity
  to each app with a shared secret, so a client can't forge it (see
  [docs/SSO.md](docs/SSO.md)).
- **Apps still run as themselves** — each is its own container on its own port; SLOP
  reverse-proxies to them, and each keeps its own native login for direct,
  non-gateway access (SSO trust is on only behind the gateway).

## Requirements

SLOP runs everything in containers, so the host needs very little — and
`install.sh` bootstraps the two tools it depends on if they're missing:

**Host (to run SLOP):**
- **Docker Engine** + the **Compose v2** plugin — runs the gateway, the IdP, and
  (via the installer) the three apps. **`install.sh` installs Docker for you** if
  it's absent (Docker's official convenience script, with a distro-package
  fallback), then enables and starts the daemon.
- **git** — `install.sh` clones each app from its own official repo, and installs
  git too if it's missing.
- **root / sudo** — to install Docker, bind ports 80/443, and drive the daemon
  (the installer re-execs itself with `sudo` if you don't start as root).
- **Ports 80 and 443** free on the host (the single front door).
- **Outbound network** the first time only — to clone the app repos and pull the
  base images (`caddy:2-alpine`, `python:3.12-slim`, and each app's build deps).
- **DNS or `/etc/hosts`** entries for `slop.lan` and the `controller.`/`slep.`/
  `connect.` subdomains pointing at this host.

That's it — no Python, Node, or database on the host. Each piece pins its own deps:

- **IdP** (`idp/`) — Python, in `idp/requirements.txt` (FastAPI + uvicorn +
  python-multipart; everything else is stdlib). Installed **inside** its container.
- **Gateway** — the stock `caddy:2-alpine` image; config is `gateway/Caddyfile`.
- **The three apps** — each brings its own dependencies in its own repo; the SLOP
  installer just clones and builds them.

## Quick start

One command from this repo stands up the **whole stack** — Controller, SLEP and
Connect as containers, with the SLOP gateway in front of them:

```sh
# 1. Optional: set your domain + upstreams (defaults: slop.lan, apps on this host)
cp .env.example .env

# 2. Install everything (installs Docker + git if missing, plus sysible_ctl):
sudo ./install.sh                 # the whole stack (apps + gateway + IdP)
#   sudo ./install.sh apps        # only Controller + SLEP + Connect
#   sudo ./install.sh gateway     # only the gateway + IdP (apps already running)
#   sudo ./deploy/sysible_ctl install   # same thing, if you reach for sysible_ctl

# 3. Resolve the domain + subdomains to this host (DNS, or every client's hosts):
#    192.168.8.10  slop.lan controller.slop.lan slep.slop.lan connect.slop.lan

# 4. Open the portal:
open https://slop.lan/
```

`install.sh` clones each app from its own official repo, builds it, and manages
everything through the unified `sysible_ctl` (`sysible_ctl status | update all |
logs …`). Already have the apps running and only want the front door? Use
`sudo ./install.sh gateway` — or `docker compose up -d --build` for just this
gateway container.

TLS is Caddy's **internal CA** by default (self-signed, like the apps today) — your
browser warns once; trust the CA or proceed. For public certs, see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Layout

| Path | What |
|---|---|
| `install.sh` | One-command installer: the three apps + this gateway + IdP (`sudo ./install.sh`). Generates the SSO shared secret into `.env`. |
| `docker-compose.yml` | The front door: the gateway (Caddy) + the IdP service. |
| `idp/` | The identity provider — the user store, login, `/account`, and `/admin` (accounts + password resets). Python deps in `idp/requirements.txt`; tests in `idp/tests/`. |
| `gateway/Caddyfile` | Reverse-proxy: portal at the apex, `controller./slep./connect.` subdomains to the apps, TLS, health proxying, and the SSO `forward_auth` + shared-secret enforcement. |
| `portal/` | The branded landing page (app cards + live health + signed-in user chip + light/dark). |
| `.env.example` | `SLOP_DOMAIN`, `SYSIBLE_SSO_SHARED_SECRET`, the first-run admin, and the three upstream `host:port`s. |
| `docs/ARCHITECTURE.md` | Design, routing, TLS options. |
| `docs/SSO.md` | The shipped single-sign-on architecture (SLOP as the identity provider). |

## Editions

- **CE SLOP** (this repo) fronts the CE apps.
- **EE SLOP** will front the Enterprise builds (PostgreSQL Controller, SSO/MFA,
  HA) with the same gateway/portal and real single sign-on. The gateway config is
  edition-agnostic — only the upstreams and the auth backend differ.
