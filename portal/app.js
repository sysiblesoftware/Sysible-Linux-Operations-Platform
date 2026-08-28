// Sysible Operations Platform — portal behaviour: wire each app card to its
// subdomain, poll live health through the gateway, and remember the theme.
(function () {
  "use strict";

  // The portal is served at the apex domain (e.g. slop.lan). Each app lives at
  // <sub>.<apex>/. Derive that from the current host so the links work on any
  // domain the operator chose, with no build-time config.
  var apex = location.hostname;                 // e.g. "slop.lan" or "localhost"
  var proto = location.protocol;                // keep https

  document.querySelectorAll(".card[data-sub]").forEach(function (card) {
    var sub = card.getAttribute("data-sub");
    card.href = proto + "//" + sub + "." + apex + "/";
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
