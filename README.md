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
- **Apps unchanged** — each still runs as its own container on its own port; SLOP
  reverse-proxies to them. You can still hit an app directly if you want.
- **An SSO seam** — the gateway has a `forward_auth` hook staged for
  **Controller-as-IdP** single sign-on (see [docs/SSO.md](docs/SSO.md)).

## Quick start

One command from this repo stands up the **whole stack** — Controller, SLEP and
Connect as containers, with the SLOP gateway in front of them:

```sh
# 1. Optional: set your domain + upstreams (defaults: slop.lan, apps on this host)
cp .env.example .env

# 2. Install everything (needs git + Docker; installs sysible_ctl to manage it all):
sudo ./install.sh                 # the whole stack (apps + gateway)
#   sudo ./install.sh apps        # only Controller + SLEP + Connect
#   sudo ./install.sh gateway     # only the gateway (apps already running)

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
| `install.sh` | One-command installer: the three apps + this gateway (`sudo ./install.sh`). |
| `docker-compose.yml` | The gateway (Caddy) + portal. |
| `gateway/Caddyfile` | Reverse-proxy: portal at the apex, `controller./slep./connect.` subdomains to the apps, TLS, health proxying, and the SSO `forward_auth` seam. |
| `portal/` | The branded landing page (app cards + live health + light/dark). |
| `.env.example` | `SLOP_DOMAIN` and the three upstream `host:port`s. |
| `docs/ARCHITECTURE.md` | Design, routing, TLS options. |
| `docs/SSO.md` | The Controller-as-IdP single-sign-on plan (phased). |

## Editions

- **CE SLOP** (this repo) fronts the CE apps.
- **EE SLOP** will front the Enterprise builds (PostgreSQL Controller, SSO/MFA,
  HA) with the same gateway/portal and real single sign-on. The gateway config is
  edition-agnostic — only the upstreams and the auth backend differ.
