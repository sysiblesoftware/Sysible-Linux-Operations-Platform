# SLOP architecture

SLOP is a **gateway + portal**, deliberately thin. It does not replace or fork the
apps; it puts a single, branded, TLS-terminated front door in front of them.

## Routing model — subdomains

The apex domain serves the **portal**; each app gets a **subdomain** that reverse-
proxies to the app on its host port:

| URL | → upstream (default) |
|---|---|
| `https://$SLOP_DOMAIN/` | the SLOP portal (static, served by Caddy) |
| `https://controller.$SLOP_DOMAIN/` | `$SLOP_CONTROLLER_UPSTREAM` (`:8800`) |
| `https://slep.$SLOP_DOMAIN/` | `$SLOP_SLEP_UPSTREAM` (`:8810`) |
| `https://connect.$SLOP_DOMAIN/` | `$SLOP_CONNECT_UPSTREAM` (`:8700`) |

**Why subdomains, not paths.** All three consoles are single-page apps that assume
they live at the web root (absolute `/assets/...`, cookie paths, a terminal
websocket). Serving each at its own subdomain root means the apps need **zero
changes** — no base-path rebuild, no cookie-path rewriting, no websocket path
juggling. The cost is DNS: point the apex and the three subdomains at this host
(wildcard `*.$SLOP_DOMAIN`, or per-name entries, or `/etc/hosts` on each client).

## TLS

Caddy owns TLS for every site. Three options:

1. **Internal CA (default).** `tls internal` — Caddy mints certs from its own CA.
   Self-signed like the apps today; trust `caddy`'s root (exportable from the
   `slop-caddy-data` volume, `/data/caddy/pki/authorities/local/root.crt`) on
   clients, or click through the warning.
2. **Public ACME.** Give `$SLOP_DOMAIN` a real public name with ports 80/443
   reachable, drop the `tls internal` lines, set an ACME email in the global
   block — Caddy fetches Let's Encrypt certs automatically.
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
- **Gateway**: this repo, `docker compose up -d`. It could later become a
  `sysible_ctl slop` product so `sysible_ctl slop up` brings up the door too.

## Roadmap

1. **Now** — gateway + portal + health + one TLS front door (this scaffold).
2. **SSO** — gateway `forward_auth` → Controller auth-check (the Controller's
   `/api/auth/verify` probe is shipped in CE and EE; enable `import sso` to gate),
   then apps accept the forwarded identity. See [SSO.md](SSO.md).
3. **`sysible_ctl slop`** — manage the gateway with the same CLI as the apps.
4. **EE SLOP** — same gateway/portal in front of the Enterprise builds, with real
   single sign-on via the Controller's SSO/MFA.
