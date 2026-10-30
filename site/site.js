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

/* The hero's download is for the reader's own system: Linux's on a Linux computer, its label and its file, and
   Windows' anywhere else, as it is without this file. The link beside it names the other one and the source. */
(function () {
  "use strict";
  var agent = navigator.userAgent || "", data = navigator.userAgentData;
  var platform = data && data.platform ? data.platform : navigator.platform || agent;
  if (!/linux/i.test(platform) || /android|cros/i.test(agent)) return;

  function linux() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-os]"), function (label) {
      label.hidden = label.getAttribute("data-os") !== "linux";
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-href-linux]"), function (link) {
      link.setAttribute("href", link.getAttribute("data-href-linux"));
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

/* The numbers' charts: a pointer over a chart, or a tap, shows the nearest day's value and date, with a hairline at
   the day; once a chart has the keyboard's focus, the arrow keys move along it. build.py puts where each point is on
   the chart (data-points: its place across in per cent and its height), and its value and day are the same row of the
   table under the chart, which screen readers read, so without this file nothing is missing. */
(function () {
  "use strict";
  function wire() {
    Array.prototype.forEach.call(document.querySelectorAll(".plot[data-points]"), function (plot) {
      var card = plot.closest(".card"), rows = card ? card.querySelectorAll("table tbody tr") : [];
      var points = plot.getAttribute("data-points").split(" ").map(function (p) { return p.split(",").map(Number); });
      if (!points.length || points.length !== rows.length) return;
      var rtl = window.getComputedStyle(plot).direction === "rtl", at = points.length - 1;
      var tip = document.createElement("div"), value = document.createElement("strong"), day = document.createElement("span");
      var cross = document.createElement("div");
      tip.className = "tip";
      cross.className = "cross";
      tip.hidden = cross.hidden = true;
      tip.setAttribute("aria-hidden", "true");
      cross.setAttribute("aria-hidden", "true");
      tip.appendChild(value);
      tip.appendChild(day);
      plot.appendChild(cross);
      plot.appendChild(tip);
      var columns = plot.querySelectorAll("[data-i]");

      function show(i) {
        at = Math.max(0, Math.min(points.length - 1, i));
        var p = points[at], cells = rows[at].cells;
        value.textContent = cells[1].textContent;
        day.textContent = cells[0].textContent;
        tip.style.setProperty("--at", p[0]);
        tip.style.setProperty("--top", (20 + p[1]) + "px");
        cross.style.setProperty("--at", p[0]);
        tip.hidden = cross.hidden = false;
        Array.prototype.forEach.call(columns, function (col) {
          col.classList.toggle("is-on", col.getAttribute("data-i") === String(at));
        });
      }
      function hide() {
        tip.hidden = cross.hidden = true;
        Array.prototype.forEach.call(columns, function (col) { col.classList.remove("is-on"); });
      }
      function nearest(event) {
        var box = plot.querySelector(".chart").getBoundingClientRect();
        var across = ((rtl ? box.right - event.clientX : event.clientX - box.left) / box.width) * 100, best = 0;
        points.forEach(function (p, i) {
          if (Math.abs(p[0] - across) < Math.abs(points[best][0] - across)) best = i;
        });
        return best;
      }
      plot.addEventListener("pointermove", function (event) { show(nearest(event)); });
      plot.addEventListener("pointerdown", function (event) { show(nearest(event)); });
      plot.addEventListener("pointerleave", hide);
      // the keyboard's way in, named by its card's title; screen readers have the table under it
      var title = card.querySelector("h3");
      plot.tabIndex = 0;
      plot.setAttribute("role", "img");
      if (title) plot.setAttribute("aria-label", title.textContent);
      // the focus starts at the newest day, unless a tap that gave it the focus has just shown one
      plot.addEventListener("focus", function () { if (tip.hidden) show(points.length - 1); });
      plot.addEventListener("blur", hide);
      plot.addEventListener("keydown", function (event) {
        var step = { ArrowLeft: rtl ? 1 : -1, ArrowRight: rtl ? -1 : 1, Home: -points.length, End: points.length }[event.key];
        if (!step) return;
        event.preventDefault();
        show(at + step);
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
