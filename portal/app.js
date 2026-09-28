// Sysible Linux Operations Platform — portal behaviour: wire each app card to its
// subdomain, poll live health through the gateway, and remember the theme.
(function () {
  "use strict";

  // SLOP is one origin, addressed by PATH: the portal is served at / and each app
  // lives at /<sub>/ on the SAME host. Same-origin relative links, so this works by
  // raw IP or any hostname with no build-time config and no subdomains.
  document.querySelectorAll(".card[data-sub]").forEach(function (card) {
    var sub = card.getAttribute("data-sub");
    card.href = "/" + sub + "/";
    // Open each app in a new tab, keeping the portal open behind it. rel guards the
    // new tab from reaching back through window.opener.
    card.target = "_blank";
    card.rel = "noopener";
  });

  // Sign out: the IdP verifies the double-submit CSRF token when one is supplied.
  // This page is STATIC (served by Caddy), so there is no server-rendered token to
  // embed in the form — read it from the JS-readable 'sysible_csrf' cookie (which is
  // non-HttpOnly for exactly this purpose) and post it as a hidden field.
  (function wireLogout() {
    var form = document.querySelector("form.logout");
    if (!form) return;
    var m = document.cookie.match(/(?:^|;\s*)sysible_csrf=([^;]*)/);
    if (!m) return;                       // no token → the IdP's origin check still gates it
    var f = document.createElement("input");
    f.type = "hidden"; f.name = "csrf"; f.value = decodeURIComponent(m[1]);
    form.appendChild(f);
  })();

  // Health: the gateway proxies /healthz/<app> to each app's own health endpoint,
  // so this stays same-origin (no CORS) and works even if the app subdomains
  // aren't reachable from the browser directly.
  function setDot(app, state) {
    var dot = document.querySelector('.dot[data-health="' + app + '"]');
    if (!dot) return;
    dot.classList.remove("up", "down", "unknown");
    dot.classList.add(state);
    dot.title = state === "up" ? "online" : state === "down" ? "not reachable" : "unknown";
  }

  // A probe that never settles leaves the dot GREY — the colour that means
  // "still checking" — for as long as the tab is open. That is not hypothetical:
  // an app whose container is up but wedged accepts the connection and never
  // answers, and the gateway had no response timeout, so every dot on the portal
  // sat grey while the platform was in trouble. The page is a status board; it
  // has to reach a verdict, and "not reachable" IS a verdict. The gateway now
  // bounds these too, but the page must not depend on the gateway's version to
  // tell the truth about it.
  var PROBE_MS = 8000;
  function poll(app) {
    var ctl = ("AbortController" in window) ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, PROBE_MS);
    var done = false;
    function settle(state) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      setDot(app, state);
    }
    // Without AbortController the fetch itself cannot be cancelled, so fall back
    // to deciding without it rather than waiting forever.
    if (!ctl) setTimeout(function () { settle("down"); }, PROBE_MS);
    fetch("/healthz/" + app, { cache: "no-store", signal: ctl ? ctl.signal : undefined })
      .then(function (r) { settle(r.ok ? "up" : "down"); })
      .catch(function () { settle("down"); });
  }
  // Which apps to poll comes from the dots the page actually renders, not from a
  // second hard-coded list: a card added here and forgotten there shows a dot that
  // is never polled (stuck on "checking…") or polls an app with nowhere to report.
  function healthApps() {
    return Array.prototype.map.call(
      document.querySelectorAll(".dot[data-health]"),
      function (d) { return d.getAttribute("data-health"); });
  }
  function pollAll() { healthApps().forEach(poll); }
  pollAll();
  // Only while the portal is actually on screen. Each sweep is one proxied request
  // per app, and a tab left open all day polled every 15s forever — work the gateway
  // and every app did for nobody. Resume with an immediate sweep so coming back to
  // the tab shows current state rather than a quarter-minute-old one.
  var timer = null;
  function startPolling() {
    if (timer === null) timer = setInterval(pollAll, 15000);
  }
  function stopPolling() {
    if (timer !== null) { clearInterval(timer); timer = null; }
  }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { stopPolling(); } else { pollAll(); startPolling(); }
  });
  if (!document.hidden) startPolling();

  // Who's signed in — the portal sits behind SLOP single sign-on, so show the
  // current user with links to manage their password (/account) and, for a
  // superuser, everyone's accounts (/admin). Best-effort: if /auth/me isn't
  // reachable the chip just stays hidden.
  fetch("/auth/me", { cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) {
      if (!me || !me.authenticated) return;
      var who = document.getElementById("who");
      if (who) who.textContent = me.user + (me.role ? " · " + me.role : "");
      if (me.role === "superuser") {
        // The Administration tile (accounts + all SLOP configuration) is the superuser
        // entry point to /admin — superuser-only, revealed here.
        var adminCard = document.getElementById("card-admin");
        if (adminCard) adminCard.hidden = false;
      }
      var box = document.getElementById("userbox");
      if (box) box.hidden = false;
    })
    .catch(function () { /* not signed in / IdP unreachable — leave chip hidden */ });

  // Theme toggle, persisted; default follows the OS until the user chooses.
  var KEY = "slop-theme";
  var root = document.documentElement;
  var btn = document.getElementById("theme");
  function apply(t) {
    root.setAttribute("data-theme", t);
    if (btn) btn.textContent = t === "light" ? "☾" : "☀";
  }
  var stored = null;
  try { stored = localStorage.getItem(KEY); } catch (e) { /* private mode */ }
  var initial = stored === "light" || stored === "dark"
    ? stored
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  apply(initial);
  if (btn) btn.addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    apply(next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* */ }
  });
})();
