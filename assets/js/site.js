/* Foundry — the interactive layer.
 *
 * Vanilla, no dependencies, ~5 KB, deferred. Every behaviour degrades: with
 * JavaScript off the page is still complete, every tab panel is reachable,
 * the carousel is a scrollable row, and the call button is in the header.
 *
 * That matters more here than on a normal site. These pages are lead capture
 * — if the phone number depends on a script, a script failure costs a call.
 */
(function () {
  "use strict";

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- mobile navigation ------------------------------------------------ */
  var burger = document.querySelector("[data-burger]");
  var nav = document.querySelector("[data-nav]");
  if (burger && nav) {
    burger.addEventListener("click", function () {
      var open = document.body.classList.toggle("nav-open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    nav.addEventListener("click", function (e) {
      if (e.target.tagName === "A") {
        document.body.classList.remove("nav-open");
        burger.setAttribute("aria-expanded", "false");
      }
    });
  }

  /* ---- header state + reading progress ---------------------------------- */
  var header = document.querySelector("[data-header]");
  var progress = document.querySelector("[data-progress]");
  var sticky = document.querySelector("[data-sticky-call]");
  var lastY = 0;

  function onScroll() {
    var y = window.scrollY || 0;
    if (header) header.classList.toggle("is-stuck", y > 12);
    if (progress) {
      var h = document.documentElement.scrollHeight - window.innerHeight;
      progress.style.transform = "scaleX(" + (h > 0 ? Math.min(y / h, 1) : 0) + ")";
    }
    // The sticky call bar appears once the hero CTA has scrolled away, and
    // hides again while scrolling up so it never covers what you are reading.
    if (sticky) {
      var show = y > 520 && y > lastY - 4;
      sticky.hidden = !show;
    }
    lastY = y;
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ---- reveal on scroll -------------------------------------------------- */
  var reveals = document.querySelectorAll(".reveal, .show-row, .sign, .cost, .tl-item, .cov-col");
  if (reduce || !("IntersectionObserver" in window)) {
    Array.prototype.forEach.call(reveals, function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    Array.prototype.forEach.call(reveals, function (el) { io.observe(el); });
  }

  /* ---- counters ---------------------------------------------------------- */
  var counters = document.querySelectorAll(".count[data-to]");
  function runCount(el) {
    var to = parseInt(el.getAttribute("data-to"), 10) || 0;
    if (reduce) { el.textContent = String(to); return; }
    var start = null, dur = 900;
    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      el.textContent = String(Math.round(to * (1 - Math.pow(1 - p, 3))));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  if (counters.length) {
    if (!("IntersectionObserver" in window)) {
      Array.prototype.forEach.call(counters, runCount);
    } else {
      var co = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) { runCount(e.target); co.unobserve(e.target); }
        });
      }, { threshold: 0.4 });
      Array.prototype.forEach.call(counters, function (el) { co.observe(el); });
    }
  }

  /* ---- tabs -------------------------------------------------------------- */
  Array.prototype.forEach.call(document.querySelectorAll("[data-tabs]"), function (root) {
    var tabs = root.querySelectorAll(".tab");
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener("click", function () {
        var key = tab.getAttribute("data-tab");
        Array.prototype.forEach.call(tabs, function (t) {
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        });
        Array.prototype.forEach.call(root.querySelectorAll("[data-panel]"), function (p) {
          p.hidden = p.getAttribute("data-panel") !== key;
        });
      });
    });
  });

  /* ---- carousel ---------------------------------------------------------- */
  Array.prototype.forEach.call(document.querySelectorAll("[data-carousel]"), function (root) {
    var track = root.querySelector(".car-track");
    var slides = root.querySelectorAll(".car-slide");
    var dots = root.querySelector(".car-dots");
    if (!track || slides.length < 2) { if (dots) dots.remove(); return; }
    var i = 0;

    Array.prototype.forEach.call(slides, function (_, n) {
      var d = document.createElement("button");
      d.className = "car-dot";
      d.setAttribute("aria-label", "Slide " + (n + 1));
      d.addEventListener("click", function () { go(n); });
      dots.appendChild(d);
    });

    function go(n) {
      i = (n + slides.length) % slides.length;
      var slide = slides[i];
      track.scrollTo({ left: slide.offsetLeft - track.offsetLeft, behavior: reduce ? "auto" : "smooth" });
      Array.prototype.forEach.call(dots.children, function (d, k) {
        d.classList.toggle("on", k === i);
      });
    }
    // Auto-advance: slide sideways on its own every few seconds. It pauses
    // while the pointer is over it, while it has keyboard focus, and briefly
    // after any manual interaction, so it never fights the reader. Respects
    // prefers-reduced-motion (reduce) by not autoplaying at all.
    var timer = null;
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    function play() {
      stop();
      if (reduce || slides.length < 2) return;
      timer = setInterval(function () { go(i + 1); }, 5000);
    }
    function bump() { go(i + 1); play(); }      // manual next, then restart clock
    function back() { go(i - 1); play(); }

    root.querySelector('[data-car="prev"]').addEventListener("click", back);
    root.querySelector('[data-car="next"]').addEventListener("click", bump);
    Array.prototype.forEach.call(dots.children, function (d) {
      d.addEventListener("click", play);         // reset the clock on a dot tap
    });
    root.addEventListener("mouseenter", stop);
    root.addEventListener("mouseleave", play);
    root.addEventListener("focusin", stop);
    root.addEventListener("focusout", play);
    root.addEventListener("touchstart", stop, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop(); else play();
    });

    track.addEventListener("scroll", function () {
      var nearest = 0, best = Infinity;
      Array.prototype.forEach.call(slides, function (s, k) {
        var d = Math.abs(s.offsetLeft - track.offsetLeft - track.scrollLeft);
        if (d < best) { best = d; nearest = k; }
      });
      i = nearest;
      Array.prototype.forEach.call(dots.children, function (d, k) {
        d.classList.toggle("on", k === i);
      });
    }, { passive: true });
    go(0);
    play();
  });

  /* ---- smooth in-page links --------------------------------------------- */
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = a.getAttribute("href").slice(1);
    var target = id && document.getElementById(id);
    if (!target) return;
    e.preventDefault();
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    var focusable = target.querySelector("input, textarea, button");
    if (focusable) setTimeout(function () { focusable.focus({ preventScroll: true }); }, 420);
  });
})();

/* Directory filter — appended for the directory skeleton.
 * No index, no fetch: every place is already in the page, so the input just
 * hides rows. On a 4,000-city site that is still one DOM pass and no network.
 */
(function () {
  "use strict";
  var input = document.querySelector("[data-dir-filter]");
  if (!input) return;
  var items = document.querySelectorAll("[data-dir-item]");
  var groups = document.querySelectorAll("[data-dir-group]");
  var empty = document.querySelector("[data-dir-empty]");
  var count = document.querySelector("[data-dir-count]");
  var original = count ? count.textContent : "";

  input.addEventListener("input", function () {
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    Array.prototype.forEach.call(items, function (li) {
      var hit = !q || li.getAttribute("data-dir-item").indexOf(q) !== -1;
      li.hidden = !hit;
      if (hit) shown++;
    });
    Array.prototype.forEach.call(groups, function (g) {
      var any = g.querySelector("[data-dir-item]:not([hidden])");
      g.hidden = !any;
    });
    if (empty) empty.hidden = shown !== 0;
    if (count) count.textContent = q ? shown + " match" + (shown === 1 ? "" : "es") : original;
  });
})();
