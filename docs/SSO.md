# Single sign-on — Controller as IdP

Goal: sign in **once** and reach all three apps. The Controller already owns
identity (admin accounts, roles, session cookies, admin tokens, and — in EE —
SSO/MFA/SCIM), so it's the natural identity provider. SLOP's gateway is the
enforcement point.

This lands in phases so each step is shippable on its own.

## Phase 1 — portal + separate logins (shipped)

The portal links to each app; each app keeps its own login. No shared session yet.
Useful immediately: one URL, one TLS front door, live health. Nothing in the apps
changes.

## Phase 2 — gateway gating (`forward_auth`)

The gateway asks the Controller "is this request signed in?" before proxying to an
app. In `gateway/Caddyfile` the `(sso)` snippet is staged; enable it by
uncommenting `import sso` in the `controller./slep./connect.` sites.

```
forward_auth <controller> {
    uri /api/auth/verify
    copy_headers X-Sysible-User X-Sysible-Role
    # 401/403 → redirect to the portal/Controller login
}
```

**What the Controller must add:** a lightweight `GET /api/auth/verify` that reads
the caller's Controller session cookie (or admin token) and returns:

- `200` with headers `X-Sysible-User: <name>`, `X-Sysible-Role: <role>` when signed in;
- `401` otherwise.

It performs no action and mutates nothing — it's a pure auth probe, cheap enough to
run on every request. Cookies scope to `.$SLOP_DOMAIN` so the one Controller
session is visible to the gateway on every subdomain.

At the end of Phase 2 the gateway blocks unauthenticated access to all three apps
behind a single Controller login — even though each app still shows its own login
screen once it's reached.

## Phase 3 — apps trust the forwarded identity (true SSO)

For a genuine single sign-on (no second login at the app), each app accepts the
gateway-forwarded identity instead of its own login **when reached through the
gateway**:

- Trust `X-Sysible-User` / `X-Sysible-Role` **only** from the gateway (bind the app
  to loopback / the gateway's network, or share a signed header secret so a client
  can't spoof it).
- Map the Controller role to the app's own authorization (e.g. auditor → read-only).
- Keep the app's native login working for **direct** (non-gateway) access, so an
  app is still usable standalone.

Connect already has a run-as/attribution model tied to the Controller admin token,
so it's the closest to this today; SLEP and the Controller console need a
"trusted-header auth" mode added.

## EE note

EE SLOP reuses this exact seam. The difference is the IdP: the EE Controller does
OIDC/SAML SSO + MFA, so `/api/auth/verify` is backed by the enterprise session, and
Phase 3's role mapping carries SSO group claims through to each app.
