# SLOP QA plan

Written to be executed, not read. Every claim it makes about the system has to
end in a test that fails when the claim stops being true — a finding with no
test is a note that rots, and "I checked it once" is not a guarantee anybody can
rely on six commits later.

## What is actually here

Measured, not remembered:

| Component | Python files | Test files | HTTP routes |
|---|---|---|---|
| idp | 21 | 17 | 37 |
| gateway | 3 | 3 | — (Caddyfile) |
| updater | 24 | 14 | 9 |
| baseline | 32 | 19 | 22 |
| visualizer | 15 | 8 | 8 |
| flashback | 26 | 16 | 40 |
| apps/controller | 265 | 187 | 257 |
| apps/slep | 94 | 70 | 157 |
| apps/connect | 30 | 17 | 23 |
| root (`tests/`) | — | 25 | — |

**553 HTTP routes, 376 test files.** Six apps are mounted behind the gateway:
`/controller`, `/slep`, `/connect`, `/flashback`, `/baseline`, `/visualizer`.

## The shape of the trust model

One origin. Caddy terminates TLS, serves the portal, and for every app route:

1. strips any client-supplied `X-Sysible-User`, `-Role`, `-Auth`, `-App`
2. asks the IdP `/auth/verify` (forward_auth), which answers 204 + identity
3. stamps the per-app shared secret `X-Sysible-Auth`
4. strips the mount prefix and proxies to the app

So each app's authentication is: *trust `X-Sysible-User`/`-Role` only when
`X-Sysible-Auth` matches my secret.* Everything below follows from that.

## Passes

Ordered by what a failure would cost, not by convenience.

### Pass 0 — baseline
Run every suite and record the numbers. Anything red before I touch it is
finding number one, not a thing to work around.

### Pass 1 — the trust boundary
The highest-cost failures live here.

- Can a request reach an app **without** the gateway and be believed? Every app
  that reads `X-Sysible-User` must refuse it without a valid `X-Sysible-Auth`.
- Is the secret per app or shared? Baseline has its own
  (`SYSIBLE_BASELINE_GATEWAY_SECRET`) and the others share one. A single shared
  secret means a flaw in any one app is one hop from forging identity in all of
  them — the Caddyfile already says so; check the code agrees.
- Spoofing: client-supplied identity headers must be stripped on **every**
  route, including the portal and any route added since.
- A fallback identity (`LOCAL_USER`/`LOCAL_ROLE`) must never be privileged by
  default, and must never apply when the gateway is in front.

### Pass 2 — route census
Mechanical and complete: enumerate all 553 routes and resolve each one's guard.
Flag every **mutating** route (POST/PUT/PATCH/DELETE/websocket) that has no
authentication dependency. This is the sweep that finds the one route somebody
added without the decorator.

### Pass 3 — injection and traversal
Where the platform shells out or touches paths with values that came from a
request:

- `virsh -c <uri>`, `ssh`, `git`, `docker`, `terraform`, `ansible`
- SLEP's IDE reads and writes project files — path traversal is the obvious
  risk and must be proven closed, not assumed
- anything interpolated into SQL, shell, HCL or YAML

### Pass 4 — the dependency graph
What talks to what, and what must not. Assert the **forbidden** edges, not just
the permitted ones: an app that gains a dependency it should not have is the
failure nobody notices.

### Pass 5 — reachability
An endpoint nothing calls does not exist; a button with no endpoint is worse.
Both directions, for every app — SLEP has already shipped one unreachable
feature this way.

### Pass 6 — improvement
Where something works but is worse than it needs to be, fix it. Only with a
test that would have failed before.

### Pass 7 — regression
Every suite, every time. A fix that breaks something else is not a fix.

## Rules for this sweep

1. **Prove it.** A finding needs a reproduction. "This looks wrong" is a lead,
   not a result.
2. **Every fix gets a test that fails without it.** Verified by reverting the
   fix and watching the test fail — not by assuming.
3. **Source presence is not behaviour.** The repeated near-miss in this codebase
   is a test asserting a symbol exists rather than that it is reached. Assert
   the behaviour.
4. **No guessing.** Where the answer depends on a library's or tool's real
   behaviour, run the tool.
