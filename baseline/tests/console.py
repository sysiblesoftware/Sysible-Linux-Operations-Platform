"""Driving the Baseline console from a test, in the shape it has now.

ONE PLACE. The console was three resizable columns — a browse list (#browselist),
a middle column of whatever the selection was made of (#col2list), and a detail
pane (#col3) — and seven test files reached into those ids directly, ninety-odd
times between them. When the layout became a left rail and one view, every one
of those selectors pointed at something that no longer existed, and each one
failed as an eight-second Playwright timeout rather than as an assertion: a
three-minute suite took twenty.

The selectors live here now. A test says what it wants to look at — this host,
that state, the runs — and the next layout change is this file rather than
every file.
"""


def goto(pg, nav, settle=250):
    """Open one of the rail's sections: overview, hosts, states, runs, rules."""
    pg.click(f"[data-nav={nav}]")
    pg.wait_for_timeout(settle)


def ready(pg, nav="hosts", timeout=8000):
    """Open a section and wait for its rows. What `wait_for_selector
    ('#browselist .it')` used to mean, now that the list is not always on
    screen."""
    pg.click(f"[data-nav={nav}]")
    pg.wait_for_selector(".grid .r", timeout=timeout)
    pg.wait_for_timeout(200)


def rows(pg, nav=None):
    """The rows of the open section (or of `nav`, if given)."""
    if nav:
        goto(pg, nav)
    return pg.locator(".grid .r")


def names(pg, nav="hosts"):
    """Every row's first cell, in the order the console put them in."""
    goto(pg, nav)
    return pg.eval_on_selector_all(
        ".grid .r .c:first-child", "els => els.map(e => e.textContent.trim())")


def open_host(pg, host_id, settle=400):
    """Open one host's detail from the Hosts section."""
    goto(pg, "hosts")
    pg.locator(f".grid .r[data-host='{host_id}']").first.click()
    pg.wait_for_timeout(settle)


def open_state(pg, name, settle=500):
    """Open one state's detail from the States section."""
    goto(pg, "states")
    pg.locator(f".grid .r[data-state='{name}']").first.click()
    pg.wait_for_timeout(settle)


def open_first_state(pg, settle=500):
    goto(pg, "states")
    pg.locator(".grid .r[data-state]").first.click()
    pg.wait_for_timeout(settle)


def detail(pg):
    """The open detail's text. #col3 used to be a column that always existed;
    the detail now exists only when something is open, which is the point."""
    return pg.text_content("#detail") or ""


def filter_to(pg, nav, text, settle=300):
    goto(pg, nav)
    pg.fill("#filter", text)
    pg.wait_for_timeout(settle)
