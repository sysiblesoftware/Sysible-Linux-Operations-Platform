"""Which build this is — the one thing the console cannot work out for itself.

A SLOP console is server-rendered: the whole page comes out of this container,
so a stale image and a current one are indistinguishable from the outside.
Three rounds of "is this my browser, my server, or your code?" came down to
exactly that, and ended with the answer being "the code was never deployed".

SYSIBLE_REV is baked in at build time (see the Dockerfile) from the checkout's
`git rev-parse --short HEAD`, set by sysiblectl, the updater and install.sh.
Empty means nobody set it — a bare `docker compose up` — and the footer says
"version unknown" rather than inventing one.
"""
from __future__ import annotations

import os

REV = (os.getenv("SYSIBLE_REV") or "").strip()


def footer(product: str) -> str:
    """The footer line, with the build this container is running."""
    from html import escape
    where = f"build {escape(REV)}" if REV else "version unknown"
    return (f"<div class=foot>{escape(product)} \u00b7 Community Edition "
            f"\u00b7 <span class=rev title='The commit this container was built "
            f"from'>{where}</span></div>")
