"""Which build this is — the one thing the console cannot work out for itself.

A SLOP console is server-rendered: the whole page comes out of this container,
so a stale image and a current one are indistinguishable from the outside.
Three rounds of "is this my browser, my server, or your code?" came down to
exactly that, and ended with the answer being "the code was never deployed".

SYSIBLE_REV is baked in at build time (see the Dockerfile) from the checkout's
`git rev-parse --short HEAD`, set by sysiblectl and the updater. Empty means
nobody set it — a bare `docker compose up` — and the chip says "version
unknown" rather than inventing one.

IN THE HEADER, not a footer. The first attempt appended a footer line reading
"Sysible Baseline · Community Edition · version unknown" after the body, and it
was wrong three ways at once: the product name was already in the header right
above it, so the page said its own name twice; the line sat outside the centred
content column, hard against the left edge in text the size of the content; and
Flashback's layout is a grid of height calc(100vh - 49px), so anything after it
is permanently below the fold. Reported as "why is the product name in the
middle of the page" — which it was.

The build is chrome, like the signed-in user it now sits beside.
"""
from __future__ import annotations

import os

REV = (os.getenv("SYSIBLE_REV") or "").strip()

# Scoped under .head so it cannot restyle anything else, and every value carries
# a fallback: the three consoles share a palette but are three stylesheets.
CSS = (".head .rev{margin-left:.85em;color:var(--faint,#6f7d92);font-size:11px;"
       "letter-spacing:.03em;font-family:var(--mono,ui-monospace,SFMono-Regular,"
       "Menlo,monospace);white-space:nowrap}"
       "@media(max-width:620px){.head .rev{display:none}}")


def chip() -> str:
    """The build, as a header chip beside the signed-in user."""
    from html import escape
    if REV:
        return (f"<span class=rev title='The commit this container was built "
                f"from'>build {escape(REV)}</span>")
    return ("<span class=rev title='This image was built without a revision, so "
            "it cannot say which commit it is running'>version unknown</span>")
