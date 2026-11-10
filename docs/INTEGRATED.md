# The one-checkout platform

The Controller, SLEP and Connect are in this repository, under `apps/`. One
checkout builds the whole platform at one commit.

They are behind a compose **profile**, so nothing changes until you ask for it.
A plain `docker compose up -d` in this directory is exactly what it always was.

## A fresh host

```sh
sudo ./install.sh --integrated
```

That is the whole thing. It does not clone anything: the three apps are services
of this compose project, built from `apps/`.

## An existing host

A host that already runs the three apps has their container names and their
ports taken. Two stacks competing for `sysible-controller` is not something to
discover part-way through an update, so the switch is deliberate and it is not
reversible without doing it again in the other direction. Read all of this
first.

### 1. Find out what your data is actually called

**Do not skip this and do not take the names from this document.** Compose
namespaces a volume by project, and the project name depends on which directory
each stack was first brought up from — which is not the same on every host. Ask
the running containers:

```sh
for c in sysible-controller sysible-slep sysible-connect; do
  printf '%s: ' "$c"
  docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$c" 2>/dev/null || echo '(not running)'
done
```

Write down what comes back. Those volumes hold the Controller's enrollment
database and certificates, SLEP's projects and run logs, and Connect's host
inventory and encrypted Controller link. Nothing below destroys them, but
nothing below carries them across either — the integrated stack creates volumes
of its own, under this project's name, and they start empty.

### 2. Back up first

Really. The Controller's volume is the one with a fleet in it.

```sh
sysiblectl flashback backup          # if Flashback is running
# and/or, per volume, from the names step 1 gave you:
docker run --rm -v <volume>:/from -v "$PWD":/to alpine \
  tar czf /to/<volume>.tgz -C /from .
```

### 3. Stop the old stacks

```sh
sysiblectl controller stop
sysiblectl slep stop
sysiblectl connect stop
docker rm -f sysible-controller sysible-slep sysible-connect
```

### 4. Copy the data across

Bring the platform up once so the new volumes exist, then copy into them:

```sh
sudo ./install.sh --integrated
docker compose --profile integrated stop controller slep connect

NEW_PROJECT="$(basename "$PWD")"
for pair in "<old-controller-volume>:${NEW_PROJECT}_controller-data" \
            "<old-slep-volume>:${NEW_PROJECT}_slep-data" \
            "<old-connect-volume>:${NEW_PROJECT}_connect-data"; do
  old="${pair%%:*}"; new="${pair##*:}"
  docker run --rm -v "$old":/from -v "$new":/to alpine \
    sh -c 'cd /from && cp -a . /to/'
done

docker compose --profile integrated start controller slep connect
```

### 5. Check, then clean up

Open the portal. Every tile should be green, the Controller should list its
fleet, and Administration → Software & services should show one updatable row
with the other three reading **Built with the platform**.

Only then:

```sh
sudo rm -rf /opt/sysible-src/sysible-controller \
            /opt/sysible-src/sysible-linux-engineering-platform \
            /opt/sysible-src/sysible-connect
```

Leaving them costs nothing but a little disk: while a product has a checkout of
its own, the platform treats that checkout as the thing in charge and leaves it
its own row and its own update button. That is deliberate — it is what makes a
half-finished migration survivable rather than a host with two Controllers
disagreeing about who owns port 9000.

## What changed, concretely

**Each app's URL prefix is a literal beside its own service.** It is a *build*
argument, baked into the console's JavaScript bundle, and all three apps read it
from one variable name — `SYSIBLE_BASE_PATH` — out of whichever `.env` sat next
to their compose file. One name, three different correct answers:

| app        | prefix      |
|------------|-------------|
| controller | *(empty)*   |
| slep       | `/slep/`    |
| connect    | `/connect/` |

A build that picked up the wrong one, or no value at all, produced a console
that served its page and then asked for its assets at a path the gateway does
not route: a blank screen, with every container reporting healthy. That was
reported twice. There is no variable left to get wrong.

**The consoles are not published.** The gateway reaches them at
`controller:8800`, `slep:8810` and `connect:8700` on this project's network, so
`:8800` / `:8810` / `:8700` stop existing on the host. There is no longer an
address at which the gateway's security headers and the platform's central login
throttle can be skipped.

The Controller's **agent API on `9000` is still published**, and must be: managed
hosts across the network dial it.

**Connect finds the Controller by service name.** `install.sh` used to write
this host's LAN IP there, from one `ip route get` at install time, and persist
it — so a host that came back on a different subnet kept pointing Connect at an
address nobody answers at. A service name cannot go stale.

**The Controller reaches Flashback and Baseline by service name too**, instead of
going back out through `host.docker.internal` to ports published on the docker
bridge.

## Updating

```sh
sysiblectl slop update
```

One pull, one build, one platform. `sysiblectl` adds `--profile integrated` and
the overlay file by itself once `SYSIBLE_INTEGRATED=1` is in this directory's
`.env`, which `install.sh --integrated` writes.

## Updating by itself

**Administration → Software & services → Update automatically.**

Off until you turn it on, and that is deliberate: updating the platform recreates
the gateway, the console and the updater, so a run in the middle of an afternoon
signs everyone out mid-task. Pick an hour when nobody is working — it defaults to
03:00 — and the panel tells you when the next run is due.

It is not a second way to update. It decides only *when*, and then presses the
button you would: the same list of what can be updated, the same refusals, the
same job and the same log. A checkout with local changes is reported and refused,
never overwritten.

What it says afterwards is the part worth reading:

| it says | it means |
|---|---|
| **updated** | a job started; the log has the rest |
| **found nothing to do** | everything was already current |
| **did not run** | refused, with the reason — a dirty checkout, or a job already running |
| **failed** | the scheduler itself hit an error |

The last two are marked in amber. A panel that says "last run: Tuesday 03:00"
without saying whether anything came of it is the kind of green that gets
believed about a checkout which has been refusing for three weeks.

**Leave the platform out** if you want your apps current and the front door
changed only by hand — it is the one that signs everyone out. Untick *include
the Operations Platform*; everything else still runs on the schedule.

The schedule lives in the updater's own volume, not in the checkout: writing to
the git tree would make it dirty, and a dirty checkout is the thing that refuses
updates.

## Developing

Edit the apps here. `apps/README.md` and `tools/publish-app` cover pushing a
finished change back out to a standalone repository.
