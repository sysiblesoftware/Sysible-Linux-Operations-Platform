"""The accounts page, after it was reported as feeling clunky.

The faults were structural rather than cosmetic, and each one has a test here so
it cannot quietly come back:

  * the role form was `class=row`, and `.row>*{flex:1}` made the Set button
    exactly as wide as the select it confirms — and painted it in the accent
    green, the loudest colour on the page, for the least consequential control
  * "Create user" was the LOGIN form's full-width green submit, inherited
  * a four-column table with one row put the Password header 300px from the
    button it labelled, because column 4 is empty for your own account
  * the create-user fields carried no autocomplete, so the browser filled them
    with the signed-in admin's own saved credentials
  * Delete had no confirmation at all
"""
import importlib
import os
import re
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ADMIN_PW = "adminpassword123"
HDR = {"origin": "http://slop.lan"}


@pytest.fixture()
def cl(tmp_path):
    from starlette.testclient import TestClient
    os.environ.update(
        SLOP_DATA_DIR=str(tmp_path), SLOP_DB_PATH=str(tmp_path / "idp.db"),
        SLOP_ADMIN_USER="admin", SLOP_ADMIN_PASSWORD=ADMIN_PW,
        SLOP_ADMIN_FORCE_CHANGE="0", SLOP_ALLOW_INSECURE_COOKIE="1",
    )
    import app as m
    importlib.reload(m)
    with TestClient(m.app, base_url="http://slop.lan") as c:
        c.get("/login", follow_redirects=False)
        c.post("/login", data={"username": "admin", "password": ADMIN_PW,
                               "csrf": c.cookies.get("sysible_csrf")},
               headers=HDR, follow_redirects=False)
        yield c


def _tok(c):
    c.get("/login", follow_redirects=False)
    return c.cookies.get("sysible_csrf")


def _add(c, user, role="operator"):
    return c.post("/admin/users", data={"username": user, "role": role,
                                        "password": "TempPassword123!", "csrf": _tok(c)},
                  headers=HDR, follow_redirects=True)


def _page(c):
    r = c.get("/admin")
    assert r.status_code == 200
    return r.text


def _body(c):
    """The markup only. _CSS is inlined into every page, so a stylesheet comment
    mentioning a tag is otherwise indistinguishable from the page using it."""
    html = _page(c)
    return html.split("</style>", 1)[1]


# ---- the shape of a row ----------------------------------------------------
def test_an_account_is_a_row_not_a_table_cell(cl):
    html = _page(cl)
    assert "<table>" not in html, "the accounts table is back"
    assert html.count("<div class=acct>") == 1, "one row for the one account"


def test_the_role_form_is_not_a_flex_row(cl):
    """`.row>*{flex:1}` is what made Set as wide as the select. It is the rule
    the login form needs and the one this row must not use."""
    html = _page(cl)
    assert "class=acct-role" in html
    assert "/role' class=row" not in html, "the role form is a .row again"


def test_set_is_not_the_pages_primary_button(cl):
    """Accent green is for the action the page exists for. Confirming a role is
    not it; creating an account is."""
    html = _page(cl)
    assert "class='btn ghost sm js-setrole'" in html, "Set is not a ghost button"
    # exactly one accent button on the page, and it is Create user
    assert html.count("<button class=btn type=submit>Create user</button>") == 1
    assert "<button type=submit>Create user</button>" not in html, \
        "Create user is the full-width login submit again"


def test_set_is_rendered_enabled(cl):
    """It is disabled from JavaScript once the page loads, so a browser with no
    JavaScript must still get a working button rather than a dead one."""
    html = _page(cl)
    # Matched on the js-setrole hook, not on the full class string: this test is
    # about the disabled attribute, and keying it to the styling made it fail for
    # the wrong reason the moment the button was restyled.
    m = re.search(r"<button[^>]*js-setrole[^>]*>", html)
    assert m, html[:400]
    assert "disabled" not in m.group(0), m.group(0)


def test_the_role_select_carries_its_current_value_for_the_script(cl):
    """The script compares against data-was to know whether anything changed."""
    html = _page(cl)
    assert "data-was='superuser'" in html


# ---- your own account ------------------------------------------------------
def test_you_cannot_delete_yourself_and_the_row_says_why(cl):
    html = _page(cl)
    assert "/admin/users/admin/delete" not in html
    assert "this is you" in html, "the row does not explain its missing Delete"


def test_another_account_does_get_a_delete(cl):
    _add(cl, "rsmith")
    html = _page(cl)
    assert "/admin/users/rsmith/delete" in html


# ---- destructive actions ---------------------------------------------------
def test_deleting_an_account_asks_first(cl):
    """There was no confirmation at all: one click removed an account."""
    _add(cl, "rsmith")
    html = _page(cl)
    assert "class=js-del" in html and "data-user='rsmith'" in html
    assert "window.confirm(" in html
    assert "cannot be undone" in html


def test_delete_is_not_painted_red_on_every_row(cl):
    """Seven rows of red is a wall of alarm over an ordinary action, and leaves
    nothing louder for the confirmation that now carries the weight."""
    _add(cl, "rsmith")
    html = _page(cl)
    assert "btn danger" not in html, "Delete is danger-styled again"


# ---- the create-user form --------------------------------------------------
def test_the_new_password_field_refuses_autofill(cl):
    """Without it the browser treats this as a sign-in form and fills it with the
    signed-in admin's own credentials — one submit from a second superuser
    holding your password. new-password is the token that actually stops it."""
    html = _page(cl)
    assert "autocomplete=new-password" in html
    assert "autocomplete=off" in html, "the username field still invites autofill"


def test_creating_a_user_still_works(cl):
    """The form was restructured, so prove the server still receives all of it."""
    r = _add(cl, "newperson", role="auditor")
    assert r.status_code == 200
    html = _page(cl)
    assert "newperson" in html
    assert html.count("<div class=acct>") == 2


def test_the_count_matches_the_rows(cl):
    assert "1 account<" in _page(cl)
    _add(cl, "rsmith")
    html = _page(cl)
    assert "2 accounts<" in html
    assert html.count("<div class=acct>") == 2


# ---- the page's own structure ----------------------------------------------
def test_both_halves_of_the_page_are_labelled_the_same_way(cl):
    """One was a <fieldset><legend>, the other had no heading at all."""
    body = _body(cl)
    assert "<fieldset>" not in body
    assert body.count('<h2 class=sect>') == 2


def test_the_explanation_is_not_chained_onto_the_link_list(cl):
    """It was joined to the nav links with the same separator the links use, so
    it read as a sixth link."""
    html = _page(cl)
    assert "<a href='/'>Portal →</a><br>" in html
    assert "Portal →</a> · one credential" not in html
