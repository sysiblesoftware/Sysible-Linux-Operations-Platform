// Sysible Operations Platform — portal behaviour: wire each app card to its
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

  function poll(app) {
    fetch("/healthz/" + app, { cache: "no-store" })
      .then(function (r) { setDot(app, r.ok ? "up" : "down"); })
      .catch(function () { setDot(app, "down"); });
  }
  function pollAll() { ["controller", "slep", "connect"].forEach(poll); }
  pollAll();
  setInterval(pollAll, 15000);

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
        var a = document.getElementById("adminlink");
        if (a) a.hidden = false;
        // The Administration tile (accounts + all SLOP configuration) is superuser-only.
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
