# SLOP architecture

SLOP is a **gateway + portal**, deliberately thin. It does not replace or fork the
apps; it puts a single, branded, TLS-terminated front door in front of them.

## Routing model — one origin, addressed by path

There is **no domain and nothing to configure**. SLOP answers on 443 for whatever
IP this host has; everything lives on that ONE origin, addressed by **path**. The
root serves the **portal**; each app is mounted under a path prefix that the
gateway strips before proxying to the app on its host port:

| URL | → upstream (default) |
|---|---|
| `https://<server-ip>/` | the SLOP portal (static, served by Caddy) |
| `https://<server-ip>/controller/` | `$SLOP_CONTROLLER_UPSTREAM` (`:8800`) |
| `https://<server-ip>/slep/` | `$SLOP_SLEP_UPSTREAM` (`:8810`) |
| `https://<server-ip>/connect/` | `$SLOP_CONNECT_UPSTREAM` (`:8700`) |

**Why paths, not subdomains.** One origin means one login cookie and zero DNS: no
apex, no wildcard, no `/etc/hosts`, and it just works on a raw IP for anyone on any
machine. Each app is built with its prefix as its front-end base path, so the
browser requests `/controller/assets/...`; the gateway strips the prefix and the
app sees its own root paths (`/assets/...`, cookies, websockets) unchanged.

## TLS

Caddy owns TLS for every site. Three options:

1. **Internal CA (default).** Caddy mints ONE self-signed cert under a fixed
   internal name (the cert-holder site in the Caddyfile) and serves it for every
   raw-IP / no-SNI request via `default_sni` — so TLS works without knowing the IP
   ahead of time. Trust `caddy`'s root (exportable from the `slop-caddy-data`
   volume, `/data/caddy/pki/authorities/local/root.crt`) on clients, or click
   through the one-time warning.
2. **Public ACME.** Point external DNS at this IP, give the `:443` site a real
   public name, set an ACME email in the global block — Caddy fetches Let's Encrypt
   certs automatically. Nothing else in the config needs to know the address.
3. **Bring your own.** Mount a cert/key and use `tls /path/cert.pem /path/key.pem`.

The apps keep serving their own self-signed HTTPS internally; the gateway proxies
to them over HTTPS with `tls_insecure_skip_verify` (they're reached on loopback /
the host, and their certs are self-signed). Set `SYSIBLE_*_TLS=0` on an app to make
it plain-HTTP behind the gateway instead — then drop `https://` from its upstream.

## Upstreams

Defaults assume the apps publish their ports on **this host**, so the gateway
reaches them at `host.docker.internal:<port>` (the compose file adds the
`host-gateway` mapping Linux needs). If the apps run on a shared docker network or
another host, point `SLOP_*_UPSTREAM` at them and drop the `extra_hosts` mapping.

## Health

The portal shows a live dot per app. To avoid CORS and cross-subdomain reachability
issues, the portal calls **same-origin** paths (`/healthz/controller`, `/slep`,
`/connect`) and the gateway proxies each to that app's own health endpoint
(`/api/health` for Controller/SLEP, `/healthz` for Connect).

## How it fits the suite

- **Apps**: installed/managed by `install-sysible` and `sysible_ctl` (separate
  container stacks). SLOP does not manage their lifecycle — it only fronts them.
- **Gateway**: this repo. Managed like the apps by `sysible_ctl slop` — point it
  at this checkout and bring the door up with the same CLI:
  `SYSIBLE_SLOP_DIR=/path/to/this/repo sysible_ctl slop up` (or a plain
  `docker compose up -d` from here). `up`/`update`/`status`/`logs`/`restart`/
  `stop` all work; SLOP still does not manage the apps' lifecycle, it only fronts them.

## Roadmap

1. **Now** — gateway + portal + health + one TLS front door (this scaffold).
2. **SSO** — gateway `forward_auth` → Controller auth-check (the Controller's
   `/api/auth/verify` probe is shipped in CE and EE; enable `import sso` to gate),
   then apps accept the forwarded identity. See [SSO.md](SSO.md).
3. **`sysible_ctl slop`** — manage the gateway with the same CLI as the apps. ✅
4. **EE SLOP** — same gateway/portal in front of the Enterprise builds, with real
   single sign-on via the Controller's SSO/MFA.
