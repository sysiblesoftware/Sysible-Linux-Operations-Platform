# apps/ — the Controller, SLEP and Connect, in this tree

These three directories hold the source of the three apps the platform serves.
They used to be three separate git checkouts on the host, cloned by `install.sh`
and updated one at a time; they are part of this repository now.

**Edit them here.** This repository is the source of truth. The standalone
repositories listed in `VENDORED.json` are publish targets — you push a finished
change out to one with `tools/publish-app`, you do not pull from it.

```
tools/publish-app --status            # has anything drifted?
tools/publish-app controller          # push apps/controller to its repo
tools/publish-app --all --dry-run     # see what would go, change nothing
```

## Why

Three checkouts meant three of everything: three `git pull`s to get one
consistent state, three `.env` files holding copies of the same shared secret,
three compose projects to bring up in the right order, and a window after any
one of them updated in which the platform was running two halves of a contract
that had changed. `install.sh` had to clone three repositories over a network
that might only half-work, and "could not clone slep — skipping it" left a host
that looked installed and was not.

One tree builds one platform at one commit.

## What this changed about the build

Each app's URL prefix is now written next to the service that needs it:

| app        | prefix        |
|------------|---------------|
| controller | *(empty)*     |
| slep       | `/slep/`      |
| connect    | `/connect/`   |

That looks like a detail and is the single most expensive bug this layout
removes. The prefix is a **build argument** — it is baked into the console's
JavaScript bundle — and all three apps read it from one variable,
`SYSIBLE_BASE_PATH`, out of whichever `.env` sat beside their compose file. One
name, one value, three different correct answers. A build that picked up the
wrong one, or no value at all, produced a console that served a page and then
asked for its assets at a path the gateway does not route: a blank screen, with
every container reporting healthy. It happened more than once, and the last time
it was fixed by writing the value into three separate `.env` files, which is a
fix that survives exactly until someone rebuilds without them.

Here the three values are literals in one compose file, beside the three
services they belong to. There is no variable left to get wrong.

## What is NOT in here

Each app keeps its own tests, its own `README`, and its own docs. Run them where
they live:

```
cd apps/controller && pytest tests/
cd apps/slep       && pytest tests/
cd apps/connect    && pytest tests/
```
