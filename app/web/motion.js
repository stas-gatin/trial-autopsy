// Motion layer: calm, clinical animation that explains what is happening.
//   - sections and cards fade up as they scroll into view (staggered within a group);
//   - key numbers count up the first time they are seen;
//   - an ECG trace runs while the agents investigate, and a thin pulse line shows network activity.
// Everything is skipped when the system asks for reduced motion. Content is never hidden from print.
(function () {
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const REVEAL = [".home-section", ".hero-demo", ".proof .pf-i", ".acc-tile", ".flow > li", ".bw-card", ".an-card",
    ".report > .panel", ".report > section", ".details", ".pb-card", ".value-cards > *",
    ".cmp-card", ".home-chain > li", ".mode-card"].join(",");
  const COUNT = ".pf-n, .acc-n, .hd-bar-v, .count-up";

  if (reduce || !("IntersectionObserver" in window)) return;
  document.documentElement.classList.add("motion");

  // ---- reveal on scroll ----------------------------------------------------------------------
  const revealIO = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("rv-in");
      revealIO.unobserve(e.target);
    }
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.06 });

  function prepareReveal(el) {
    if (el.dataset.rv) return;
    el.dataset.rv = "1";
    const siblings = el.parentElement ? [...el.parentElement.children].filter((c) => c.matches(REVEAL)) : [el];
    const i = Math.max(0, siblings.indexOf(el));
    el.style.setProperty("--rv-delay", `${Math.min(i, 6) * 70}ms`);
    el.classList.add("rv");
    revealIO.observe(el);
  }

  // ---- count-up numbers ----------------------------------------------------------------------
  const NUM = /(-?\d[\d,]*\.?\d*)/;
  const countIO = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      countIO.unobserve(e.target);
      countUp(e.target);
    }
  }, { threshold: 0.4 });

  function firstNumberNode(el) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (NUM.test(n.nodeValue)) return n;
    return null;
  }

  function countUp(el) {
    const node = firstNumberNode(el);
    if (!node) return;
    const m = node.nodeValue.match(NUM);
    const raw = m[1];
    const target = parseFloat(raw.replace(/,/g, ""));
    if (!isFinite(target) || target === 0) return;
    const decimals = (raw.split(".")[1] || "").length;
    const grouped = raw.includes(",");
    const before = node.nodeValue.slice(0, m.index), after = node.nodeValue.slice(m.index + raw.length);
    const fmt = (v) => {
      const s = v.toFixed(decimals);
      return grouped ? Number(s).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : s;
    };
    const dur = 900, t0 = performance.now();
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      node.nodeValue = before + fmt(target * ease(t)) + after;
      if (t < 1) requestAnimationFrame(tick);
      else node.nodeValue = before + raw + after;
    };
    requestAnimationFrame(tick);
  }

  function prepareCount(el) {
    if (el.dataset.cu) return;
    el.dataset.cu = "1";
    countIO.observe(el);
  }

  // ---- watch the page for new content ----------------------------------------------------------
  function scan(root) {
    if (!(root instanceof Element)) return;
    if (root.matches(REVEAL)) prepareReveal(root);
    if (root.matches(COUNT)) prepareCount(root);
    root.querySelectorAll(REVEAL).forEach(prepareReveal);
    root.querySelectorAll(COUNT).forEach(prepareCount);
  }
  new MutationObserver((records) => {
    for (const r of records) r.addedNodes.forEach(scan);
  }).observe(document.body, { childList: true, subtree: true });
  scan(document.body);

  // ---- hero: a faint heart monitor runs behind the first screen -------------------------------------
  function heroMonitor() {
    const hero = document.querySelector(".hero2");
    if (!hero || hero.querySelector(".hero-ecg")) return;
    let pts = [];
    for (let k = 0; k < 8; k++) {
      const x = k * 150;
      pts.push(`${x},60 ${x + 60},60 ${x + 70},54 ${x + 80},60 ${x + 92},60 ${x + 98},68 ${x + 104},14 ${x + 110},96 ` +
               `${x + 116},60 ${x + 128},60 ${x + 138},50 ${x + 150},60`);
    }
    const svg = `<svg class="hero-ecg" viewBox="0 0 1200 120" preserveAspectRatio="none" aria-hidden="true">` +
      `<polyline class="he-base" points="${pts.join(" ")}"/><polyline class="he-pulse" points="${pts.join(" ")}"/></svg>`;
    hero.insertAdjacentHTML("afterbegin", svg);
  }
  heroMonitor();
  new MutationObserver(heroMonitor).observe(document.body, { childList: true, subtree: false });

  // ---- network pulse: a thin ECG line under the top bar while data loads --------------------------
  const bar = document.createElement("div");
  bar.className = "net-pulse";
  bar.innerHTML = '<svg viewBox="0 0 240 12" preserveAspectRatio="none" aria-hidden="true">' +
    '<polyline points="0,6 70,6 80,6 86,1 92,11 98,3 104,6 240,6"/></svg>';
  document.body.appendChild(bar);
  let inflight = 0, hideTimer = null;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    inflight += 1;
    clearTimeout(hideTimer);
    document.body.classList.add("is-loading");
    try { return await nativeFetch(...args); } finally {
      inflight = Math.max(0, inflight - 1);
      if (!inflight) hideTimer = setTimeout(() => document.body.classList.remove("is-loading"), 250);
    }
  };
})();
