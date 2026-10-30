/* The website's anonymous counts (docs/privacy.md, "The website"). Each time a page is opened it sends one page_view
   to Aptabase, and a click on a download sends one download: only values from the fixed lists below, and of the site
   that linked here only its host name. With them go the browser's language, its system in one word and the version
   the page offers. No cookie and no storage: the session number is drawn when the page is opened and kept nowhere, so
   nothing links two visits. Nothing is sent when the browser asks for Do Not Track or Global Privacy Control, or
   anywhere but the published site, so a local preview, a copy elsewhere or a page opened from the disk sends
   nothing. build.py puts the key, the address, the site's host, the version and the page on this script's element,
   and leaves the script out without a key. It is deferred, so it never holds the page up; it never throws, waits or
   tries again, and a count that fails is lost. */
(function () {
  "use strict";
  var script = document.currentScript, nav = window.navigator || {};
  if (nav.doNotTrack === "1" || window.doNotTrack === "1" || nav.globalPrivacyControl === true) return;
  if (!script || !window.fetch || location.hostname !== script.getAttribute("data-host")) return;
  var key = script.getAttribute("data-key"), endpoint = script.getAttribute("data-endpoint");
  var page = script.getAttribute("data-page");
  if (!key || !endpoint) return;

  // What each event may hold: each prop with its values, or null for the referrer, which is a host name
  var EVENTS = {
    "page_view": {"page": ["en", "ar", "404"], "device": ["phone", "tablet", "desktop"],
                  "theme": ["light", "dark", "system"], "referrer": null},
    "download": {"platform": ["windows", "linux", "source"], "place": ["hero", "get"], "page": ["en", "ar", "404"]}
  };
  var HOST = /^[a-z0-9._:\[\]-]{1,60}$/;

  // the session number: the second the page was opened and eight random digits, as Aptabase reads it
  var random = Math.floor(Math.random() * 1e8);
  try {
    random = window.crypto.getRandomValues(new Uint32Array(1))[0] % 1e8;
  } catch (e) { /* Math.random's will do */ }
  var session = String(Math.floor(Date.now() / 1000)) + String(1e8 + random).slice(1);

  function system() {
    var data = nav.userAgentData, said = data && data.platform ? data.platform : nav.userAgent || "";
    return /android/i.test(said) ? "Android" : /iphone|ipad|ipod/i.test(said) ? "iOS" : /windows/i.test(said) ? "Windows"
      : /mac/i.test(said) ? "macOS" : /linux/i.test(said) ? "Linux" : "other";
  }

  var SYSTEM = {locale: (nav.language || "").slice(0, 10), appVersion: (script.getAttribute("data-version") || "").slice(0, 50),
                isDebug: false, sdkVersion: "sedjem-site@1", osName: system()};

  // the stylesheet's widths: a phone's layout under 700 px, the full one from 1100
  function device() {
    var width = window.innerWidth || document.documentElement.clientWidth || 0;
    return width < 700 ? "phone" : width < 1100 ? "tablet" : "desktop";
  }

  // the theme in force, as the theme button names it: the reader's pick, or the system's
  function theme() {
    var choice = document.documentElement.getAttribute("data-theme");
    return choice === "light" || choice === "dark" ? choice : "system";
  }

  // the host of the site that linked here, without www.; "none" for no link or one from this site
  function referrer() {
    var host;
    try {
      host = new URL(document.referrer).hostname.toLowerCase().replace(/^www\./, "");
    } catch (e) {
      return "none";
    }
    return !host || host === location.hostname.replace(/^www\./, "") ? "none" : host.slice(0, 60);
  }

  // whether an event is one of the two, with exactly its props and each value from its list
  function checked(name, props) {
    var allowed = EVENTS.hasOwnProperty(name) ? EVENTS[name] : null;
    if (!allowed || Object.keys(props).length !== Object.keys(allowed).length) return false;
    return Object.keys(allowed).every(function (prop) {
      var value = props[prop];
      return typeof value === "string" && (allowed[prop] ? allowed[prop].indexOf(value) >= 0 : HOST.test(value));
    });
  }

  function send(name, props) {
    if (!checked(name, props)) return;
    try {
      fetch(endpoint, {
        method: "POST", keepalive: true, credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer",
        headers: {"App-Key": key, "Content-Type": "application/json"},
        body: JSON.stringify({timestamp: new Date().toISOString(), sessionId: session, eventName: name,
                              systemProps: SYSTEM, props: props})
      }).catch(function () { /* lost */ });
    } catch (e) { /* lost */ }
  }

  function view() {
    send("page_view", {page: page, device: device(), theme: theme(), referrer: referrer()});
  }

  // a page the browser prepares before it is opened counts when it is shown
  if (document.prerendering) document.addEventListener("prerenderingchange", view, {once: true});
  else view();

  // A download: what its link names in data-download (the hero's is the label on show, which site.js picks) and
  // data-place. Each once a page load, so a second click on the same link isn't counted again.
  var counted = {};
  document.addEventListener("click", function (event) {
    var link = event.target && event.target.closest ? event.target.closest("[data-download]") : null;
    if (!link) return;
    var shown = link.querySelector("[data-os]:not([hidden])");
    var platform = link.getAttribute("data-download") || (shown ? shown.getAttribute("data-os") : "");
    var place = link.getAttribute("data-place");
    if (counted[platform + " " + place]) return;
    counted[platform + " " + place] = true;
    send("download", {platform: platform, place: place, page: page});
  });
})();
