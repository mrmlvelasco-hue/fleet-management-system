/* ==========================================================================
   FMS DASHBOARD — MOTION LAYER  (drop-in, zero dependencies)
   Save as: static/js/dashboard-animations.js
   Pairs with: static/css/dashboard-animations.css

   It auto-tags your existing markup, so you do NOT have to edit the Jinja
   template. Just include the CSS + this file and the dashboard animates.
   Public API: window.FMSDashFX = { refresh, countUp, setValue, speed }
   ========================================================================== */
(function () {
  "use strict";

  var root = document.documentElement;
  var prefersReduced = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;

  /* Marks the page as "JS is alive" so the CSS is allowed to hide things. */
  root.classList.add("fx-on");

  var EASE_OUT_CUBIC = function (t) { return 1 - Math.pow(1 - t, 3); };
  var seen = "__fxSeen";

  /* ---------------------------------------------------------------- utils */
  function each(sel, ctx, fn) {
    var list = (ctx || document).querySelectorAll(sel);
    for (var i = 0; i < list.length; i++) fn(list[i], i);
  }

  function tag(el, kind, index) {
    if (el[seen]) return;
    el[seen] = true;
    el.setAttribute("data-anim", kind);
    el.style.setProperty("--i", String(index));
  }

  /* ------------------------------------------------- 1. auto-tag elements */
  function autoTag(ctx) {
    each(".ent-kpi-grid", ctx, function (grid) {
      each(".ent-kpi", grid, function (el, i) { tag(el, "rise", i); });
    });
    each(".ent-panel", ctx, function (el, i) { tag(el, "rise", i % 3); });
    each(".ent-tiles", ctx, function (wrap) {
      each(".ent-tile", wrap, function (el, i) { tag(el, "pop", i); });
    });
    each(".ent-gov", ctx, function (wrap) {
      each(".ent-gov__card", wrap, function (el, i) { tag(el, "rise", i); });
    });
    each(".ent-ribbon", ctx, function (wrap) {
      each(".ent-ribbon__item", wrap, function (el, i) { tag(el, "rise", i); });
    });
    each(".ent-flow", ctx, function (flow) {
      each(".ent-flow__step", flow, function (step, i) {
        step.style.setProperty("--i", String(i));
      });
      if (!flow.hasAttribute("data-anim")) tag(flow, "fade", 0);
    });
    /* worklist rows */
    each(".worklist-item", ctx, function (el, i) {
      el.style.setProperty("--i", String(i % 5));
    });
    /* sparklines: normalise the path so the CSS "draw" works on any shape */
    each(".ent-kpi__spark polyline", ctx, function (line) {
      line.setAttribute("pathLength", "1");
    });
  }

  /* -------------------------------------------- 2. reveal on scroll (IO)  */
  var io = null;
  if ("IntersectionObserver" in window && !prefersReduced) {
    io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-in");
          io.unobserve(entry.target);
          startCounters(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
  }

  function observe(ctx) {
    each("[data-anim]", ctx, function (el) {
      if (el.__fxObserved) return;
      el.__fxObserved = true;
      if (io) io.observe(el);
      else { el.classList.add("is-in"); startCounters(el); }
    });
  }

  /* ------------------------------------------------ 3. animated counters  */
  function countUp(el, target, opts) {
    opts = opts || {};
    if (!el || isNaN(target)) return;
    var decimals = parseInt(opts.decimals, 10) || 0;
    var prefix = opts.prefix || "";
    var suffix = opts.suffix || "";
    var duration = prefersReduced ? 0 : (opts.duration || 1200);

    var format = function (v) {
      var n = decimals > 0
        ? v.toFixed(decimals)
        : Math.round(v).toLocaleString();
      return prefix + n + suffix;
    };
    if (!duration) { el.textContent = format(target); return; }

    var start = null;
    (function step(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / duration, 1);
      el.textContent = format(EASE_OUT_CUBIC(p) * target);
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = format(target);
    })(performance.now());
  }

  function startCounters(scope) {
    var nodes = [];
    if (scope && scope.matches && scope.matches("[data-counter]")) nodes.push(scope);
    each("[data-counter]", scope, function (el) { nodes.push(el); });

    nodes.forEach(function (el) {
      if (el.__fxCounted) return;
      el.__fxCounted = true;
      var val = parseFloat(el.getAttribute("data-counter"));
      if (isNaN(val)) return;
      countUp(el, val, {
        decimals: el.getAttribute("data-decimals") || 0,
        prefix: el.getAttribute("data-prefix") || "",
        suffix: el.getAttribute("data-suffix") || "",
        duration: 1200
      });
    });

    /* Server-rendered plain numbers (KPI values, workflow counts) */
    each(".ent-kpi__value, .ent-flow__count", scope, function (el) {
      if (el.__fxCounted || el.hasAttribute("data-kpi") || el.querySelector("*")) return;
      var raw = (el.textContent || "").trim().replace(/,/g, "");
      if (!/^\d+(\.\d+)?$/.test(raw)) return;
      el.__fxCounted = true;
      countUp(el, parseFloat(raw), {
        decimals: raw.indexOf(".") > -1 ? 1 : 0,
        duration: 1100
      });
    });
  }

  /* ---------------------------- 4. deferred values (your AJAX widgets)     */
  function setValue(el, value, opts) {
    if (!el) return;
    el.classList.remove("fx-skel");
    el.__fxCounted = true;
    var num = parseFloat(String(value).replace(/,/g, ""));
    if (isNaN(num)) { el.textContent = value; return; }
    countUp(el, num, opts || { duration: 900 });
    el.classList.add("fx-flash");
    setTimeout(function () { el.classList.remove("fx-flash"); }, 950);
  }

  /* -------------------------- 5. swap bootstrap spinners for skeletons     */
  function skeletonise(ctx) {
    each("[data-kpi] .spinner-border", ctx, function (sp) {
      var host = sp.closest("[data-kpi]");
      sp.remove();
      host.classList.add("fx-skel", "fx-skel--line");
      host.style.width = "72px";
      host.textContent = "0";
    });
    each("[data-tile] .spinner-border", ctx, function (sp) {
      var host = sp.closest("[data-tile]");
      sp.remove();
      host.classList.add("fx-skel");
      host.style.minWidth = "22px";
      host.textContent = "0";
    });
  }

  /* ------------------------------------ 6. worklist pagination (animated)  */
  function initPager() {
    var group = document.getElementById("approvalWorklistGroup");
    if (!group) return;
    var items = [].slice.call(group.querySelectorAll(".worklist-item"));
    if (items.length <= 5) return;

    var pageSize = 5;
    var page = 1;
    var pages = Math.ceil(items.length / pageSize);
    var info = document.getElementById("actionWorklistPageInfo");
    var prev = document.getElementById("prevWorklistPage");
    var next = document.getElementById("nextWorklistPage");

    function render(p) {
      page = p;
      var start = (p - 1) * pageSize;
      var end = start + pageSize;
      items.forEach(function (item, idx) {
        var visible = idx >= start && idx < end;
        item.style.setProperty("display", visible ? "" : "none", "important");
        if (visible) {
          item.style.setProperty("--i", String(idx - start));
          /* restart the entrance animation */
          item.classList.remove("worklist-item");
          void item.offsetWidth;
          item.classList.add("worklist-item");
        }
      });
      if (info) {
        info.textContent = "Showing " + (start + 1) + "\u2013" +
          Math.min(end, items.length) + " of " + items.length;
      }
      if (prev) prev.disabled = page === 1;
      if (next) next.disabled = page === pages;
    }

    if (prev) prev.addEventListener("click", function () { if (page > 1) render(page - 1); });
    if (next) next.addEventListener("click", function () { if (page < pages) render(page + 1); });
    render(1);
  }

  /* ------------------------------------------- 7. Chart.js nicer motion    */
  function tuneChartJs() {
    if (!window.Chart || prefersReduced) return;
    var d = window.Chart.defaults;
    d.animation = d.animation || {};
    d.animation.duration = 1100;
    d.animation.easing = "easeOutQuart";
    d.animations = d.animations || {};
    d.animations.numbers = { type: "number", duration: 1100, easing: "easeOutQuart" };
    if (d.plugins && d.plugins.tooltip) d.plugins.tooltip.animation = { duration: 180 };
    if (d.elements && d.elements.arc) d.elements.arc.hoverOffset = 8;
  }

  /* ------------------------------------------------------ 8. bootstrapping */
  function refresh(ctx) {
    autoTag(ctx);
    skeletonise(ctx);
    observe(ctx);
  }

  function boot() {
    tuneChartJs();
    refresh(document);
    initPager();

    /* Content injected later (e.g. due_maintenance_html) animates too. */
    if (window.MutationObserver) {
      var pending = null;
      new MutationObserver(function () {
        clearTimeout(pending);
        pending = setTimeout(function () { refresh(document); }, 80);
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.FMSDashFX = {
    refresh: refresh,
    countUp: countUp,
    setValue: setValue,
    speed: function (multiplier) {
      root.style.setProperty("--fx-speed", String(multiplier));
    }
  };
})();
