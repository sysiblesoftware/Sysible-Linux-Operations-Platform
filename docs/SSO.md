# Single sign-on — SLOP is the identity provider (CE)

Goal: sign in **once** and reach all three apps, with one set of accounts and one
place to manage passwords. In CE, **SLOP itself owns identity** — it ships a small
identity provider (IdP) with its own user store, the login page, and the account /
password-reset UI for every app. The gateway is the enforcement point; the three
apps trust the identity SLOP asserts and no longer show their own login.

This is implemented and shipped in CE. (EE swaps the IdP for enterprise SSO — see
the EE note at the end.)

## The pieces

```
  browser ──► Caddy gateway ──► app (Controller / SLEP / Connect)
                 │  forward_auth
                 ▼
             SLOP IdP  (idp/ service)  ── the one user store + login + resets
```

- **IdP** (`idp/`, a small FastAPI service): the user store (SQLite), `POST /login`,
  self-service `/account` (change your password), and superuser `/admin` (create /
  delete users, reset anyone's password, set roles). It issues a session cookie
  `sysible_sso` scoped to the **parent** domain (`.slop.lan`), so one login is
  visible to the apex **and** every app subdomain — that shared cookie is what
  makes it single sign-on rather than three logins.
- **Gateway** (`gateway/Caddyfile`): on every proxied request it asks the IdP
  "is this browser signed in?" (`forward_auth` → `GET /auth/verify`). A 2xx lets
  it through; a 401/403 redirects the browser to `/login?next=…`.

## The trust boundary (why a client can't forge identity)

On a successful `/auth/verify`, the gateway does three things before proxying to
the app:

1. **Strips** any client-supplied `X-Sysible-User` / `X-Sysible-Role` /
   `X-Sysible-Auth` — a browser must never be able to assert its own identity.
2. **Injects** the real identity from the IdP: `X-Sysible-User`, `X-Sysible-Role`
   (one of `superuser` / `operator` / `auditor`).
3. **Stamps** a shared secret `X-Sysible-Auth: $SYSIBLE_SSO_SHARED_SECRET`, proving
   the request came through the gateway.

Each app honors the asserted identity **only** when its trust flag is on **and**
`X-Sysible-Auth` matches its configured `SYSIBLE_SSO_SHARED_SECRET`. A client
hitting an app directly can't know the secret, so it can't spoof the headers; if
the secret is unset the apps **fail closed** (ignore the headers). `install.sh`
generates one strong secret and wires it into the gateway and all three apps.

## Per-app trust mode (all default OFF → standalone apps are unchanged)

| App | Trust flag | Role mapping | Notes |
|-----|-----------|--------------|-------|
| Controller | `SYSIBLE_WEBGUI_TRUST_SSO=1` | superuser→superuser, operator→sysadmin, auditor→auditor | The BFF provisions a backend account+token for the asserted user via the root-only API key (`POST /admin/sso-provision`); SLOP is authoritative for the account's role. |
| SLEP | `SLEP_TRUST_GATEWAY_AUTH=1` | superuser→superuser, operator→operator, auditor→viewer | Honored in `_session_or_401` + the read-only middleware; the BFF strips client `X-Sysible-*`. |
| Connect | `SYSIBLE_CONNECT_TRUST_GATEWAY_AUTH=1` | (no roles) any authenticated user is a full user | Applied on HTTP **and** the terminal websocket handshake. |

All three also read `SYSIBLE_SSO_SHARED_SECRET`. Turn a flag off (the default) and
the app keeps its own native login for direct, non-gateway use.

## Accounts & password resets

Because the apps trust SLOP, there is effectively **one credential**. Manage it in
the portal:

- **Your password:** `https://$SLOP_DOMAIN/account`.
- **Everyone's accounts + resets (superuser):** `https://$SLOP_DOMAIN/admin` — add
  or remove users, set roles, and reset any user's password (which forces a change
  at their next login and drops their live sessions immediately).

First run creates an initial `admin` superuser; set `SLOP_ADMIN_PASSWORD` or read
the one-time generated password from the IdP logs (`docker logs sysible-slop-idp`).

## CE limitations (hardened in EE)

- SLEP's per-org RBAC still keys off org membership: a gateway `superuser` bypasses
  org checks, but an `operator`/`auditor` who isn't a member of an org will hit the
  normal per-org checks for org-scoped features.
- The shared secret is a symmetric bearer between the gateway and the apps (not a
  signed, per-request assertion), and the gateway↔app hop is plain reverse-proxy.

## EE note

EE SLOP keeps this exact gateway seam but replaces the CE IdP with enterprise
identity: OIDC/SAML federation + MFA, signed per-request assertions carrying group
claims, fine-grained per-app RBAC, and mTLS on the gateway↔app hop. The apps' trust
mode is the same hook; only the assertion's issuer and strength change.
