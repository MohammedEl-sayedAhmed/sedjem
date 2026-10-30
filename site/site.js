/* The theme. The page follows the system's light or dark setting until the reader switches with the button in the
   header (in the menu, on a phone). Each press switches to the other theme, so every press changes what the page
   looks like. A switch to the system's own theme is no pick: the page follows the system again and nothing is kept.
   Any other is kept in this browser under the site's own key and put on the page here, in the head, before the page
   is first drawn, so the other theme never shows first. The button's icon and its name both say what a press does: a
   moon on a light page, a sun on a dark one (the stylesheet draws it, so it follows the system too). Without this file the page follows
   the system, and the button isn't shown. */
(function () {
  "use strict";
  var KEY = "sedjem-site-theme";
  var root = document.documentElement;
  var system = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");

  function stored() {
    try {
      var choice = window.localStorage.getItem(KEY);
      return choice === "light" || choice === "dark" ? choice : "system";
    } catch (e) {
      return "system";  // storage is turned off: follow the system
    }
  }

  function systems() {
    return system && system.matches ? "dark" : "light";
  }

  // the theme on show: the reader's pick, or the system's
  function shown() {
    var choice = root.getAttribute("data-theme");
    return choice === "light" || choice === "dark" ? choice : systems();
  }

  function other() {
    return shown() === "dark" ? "light" : "dark";
  }

  function apply(choice) {
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    // the browser's own parts (scroll bars, form controls, the canvas) and its bars take the page's scheme and colour
    root.style.colorScheme = choice === "system" ? "" : choice;
    var schemes = document.querySelector('meta[name="color-scheme"]');
    if (schemes) schemes.setAttribute("content", choice === "system" ? "light dark" : choice);
    Array.prototype.forEach.call(document.querySelectorAll('meta[name="theme-color"]'), function (meta) {
      var scheme = meta.getAttribute("data-scheme");
      meta.setAttribute("media", choice === "system" ? "(prefers-color-scheme: " + scheme + ")"
        : choice === scheme ? "all" : "not all");
    });
  }

  function wire() {
    var buttons = document.querySelectorAll("[data-theme-switch]");
    function name() {
      Array.prototype.forEach.call(buttons, function (button) {
        var text = button.getAttribute("data-to-" + other());
        button.setAttribute("aria-label", text);
        button.setAttribute("title", text);
        var visible = button.querySelector(".theme-name");
        if (visible) visible.textContent = text;
      });
    }
    name();
    // while the page follows the system, a change of the system's theme changes what a press does
    if (system && system.addEventListener) system.addEventListener("change", name);
    else if (system && system.addListener) system.addListener(name);
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        var to = other(), choice = to === systems() ? "system" : to;
        apply(choice);
        try {
          if (choice === "system") window.localStorage.removeItem(KEY);
          else window.localStorage.setItem(KEY, choice);
        } catch (e) { /* not kept: the pick lasts until the page is left */ }
        name();
      });
    });
  }

  apply(stored());
  root.classList.add("can-switch-theme");
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();

/* The hero's download is for the reader's own system: Linux's on a Linux computer, and Windows' anywhere else, as
   it is without this file. The link beside it names the other one and the source. */
(function () {
  "use strict";
  var agent = navigator.userAgent || "", data = navigator.userAgentData;
  var platform = data && data.platform ? data.platform : navigator.platform || agent;
  if (!/linux/i.test(platform) || /android|cros/i.test(agent)) return;

  function linux() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-os]"), function (label) {
      label.hidden = label.getAttribute("data-os") !== "linux";
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", linux);
  else linux();
})();

/* The page's one motion. The hero's transcript arrives line by line, as the app shows a transcript while it is
   being written, once, in about two seconds. Its timing is the stylesheet's: --motion-start, --motion-step and
   --motion-row, in milliseconds, each multiplied by --motion, which the reader's reduced motion turns to 0. Every
   line is in the page from the start; this file only holds them back until their turn, so without it, or with
   less motion asked for, they are all simply there. It runs in the page's head, after the stylesheet, so it can
   hide the lines before anyone sees them. */
(function () {
  "use strict";
  var root = document.documentElement;
  if (!Element.prototype.animate || !window.getComputedStyle) return;
  var css = window.getComputedStyle(root);
  function token(name) {
    return parseFloat(css.getPropertyValue(name)) || 0;
  }
  var motion = token("--motion");
  if (!(motion > 0)) return;
  var start = token("--motion-start") * motion, step = token("--motion-step") * motion, row = token("--motion-row") * motion;
  var running = [];

  root.classList.add("will-reveal");

  function reveal() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-reveal] .row"), function (line, i) {
      running.push(line.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
        { duration: row, delay: start + i * step, easing: "cubic-bezier(0.2, 0, 0, 1)", fill: "backwards" }));
    });
    // the animations now hold the lines back themselves, each until its turn
    root.classList.remove("will-reveal");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reveal);
  else reveal();

  // less motion asked for on the way: every line at once
  var less = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
  if (less && less.addEventListener) less.addEventListener("change", function (e) {
    if (!e.matches) return;
    running.forEach(function (a) { a.finish(); });
    root.classList.remove("will-reveal");
  });
})();
