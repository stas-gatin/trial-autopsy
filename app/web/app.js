"use strict";

const AGENTS = [
  ["Intake", "Trial, drug, mode, scope"],
  ["CaseBuilder", "Registry, genetics, literature, quoted facts"],
  ["MechanismAnalyst", "Causal pathway and calculator inputs"],
  ["Calculator", "Four deterministic checks"],
  ["Investigator", "Differential diagnosis"],
  ["Critic", "Checks citations, sends work back"],
  ["ReadThrough", "Other programmes on the target"],
  ["Reporter", "Analyst-facing report"],
];

const CATEGORY = {
  target_not_causal: "Target does not cause the disease",
  biomarker_not_causal: "Drug moves the wrong biomarker",
  effect_too_small: "Effect too small for this trial",
  off_target_harm: "Off-target harm",
  population: "Wrong patient population",
  design_or_endpoint: "Design or endpoint problem",
  execution_or_nonscientific: "Execution / non-scientific",
  insufficient_data: "Insufficient data to conclude",
  robust_success: "Robust success",
  narrow_success: "Narrow success",
  success_with_caveats: "Success with caveats",
};

const OUTCOME = {
  failure: ["Failed", "o-fail"], stopped_harm: ["Stopped: harm", "o-fail"], success: ["Succeeded", "o-ok"],
  likely_failure: ["Likely to fail", "o-fail"], borderline: ["Borderline", "o-warn"],
  likely_success: ["Likely to succeed", "o-ok"], not_computable: ["Not computable", "o-info"],
};

const STATUS_LABEL = { supported: "Supported by the facts", weakened: "Ruled out by the facts", undetermined: "Not enough data" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x === null || x === undefined ? "–" : x >= 0.995 && x < 1 ? ">99%" : x > 0 && x < 0.005 ? "<1%" : `${Math.round(x * 100)}%`);
const num = (x, d = 3) => (x === null || x === undefined || x === "" ? "–" : Number(x).toFixed(d).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, ""));

let mode = "auto";
let factsById = {};
let verifyById = {};   // fact_id -> the Verifier's result for that number (checked through Zetaris)
let pollTimer = null;
let currentRun = null;
let problemAnnotated = false;
let analysesCache = [];
let filterKind = "all";

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

/* ---------- intake ---------- */

let sampleMode = false;   // offline demo: only recorded analyses can run

async function refreshHealth() {
  try {
    const h = await getJSON("/health");
    sampleMode = !!h.sample_mode;
    const chip = $("modeChip");
    chip.textContent = h.sample_mode ? "SAMPLE MODE · offline, recorded" : "LIVE · open data sources";
    chip.className = `chip ${h.sample_mode ? "chip-sample" : "chip-live"}`;
  } catch { $("modeChip").textContent = "API unreachable"; }
}

async function init() {
  refreshHealth();

  $("modeSeg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $("idHelpBtn").addEventListener("click", openIdHelp);
  $("caseForm").addEventListener("submit", (e) => { e.preventDefault(); startRun(formRequest()); });
  $("factFilter").addEventListener("input", filterFacts);
  document.querySelectorAll("#filters button").forEach((b) => b.addEventListener("click", () => {
    filterKind = b.dataset.f;
    document.querySelectorAll("#filters button").forEach((x) => x.classList.toggle("active", x === b));
    renderAnalyses();
  }));
  $("setSave").addEventListener("click", saveSettings);
  setupMolControls();
  $("applyRecommended").addEventListener("click", async () => { pending.apply_recommended = true; await saveSettings(); });
  window.addEventListener("hashchange", route);
  route();
  $("drawerClose").addEventListener("click", closeDrawer);
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
  $("replayBtn").addEventListener("click", startReplay);
  $("mapTop").addEventListener("click", () => { selectTab("tabMap"); $("tabMap").scrollIntoView({ behavior: "smooth", block: "start" }); });
  $("lgDetails").addEventListener("toggle", () => { if ($("lgDetails").open) drawGraph(); });
  $("printBtn").addEventListener("click", () => window.print());
  $("cmpBtn").addEventListener("click", () => {
    const other = currentRun === "bt-accelerate" ? "bt-clear_outcomes" : currentRun === "bt-fourier" ? "bt-accelerate" : "bt-fourier";
    location.hash = `#/compare?a=${encodeURIComponent(currentRun || "")}&b=${other}`;
  });
  ["cmpA", "cmpB"].forEach((id) => $(id).addEventListener("change", () => {
    location.hash = `#/compare?a=${encodeURIComponent($("cmpA").value)}&b=${encodeURIComponent($("cmpB").value)}`;
  }));
  window.addEventListener("beforeprint", () => {
    const t = $("caseTitle").textContent;
    $("printHead").innerHTML = `<span class="ph-brand">Trial Autopsy</span><span>${esc(t)}</span>` +
      `<span>Exported ${new Date().toISOString().slice(0, 10)} · ${esc(location.hash.replace("#/run/", "run "))}</span>`;
    document.title = `Trial Autopsy — ${t}`;
  });
  window.addEventListener("afterprint", () => { document.title = "Trial Autopsy"; });
  $("replaySpeed").addEventListener("click", () => {
    replaySpeed = replaySpeed === 1 ? 2 : replaySpeed === 2 ? 4 : 1;
    $("replaySpeed").textContent = `Speed ${replaySpeed}×`;
  });
  $("replayTop").addEventListener("click", () => {
    selectTab("tabTrace");
    $("replayGraph").scrollIntoView({ behavior: "smooth", block: "center" });
    stopReplay(); replayStep = 0; setTimeout(startReplay, 450);
  });
  $("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
}

function setMode(m) {
  mode = m;
  $("modeSeg").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  $("modeHint").textContent = {
    auto: "Autopsy if results are out, forecast if the trial is still running.",
    autopsy: "Explain why a finished trial failed or succeeded.",
    forecast: "Estimate the risk before results: what could sink it.",
  }[m];
}

function formRequest() {
  const input = { mode };
  const trial = $("trial").value.trim().toUpperCase();
  if (trial) input.trial = trial;
  if ($("readout").value.trim()) input.readout_text = $("readout").value.trim();
  if ($("asOf").value) input.as_of = $("asOf").value;
  if ($("archiveCase").value.trim()) input.archive_case = $("archiveCase").value.trim();
  return { input, options: {} };
}

/* ---------- pages ---------- */

async function route() {
  const h = location.hash || "#/";
  if (h.startsWith("#/compare")) {
    document.querySelectorAll(".topnav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === "compare"));
    hide("homeView"); hide("workView"); hide("settingsView"); hide("problemView"); show("compareView");
    renderCompare(h);
    window.scrollTo({ top: 0 });
    return;
  }
  hide("compareView");
  document.querySelectorAll(".topnav a").forEach((a) => a.classList.toggle("active",
    (h === "#/" && a.dataset.nav === "home") || (h.startsWith("#/analyses") && a.dataset.nav === "analyses") ||
    ((h.startsWith("#/new") || h.startsWith("#/run")) && a.dataset.nav === "new") ||
    (h.startsWith("#/settings") && a.dataset.nav === "settings") ||
    (h.startsWith("#/problem") && a.dataset.nav === "problem")));
  if (h.startsWith("#/run/")) {
    const id = decodeURIComponent(h.slice(6));
    showWork();
    if (id === currentRun && !$("report").classList.contains("hidden")) return;
    try {
      const job = await getJSON(`/api/runs/${encodeURIComponent(id)}`);
      if (job.status === "done" && job.result.status === "success") { currentRun = id; hide("emptyState"); hide("errorBox"); hide("progress"); render(job.result); }
      else if (job.status === "running") { startPolling(id); }
      else showError(job.result.error || { type: "Error", message: "Run did not complete" }, job.result);
    } catch { showError({ type: "Not found", message: `No saved analysis called ${id}.` }); }
    return;
  }
  if (h.startsWith("#/problem")) {
    hide("homeView"); hide("workView"); hide("settingsView"); show("problemView");
    if (typeof annotateTerms === "function" && !problemAnnotated) {
      document.querySelectorAll("#problemView .pb-step p, #problemView .fc p").forEach(annotateTerms);
      problemAnnotated = true;
    }
    window.scrollTo({ top: 0 });
    return;
  }
  hide("problemView");
  if (h.startsWith("#/settings")) {
    hide("homeView"); hide("workView"); show("settingsView");
    loadSettings();
    window.scrollTo({ top: 0 });
    return;
  }
  hide("settingsView");
  if (h.startsWith("#/new")) {
    showWork(); currentRun = null;
    hide("report"); hide("errorBox"); hide("progress"); show("emptyState");
    document.body.classList.remove("has-report");
    window.scrollTo({ top: 0 });
    return;
  }
  showHome();
  if (h.startsWith("#/analyses")) setTimeout(() => $("analysesSection").scrollIntoView({ behavior: "smooth" }), 60);
  else window.scrollTo({ top: 0 });
}

function showWork() { hide("homeView"); show("workView"); }

async function showHome() {
  show("homeView"); hide("workView");
  if (!analysesCache.length) {
    try { analysesCache = await getJSON("/api/analyses"); } catch { analysesCache = []; }
    renderAnalyses();
    renderFeatured();
    renderTargetMap();
    renderAccuracy();
    renderBenchmark();
    renderProof();
    renderHeroDemo();
  }
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const KIND_LABEL = { autopsy: "Autopsy", forecast: "Forecast", backtest: "Historical backtest" };

function renderAnalyses() {
  const order = { autopsy: 0, forecast: 1, backtest: 2 };
  const items = analysesCache.filter((a) => filterKind === "all" || a.kind === filterKind)
    .sort((x, y) => (order[x.kind] ?? 3) - (order[y.kind] ?? 3) || String(x.trial).localeCompare(String(y.trial), "en", { sensitivity: "base" }));
  $("analysesGrid").innerHTML = items.length ? `<table class="an-table"><thead><tr><th>Trial</th><th>Drug</th><th>Type</th><th>Outcome</th><th>Main cause</th><th>Date</th></tr></thead><tbody>` +
    items.map((a) => {
      const [label, cls] = OUTCOME[a.outcome] || [a.outcome || "", "o-info"];
      const date = a.kind === "backtest" ? `as of ${a.analysis_date || ""}` : (a.readout_date ? `result ${a.readout_date}` : (a.analysis_date || ""));
      const mine = a.origin === "recent" && !String(a.run_id).startsWith("example-") && !String(a.run_id).startsWith("bt-");
      return `<tr data-href="#/run/${encodeURIComponent(a.run_id)}" title="${esc(a.headline || "")}">
        <td><a href="#/run/${encodeURIComponent(a.run_id)}" class="an-t">${esc(a.trial || a.nct_id || "")}</a>${mine ? ' <span class="an-new">your run</span>' : ""}</td>
        <td>${esc(a.drug || "")}<div class="muted an-mech">${esc(a.mechanism || "")}</div></td>
        <td><span class="an-kind k-${esc(a.kind)}">${esc(KIND_LABEL[a.kind] || a.kind)}</span></td>
        <td><span class="an-out ${cls}">${esc(label)}</span></td>
        <td class="an-cause">${esc(CATEGORY[a.primary_category] || a.primary_category || "")}</td>
        <td class="muted an-date">${esc(date)}</td></tr>`;
    }).join("") + "</tbody></table>" : '<p class="muted">No analyses yet.</p>';
  document.querySelectorAll("#analysesGrid tr[data-href]").forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.tagName !== "A") location.hash = tr.dataset.href; }));
}

// Hero: a live miniature of a real report (pelacarsen), so the first screen shows what the product does.
async function renderHeroDemo() {
  const box = $("heroDemo");
  let res;
  try { res = (await getJSON("/api/runs/example-pelacarsen")).result; } catch { box.innerHTML = ""; return; }
  const o = res.output, v = o.verdict, c = o.calculator;
  const links = chainLinks(o), [fi] = focusLink(links, v.primary_category);
  const [label, cls] = OUTCOME[v.outcome] || [v.outcome, "o-info"];
  const short = ["Target causes disease", "Drug moves a causal marker", "Benefit big enough", "Trial big enough"];
  box.innerHTML = `<div class="hd-top"><span class="hd-live">Real example</span><span class="an-out ${cls}">${esc(label)}</span></div>
    <div class="hd-title">${esc(res.trial.acronym || "")} <span class="muted">· ${esc(res.drug.name)}</span></div>
    <div class="hd-sub">Novartis said on 4 Sep 2026 the trial missed its goal. Why?</div>
    <ol class="hd-chain">${links.map((l, i) => `<li class="hd-${l.s}${i === fi ? " hd-focus" : ""}"><span class="hd-dot"></span>
      <span class="hd-q">${short[i]}</span><span class="hd-a">${esc(l.a)}</span></li>`).join("")}</ol>
    <div class="hd-bar"><div class="hd-bar-l">Chance the trial could prove the expected effect</div>
      <div class="hd-track"><div class="hd-fill" style="width:${Math.round((c.power || 0) * 100)}%"></div></div>
      <div class="hd-bar-v">${pct(c.power)} <span class="muted">· expected HR ${num(c.expected_hr)}</span></div></div>
    <div class="hd-verdict"><span class="muted">Diagnosis</span> <b>${esc(CATEGORY[v.primary_category] || v.primary_category)}</b></div>
    <a class="hd-open" href="#/run/example-pelacarsen">Open the full report →</a>`;
}

// Proof strip under the hero: the blind-test numbers, computed from the evaluation files.
async function renderProof() {
  let acc, cal;
  try { [acc, cal] = await Promise.all([getJSON("/api/accuracy"), getJSON("/api/calibration")]); } catch { return; }
  const b = acc.backtest; if (!b) return;
  const pts = (cal.rows || []).filter((r) => r.predicted_hr && r.ci && r.ci[0]);
  const inRange = pts.filter((r) => r.ci[0] <= r.predicted_hr && r.predicted_hr <= r.ci[1]).length;
  const item = (n, l) => `<div class="pf-i"><div class="pf-n">${n}</div><div class="pf-l">${l}</div></div>`;
  $("proofStrip").innerHTML = item(b.cases, "past trials replayed blind, as of their result day") +
    item(`${b.category_overlap}/${b.completed}`, "causes matching the expert consensus") +
    item(`${inRange}/${pts.length}`, "effect predictions inside the trial's 95% range") +
    item(`${b.prediction_correct}/${b.prediction_scored}`, "success-or-failure forecasts right") +
    `<a class="pf-more" href="#/analyses" onclick="setTimeout(()=>document.querySelector('#accuracy').scrollIntoView({behavior:'smooth'}),80)">How we test →</a>`;
}

// Target map: all analysed trials grouped by what human genetics says, coloured by the real outcome.
const TARGET_CLASS = { HCAR2: "niacin" };
async function renderTargetMap() {
  let cal = { rows: [] };
  try { cal = await getJSON("/api/calibration"); } catch { /* outcomes fall back to the verdict */ }
  const actual = Object.fromEntries((cal.rows || []).map((r) => [r.run_id, r.actual]));
  const nonCausal = (b) => /^(hdl|lp-?pla2|spla2)/i.test(String(b || "").replace(/\s/g, "")) && !/non-hdl/i.test(String(b || ""));
  const groups = { yes: [], no: [], none: [] };
  for (const a of analysesCache) {
    if (!["autopsy", "forecast", "backtest"].includes(a.kind)) continue;
    const target = a.target || (/niacin/i.test(a.drug || "") ? "HCAR2" : null);
    const g = a.target_genetics === "not_causal" || nonCausal(a.intended_biomarker) ? "no"
      : a.expected_hr === null || a.expected_hr === undefined ? (a.kind === "forecast" ? "yes" : "none") : "yes";
    const out = a.kind === "forecast" ? "pending" : (actual[a.run_id] || a.outcome);
    groups[g].push({ ...a, target, out: out === "stopped_harm" ? "failure" : out });
  }
  const tile = (target, trials) => {
    const p = PATHWAY[target] || {};
    return `<div class="tm-tile"><div class="tm-t">${esc(p.name || target || "other")}</div><div class="tm-r">${esc(p.role || "")}</div>
      <div class="tm-trials">${trials.map((t) => `<a class="tm-pill tm-${esc(t.out)}" href="#/run/${encodeURIComponent(t.run_id)}" title="${esc(t.drug || "")}: ${esc(t.out === "pending" ? "result pending" : t.out === "success" ? "succeeded" : "failed")}">${esc(t.trial)}</a>`).join("")}</div></div>`;
  };
  const column = (key, title, sub) => {
    const items = groups[key]; if (!items.length) return "";
    const byT = {}; items.forEach((t) => { (byT[t.target || t.drug] = byT[t.target || t.drug] || []).push(t); });
    const done = items.filter((t) => t.out !== "pending"), won = done.filter((t) => t.out === "success").length;
    return `<div class="tm-col tm-${key}"><div class="tm-head"><b>${title}</b><span>${sub}</span>
        <div class="tm-score">${won} of ${done.length} <small>finished trials succeeded</small></div></div>
      ${Object.entries(byT).map(([t, trials]) => tile(t, trials)).join("")}</div>`;
  };
  const yes = groups.yes.filter((t) => t.out !== "pending"), no = groups.no.filter((t) => t.out !== "pending");
  $("tmapLead").innerHTML = `Every trial we analysed, grouped by what human genetics says about its target and biomarker. ` +
    `<b>When genetics backs them, ${yes.filter((t) => t.out === "success").length} of ${yes.length} trials succeeded; when it does not, ${no.filter((t) => t.out === "success").length} of ${no.length}.</b>`;
  $("targetMap").innerHTML = column("yes", "Genetics backs the target and the biomarker", "lowering LDL, Lp(a)…") +
    column("no", "Genetics says the biomarker or target is not a cause", "raising HDL, blocking plaque enzymes") +
    column("none", "No validated formula for the mechanism", "inflammation: judged on evidence") +
    `<div class="tm-legend"><span><i class="tm-dot tm-success"></i> succeeded</span><span><i class="tm-dot tm-failure"></i> failed or stopped</span>
      <span><i class="tm-dot tm-pending"></i> still running</span>
      ${groups.no.some((t) => t.trial === "REVEAL" && t.out === "success") ? '<span class="muted">REVEAL succeeded narrowly through anacetrapib’s small LDL-lowering side effect, not by raising HDL.</span>' : ""}</div>`;
}

/* ---------- Compare two trials ---------- */
function miniBell(o) {
  const c = o.calculator, t = trialOdds(o);
  if (!c.expected_hr) return '<div class="cmp-nobell">No forecast: no validated formula for this mechanism.</div>';
  if (!t) return `<div class="cmp-nobell">Expected HR ${num(c.expected_hr)}; trial size not public, so no chance can be computed.</div>`;
  const W = 420, H = 120, L = 10, R = 10, base = 96, lo = 0.6, hi = 1.3;
  const X = (hr) => L + (Math.log(hr) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * (W - L - R);
  const pts = []; for (let i = 0; i <= 160; i++) { const lx = Math.log(lo) + (Math.log(hi) - Math.log(lo)) * i / 160; pts.push([lx, Math.exp(-0.5 * ((lx - t.mu) / t.se) ** 2)]); }
  const area = (keep) => { const seg = pts.filter(([lx]) => keep(lx)); if (seg.length < 2) return "";
    const xs = seg.map(([lx]) => X(Math.exp(lx))); return `M${xs[0]} ${base} ` + seg.map(([, d], i) => `L${xs[i].toFixed(1)} ${(base - d * 66).toFixed(1)}`).join(" ") + ` L${xs[xs.length - 1]} ${base} Z`; };
  const cut = Math.log(t.pass), px = X(t.pass), pp = Math.round(t.power * 100);
  return `<svg viewBox="0 0 ${W} ${H}" class="cmp-bell"><path class="bell-pass" d="${area((lx) => lx <= cut)}"/><path class="bell-miss" d="${area((lx) => lx >= cut)}"/>
    <line x1="${L}" y1="${base}" x2="${W - R}" y2="${base}" stroke="#c9d3dd"/><line x1="${X(1)}" y1="20" x2="${X(1)}" y2="${base}" stroke="#3c4a5e" stroke-dasharray="3 3"/>
    <line x1="${px}" y1="14" x2="${px}" y2="${base}" stroke="#142133" stroke-width="2"/>
    <text x="${px - 6}" y="24" text-anchor="end" class="cmp-bp">${pp >= 100 ? ">99" : pp}% pass</text><text x="${px + 6}" y="24" class="cmp-bm">${pp >= 100 ? "<1" : 100 - pp}% miss</text>
    ${[0.7, 0.8, 0.9, 1.0, 1.1, 1.2].map((v) => `<text x="${X(v)}" y="${base + 16}" text-anchor="middle" class="tick">${v.toFixed(1)}</text>`).join("")}</svg>`;
}

async function renderCompare(h) {
  if (!analysesCache.length) { try { analysesCache = await getJSON("/api/analyses"); } catch { analysesCache = []; } }
  const q = new URLSearchParams(h.split("?")[1] || "");
  const ids = analysesCache.map((a) => a.run_id);
  const a = ids.includes(q.get("a")) ? q.get("a") : "bt-clear_outcomes", b = ids.includes(q.get("b")) ? q.get("b") : "bt-accelerate";
  const opts = (sel) => analysesCache.map((x) => `<option value="${esc(x.run_id)}"${x.run_id === sel ? " selected" : ""}>${esc(x.trial)} · ${esc(x.drug || "")}</option>`).join("");
  $("cmpA").innerHTML = opts(a); $("cmpB").innerHTML = opts(b);
  let ra, rb, cal = { rows: [] };
  try { [ra, rb, cal] = await Promise.all([getJSON(`/api/runs/${encodeURIComponent(a)}`), getJSON(`/api/runs/${encodeURIComponent(b)}`), getJSON("/api/calibration").catch(() => ({ rows: [] }))]); }
  catch { $("cmpCols").innerHTML = '<p class="muted">Could not load these analyses.</p>'; return; }
  ra = ra.result; rb = rb.result;
  const real = Object.fromEntries((cal.rows || []).map((r) => [r.run_id, r]));
  const la = chainLinks(ra.output), lb = chainLinks(rb.output);
  const short = ["Target causes disease", "Drug moves a causal marker", "Benefit big enough", "Trial big enough"];
  const col = (res, links, other) => {
    const o = res.output, v = o.verdict, c = o.calculator, ci = o.calculator_inputs, rr = real[res.run_id];
    const actual = rr ? rr.actual : v.outcome;
    const [label, cls] = OUTCOME[actual] || [actual, "o-info"];
    return `<div class="cmp-col"><div class="cmp-top"><div><div class="cmp-t">${esc(KNOWN_TRIAL_NAME(res))}</div>
        <div class="muted">${esc(res.drug.name)} · ${esc((res.drug.mechanisms || []).join("; "))}</div></div><span class="an-out ${cls}">${esc(label)}</span></div>
      <ol class="hd-chain cmp-chain">${links.map((l, i) => `<li class="hd-${l.s}${other && other[i].s !== l.s ? " cmp-differs" : ""}"><span class="hd-dot"></span>
        <span class="hd-q">${short[i]}</span><span class="hd-a">${esc(l.a)}</span></li>`).join("")}</ol>
      ${miniBell(o)}
      <table class="kv cmp-kv">
        <tr><td>Causal lowering</td><td>${c.ldl_equivalent_mmol !== null && c.ldl_equivalent_mmol !== undefined ? `${num(c.ldl_equivalent_mmol, 2)} mmol/L LDL-equivalent` : "–"}</td></tr>
        <tr><td>Expected HR</td><td>${c.expected_hr ? num(c.expected_hr) : "–"}</td></tr>
        <tr><td>Primary events</td><td>${ci.events || "unknown"}</td></tr>
        <tr><td>Follow-up</td><td>${ci.followup_years ? num(ci.followup_years, 1) + " years" : "unknown"}</td></tr>
        <tr><td>Chance to prove it</td><td>${c.power !== null && c.power !== undefined ? pct(c.power) : "–"}</td></tr>
        ${rr && rr.observed_hr ? `<tr><td>What the trial found</td><td>HR ${rr.observed_hr}${rr.ci && rr.ci[0] ? ` (${rr.ci[0]}–${rr.ci[1]})` : ""}</td></tr>` : ""}
      </table>
      <div class="cmp-cause"><span class="muted">Diagnosis</span> <b>${esc(CATEGORY[v.primary_category] || v.primary_category)}</b></div>
      <a href="#/run/${encodeURIComponent(res.run_id)}">Open the full report →</a></div>`;
  };
  $("cmpCols").innerHTML = col(ra, la, lb) + col(rb, lb, la);
  // the decisive difference: the first link broken in one trial but not the other; otherwise the first that differs at all
  const broken = la.findIndex((l, i) => l.s !== lb[i].s && (l.s === "bad" || lb[i].s === "bad"));
  const d = broken >= 0 ? broken : la.findIndex((l, i) => l.s !== lb[i].s);
  document.querySelectorAll("#cmpCols .cmp-chain").forEach((ol) => ol.querySelectorAll("li").forEach((li, i) => li.classList.toggle("cmp-differs", i === d)));
  $("cmpDiff").innerHTML = d < 0 ? "<b>Both trials pass the same links.</b> The difference lies in the size of the effect or the trial: compare the bells."
    : `<b>The decisive difference: ${short[d].toLowerCase()}.</b> ${esc(KNOWN_TRIAL_NAME(ra))}: ${esc(la[d].a)} — ${esc(la[d].d)} ${esc(KNOWN_TRIAL_NAME(rb))}: ${esc(lb[d].a)} — ${esc(lb[d].d)}`;
}
const KNOWN_TRIAL_NAME = (res) => (analysesCache.find((x) => x.run_id === res.run_id) || {}).trial || (res.trial || {}).acronym || (res.trial || {}).nct_id;

const FEATURED = [
  { run: "example-pelacarsen", tag: "Live autopsy · 2026", look: "The Verifier found in Zetaris that the 2.5-year follow-up is a minimum, not the median; the pre-registered prediction, and who is next on the same target." },
  { run: "bt-dal_outcomes", tag: "The Verifier catches a number", look: "Zetaris showed that \"2.5 years\" of follow-up meant 80% of patients were followed at least that long, not the median; the critic sent the work back." },
  { run: "bt-cantos", tag: "Honest abstention", look: "Inflammation has no validated formula, so the calculator refuses to invent a number; the agents judge the evidence." },
  { run: "bt-fourier", tag: "Every link holds", look: "A causal target, a large LDL drop and a big trial: >99% chance. Colour PCSK9 by its domains in 3D." },
];

function renderFeatured() {
  const byId = Object.fromEntries(analysesCache.map((a) => [a.run_id, a]));
  $("featured").innerHTML = FEATURED.map((f) => {
    const a = byId[f.run]; if (!a) return "";
    const [label, cls] = OUTCOME[a.outcome] || [a.outcome || "", "o-info"];
    return `<a class="ft-card" href="#/run/${encodeURIComponent(f.run)}"><div class="ft-top"><span class="ft-tag">${esc(f.tag)}</span><span class="an-out ${cls}">${esc(label)}</span></div>
      <div class="ft-title">${esc(a.trial)}</div><div class="muted ft-drug">${esc(a.drug || "")}</div>
      <div class="ft-cause">${esc(CATEGORY[a.primary_category] || "")}</div>
      <p><span class="ft-look">Look at:</span> ${esc(f.look)}</p><span class="ft-open">Open →</span></a>`;
  }).join("");
}

// Same blind backtests, two model families: the product's NVIDIA Nemotron setup against Claude.
async function renderBenchmark() {
  const box = $("benchmark");
  if (!box) return;
  let data;
  try { data = await getJSON("/api/benchmark"); } catch { box.innerHTML = ""; return; }
  const models = data.models || [];
  if (models.length < 2) { box.closest(".home-section").classList.add("hidden"); return; }
  box.closest(".home-section").classList.remove("hidden");
  const logo = (m) => logoImg(m.label === "product" ? "nvidia" : "anthropic", 18);
  const metric = (label, f) => `<tr><td>${label}</td>${models.map((m) => `<td class="bm-v">${f(m)}</td>`).join("")}</tr>`;
  const frac = (a, b) => `<span class="count-up">${a}</span><span class="muted">/${b}</span>`;
  const cases = Object.keys(models[0].rows).filter((c) => models.every((m) => m.rows[c]));
  const agree = cases.filter((c) => models[0].rows[c].predicted === models[1].rows[c].predicted).length;
  box.innerHTML = `<table class="bm-table"><thead><tr><th></th>${models.map((m) =>
      `<th>${logo(m)} ${esc(m.name)}${m.label === "product" ? ' <span class="pill live">in the product</span>' : ""}</th>`).join("")}</tr></thead><tbody>` +
    metric("Outcome predicted right", (m) => frac(m.summary.prediction_correct, m.summary.prediction_scored)) +
    metric("Experts' cause among the causes named", (m) => frac(m.summary.category_overlap, m.summary.completed)) +
    metric("Experts' cause as the main cause", (m) => frac(m.summary.category_correct, m.summary.completed)) +
    metric("Effect size within 0.03 of the answer key", (m) => frac(m.summary.expected_hr_within_0_03, m.summary.expected_hr_compared)) +
    `</tbody></table><p class="home-note">Both columns are the same ${cases.length} trials, replayed blind from documents published before each result.
      The two model families reached the same success-or-failure call on ${agree} of ${cases.length}.
      The Claude column is an earlier recorded run, made before the prompts were tuned for Nemotron.</p>`;
}

// Worked examples under "How to read this table", taken from the current results so they never go stale.
function outcomeExamples(rows, mark) {
  const graded = rows.filter((r) => r.prediction_correct === true || r.prediction_correct === false);
  const pick = [...graded.filter((r) => r.prediction_correct).slice(0, 3), ...graded.filter((r) => !r.prediction_correct)].slice(0, 4);
  return pick.map((r) => `<b>${esc(r.trial)}</b>: ${r.chance >= 0.995 ? "&gt;99%" : pct(r.chance)} → ${r.actual === "success" ? "succeeded" : "failed"} ${mark(r.prediction_correct)}`).join(". ") + ".";
}

function causeExamples(rows, mark) {
  const name = (c) => (CATEGORY[c] || c || "").toLowerCase();
  const main = rows.find((r) => r.cause_correct), side = rows.find((r) => !r.cause_correct && r.cause_overlap), miss = rows.find((r) => !r.cause_overlap);
  const out = [];
  if (main) out.push(`<b>${esc(main.trial)}</b>: agents and experts both said “${esc(name(main.cause))}” ${mark(true)}`);
  if (side) out.push(`<b>${esc(side.trial)}</b>: agents' main cause “${esc(name(side.cause))}”, with the experts' “${esc(name(side.gold_cause))}” among their contributing causes ${mark(true)}`);
  if (miss) out.push(`<b>${esc(miss.trial)}</b>: agents said “${esc(name(miss.cause))}”, experts “${esc(name(miss.gold_cause))}” ${mark(false)}`);
  return out.join(". ") + ".";
}

async function renderAccuracy() {
  try {
    const acc = await getJSON("/api/accuracy");
    const b = acc.backtest, c = acc.calculator;
    if (!b) { $("accuracy").innerHTML = '<p class="muted">No evaluation run yet.</p>'; return; }
    const tile = (n, d, label, sub) => `<div class="acc-tile"><div class="acc-n">${n}<span>/${d}</span></div><div class="acc-l">${label}</div><div class="acc-s">${sub}</div></div>`;
    $("accuracy").innerHTML =
      `<p class="home-note">Blind test on ${b.cases} historical trials: each agent run saw only documents published before that trial's result day, and the answer key was held out.</p>` +
      `<div class="acc-grid">` +
      tile(b.prediction_correct, b.prediction_scored, "outcomes predicted correctly", "success vs failure, from evidence before the result") +
      tile(b.category_overlap, b.completed, "causes matching the scientific consensus", `${b.category_correct}/${b.completed} as the single main cause`) +
      tile(b.expected_hr_within_0_03, b.expected_hr_compared, "effect sizes within 0.03 of the answer key", "numbers the agents found on their own") +
      (c ? tile(c.outcome_correct, c.outcome_scored, "outcomes right with hand-checked inputs", "the calculator alone: the ceiling") : "") +
      `</div><div id="calibration" class="calibration"></div>`;
    renderCalibration();
  } catch { $("accuracy").innerHTML = ""; }
}

// Prediction vs reality: expected HR before the result (agents) vs observed HR (held-out answer key).
async function renderCalibration() {
  let rows;
  try { rows = (await getJSON("/api/calibration")).rows; } catch { return; }
  const pts = rows.filter((r) => r.predicted_hr && r.observed_hr);
  if (!pts.length) return;
  const W = 640, H = 420, L = 56, R = 18, T = 18, B = 50;
  const xlo = 0.75, xhi = 1.05, ylo = 0.75, yhi = 1.45;
  const X = (v) => L + (Math.min(xhi, Math.max(xlo, v)) - xlo) / (xhi - xlo) * (W - L - R);
  const Y = (v) => H - B - (Math.min(yhi, Math.max(ylo, v)) - ylo) / (yhi - ylo) * (H - T - B);
  let g = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Predicted versus observed hazard ratio for ${pts.length} historical trials">`;
  for (const v of [0.8, 0.9, 1.0]) g += `<line class="cal-grid" x1="${X(v)}" y1="${T}" x2="${X(v)}" y2="${H - B}"/><text class="tick" x="${X(v)}" y="${H - B + 18}" text-anchor="middle">${v.toFixed(1)}</text>`;
  for (const v of [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4]) g += `<line class="cal-grid" x1="${L}" y1="${Y(v)}" x2="${W - R}" y2="${Y(v)}"/><text class="tick" x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v.toFixed(1)}</text>`;
  g += `<line class="cal-null" x1="${L}" y1="${Y(1)}" x2="${W - R}" y2="${Y(1)}"/><text class="cal-note" x="${L + 6}" y="${Y(1) - 6}">no effect (HR 1.0)</text>`;
  g += `<line class="cal-diag" x1="${X(xlo)}" y1="${Y(xlo)}" x2="${X(xhi)}" y2="${Y(xhi)}"/>` +
    `<text class="cal-note" x="${X(0.775)}" y="${Y(0.775) - 10}" transform="rotate(${-Math.atan2(Y(xlo) - Y(xhi), X(xhi) - X(xlo)) * 180 / Math.PI} ${X(0.775)} ${Y(0.775) - 10})">prediction = reality</text>`;
  g += `<text class="tick" x="${(L + W - R) / 2}" y="${H - 8}" text-anchor="middle">expected HR, predicted before the result</text>` +
    `<text class="tick" transform="rotate(-90)" x="${-(T + H - B) / 2}" y="14" text-anchor="middle">observed HR, what the trial found</text>`;
  pts.forEach((r, i) => {
    const ok = r.actual === "success", x = X(r.predicted_hr);
    const [lo, hi] = r.ci;
    if (lo && hi) g += `<line class="cal-ci ${ok ? "c-ok" : "c-fail"}" x1="${x}" y1="${Y(lo)}" x2="${x}" y2="${Y(hi)}"/>`;
    if (["ILLUMINATE", "VISTA-16", "ACCELERATE"].includes(r.trial))
      g += `<text class="cal-lbl" x="${x - 9}" y="${Y(r.observed_hr) + 4}" text-anchor="end">${esc(r.trial)}</text>`;
    g += `<circle class="cal-pt ${ok ? "c-ok" : "c-fail"}" cx="${x}" cy="${Y(r.observed_hr)}" r="5.5" data-i="${i}"/>` +
      `<circle class="cal-hit" cx="${x}" cy="${Y(r.observed_hr)}" r="13" data-i="${i}"/>`;
  });
  g += "</svg>";
  const fmt = (r) => `${r.observed_hr}${r.ci[0] ? ` (${r.ci[0]}–${r.ci[1]})` : ""}`;
  const within = (r) => r.ci[0] && r.ci[1] && r.ci[0] <= r.predicted_hr && r.predicted_hr <= r.ci[1];
  const inRange = pts.filter(within).length;
  const missed = pts.filter((r) => r.ci[0] && r.ci[0] > r.predicted_hr + 0.02);   // clearly above the diagonal, not just touching it
  const near = pts.filter((r) => r.ci[0] && !within(r) && !missed.includes(r));
  const mark = (b) => b === true ? '<span class="ok-t">✓</span>' : b === false ? '<span class="fail-t">✗</span>' : "–";
  $("calibration").innerHTML = `<h3 class="cal-h">Prediction vs reality</h3>
    <div class="cal-two"><div class="cal-chart">${g}<div class="cal-tip hidden" id="calTip"></div>
      <div class="legend"><span><i class="lg lg-dot c-ok"></i> trial succeeded</span><span><i class="lg lg-dot c-fail"></i> trial failed</span>
      <span><i class="lg lg-diag"></i> prediction = reality</span><span><i class="lg lg-bar"></i> 95% range of the real result</span></div></div>
    <div class="cal-side">
      <p><b>Each dot is one past trial.</b> Across: the effect the agents expected before the result. Up: what the trial actually found.</p>
      <ul class="cal-keys">
        <li><b>Vertical bar</b> = the trial's 95% range: where the true effect lies. Big trials, short bars.</li>
        <li><b>Diagonal through the bar</b> = prediction consistent with reality: <b>${inRange} of ${pts.length}</b> trials.</li>
        <li><b>Bar above the diagonal</b> = the trial did worse than predicted${missed.length ? `: ${missed.map((r) => r.trial).join(", ")}` : ""}.</li>
      </ul>
      <details class="cal-more"><summary>Read the full explanation</summary><p class="home-note"><b>The vertical bar through each dot is the trial's 95% range</b>: a trial measures the true effect with some noise, and the
      true value lies somewhere on that bar. Big trials with many events give short bars (FOURIER); small or early-stopped trials give long ones (VISTA-16).
      So a prediction does not have to hit the dot exactly: <b>if the dashed diagonal passes through the bar, the prediction is consistent with what the trial found.</b>
      That is the case for ${inRange} of ${pts.length} trials.
      ${missed.length ? `The bar sits wholly above the diagonal for ${missed.map((r) => r.trial).join(", ")}: the trial did worse than predicted${missed.some((r) => r.trial === "ILLUMINATE") ? " (ILLUMINATE: torcetrapib raised blood pressure, a harm no cholesterol formula sees" : ""}${missed.some((r) => r.trial === "ACCELERATE") ? `${missed.some((r) => r.trial === "ILLUMINATE") ? "; " : " ("}ACCELERATE: stopped early, with a smaller real LDL particle reduction` : ""}${missed.some((r) => ["ILLUMINATE", "ACCELERATE"].includes(r.trial)) ? ")" : ""}.` : ""}
      ${near.length ? `${near.map((r) => r.trial).join(" and ")} sit${near.length === 1 ? "s" : ""} just above the line: the real effect was a little weaker than predicted.` : ""}</p></details>
    </div></div>
    <div class="cal-wrap"><div class="cal-table-wrap"><table class="cal-table"><thead><tr><th>Trial</th><th>Expected HR</th><th>Chance given</th><th>Observed HR</th><th>Outcome · forecast right?</th><th>Agents' cause · matches experts?</th></tr></thead>
      <tbody>${rows.map((r) => `<tr><td><a href="#/run/${esc(r.run_id)}">${esc(r.trial)}</a></td><td class="mono">${r.predicted_hr ?? "n/a"}</td>
        <td class="mono">${r.chance !== null && r.chance !== undefined ? (r.chance >= 0.995 ? ">99%" : pct(r.chance)) : "–"}</td><td class="mono">${esc(r.observed_hr ? fmt(r) : "–")}</td>
        <td title="${r.chance !== null && r.chance !== undefined ? `Agents gave a ${pct(r.chance)} chance of success before the result` : "No forecast: trial size or mechanism not computable"}">${r.actual === "success" ? "succeeded" : "failed"} ${r.prediction_correct === null || r.prediction_correct === undefined ? '<span class="muted">· no forecast</span>' : mark(r.prediction_correct)}</td>
        <td title="Expert consensus: ${esc(CATEGORY[r.gold_cause] || r.gold_cause || "")}">${mark(r.cause_overlap)} <span class="cal-cause">${esc(CATEGORY[r.cause] || r.cause || "")}</span></td></tr>`).join("")}</tbody></table></div></div>
    <div class="howto">
      <h3 class="cal-h">How to read this table</h3>
      <p class="home-note">Each row is a real trial replayed “from the past”: the agents saw only documents published before its result day.
        The last two columns grade them against what was learned later. Hover any cell for details.</p>
      <div class="howto-grid">
        <div class="howto-col"><div class="howto-k">Expected HR · Chance given</div>
          <p>The agents' answer <b>before</b> the result. <i>Expected HR</i>: how much the drug should cut heart attacks and strokes
            (0.80 = 20% fewer; 1.0 = no effect). <i>Chance given</i>: how likely a trial of this size was to prove that effect.
            “–” means no forecast was possible: the trial's planned size was not public, or the drug works through a mechanism
            with no validated formula (e.g. inflammation).</p></div>
        <div class="howto-col"><div class="howto-k">Observed HR</div>
          <p>What the trial actually found, with its 95% range. Taken from the published results, which the agents never saw.
            If the whole range is below 1.0, the benefit was proven.</p></div>
        <div class="howto-col"><div class="howto-k">Outcome · forecast right?</div>
          <p>Whether the trial succeeded or failed, and whether the agents' chance pointed the right way:
            <span class="ok-t">✓</span> above 50% and it succeeded, or below 50% and it failed; <span class="fail-t">✗</span> the other way round;
            “no forecast” when there was no chance to grade.</p>
          <p class="howto-ex">${outcomeExamples(rows, mark)}</p></div>
        <div class="howto-col"><div class="howto-k">Agents' cause · matches experts?</div>
          <p>The cause the agents named before the result, compared with the explanation experts reached later from the full data:
            <span class="ok-t">✓</span> when the consensus cause is the agents' main cause or one of their contributing causes.</p>
          <p class="howto-ex">${causeExamples(rows, mark)}</p></div>
      </div>
      <p class="howto-sum">Scores on the home page come straight from this table: outcomes ${rows.filter((r) => r.prediction_correct === true).length} of
        ${rows.filter((r) => r.prediction_correct === true || r.prediction_correct === false).length} forecasts right,
        causes ${rows.filter((r) => r.cause_overlap).length} of ${rows.length} matching the experts. The misses are shown, not hidden.</p>
    </div>`;
  const tip = $("calTip"), chart = document.querySelector(".cal-chart");
  document.querySelectorAll(".cal-hit").forEach((h) => {
    h.addEventListener("mouseenter", () => {
      const r = pts[Number(h.dataset.i)], box = chart.getBoundingClientRect(), c = h.getBoundingClientRect();
      tip.innerHTML = `<b>${esc(r.trial)}</b><br>expected ${r.predicted_hr}${r.chance !== null && r.chance !== undefined ? ` · ${r.chance >= 0.995 ? ">99%" : pct(r.chance)} chance` : ""}<br>observed ${esc(fmt(r))} · ${r.actual === "success" ? "succeeded" : "failed"}<br>${r.ci[0] && r.ci[0] <= r.predicted_hr && r.predicted_hr <= r.ci[1] ? "prediction inside the 95% range" : "prediction outside the 95% range"}`;
      tip.style.left = `${c.left - box.left + c.width / 2}px`; tip.style.top = `${c.top - box.top}px`;
      tip.classList.remove("hidden");
    });
    h.addEventListener("mouseleave", () => tip.classList.add("hidden"));
    h.addEventListener("click", () => { location.hash = `#/run/${pts[Number(h.dataset.i)].run_id}`; });
  });
}

/* ---------- settings ---------- */

let settingsData = null;
const pending = { sources: {}, providers: {}, agents: {}, run: {}, zetaris: {} };
const MODEL_LABEL = { "claude-fable-5-1": "Claude Fable 5.1", "claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
  "claude-haiku-4-5": "Claude Haiku 4.5", "nvidia/llama-3.3-nemotron-super-49b-v1": "Nemotron Super 49B",
  "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super", "nvidia/nemotron-3.5-super-vl-120b-a12b": "Nemotron 3.5 Super", "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron 3 Ultra",
  "nvidia/nemotron-3.5-lightning-30b-a3b": "Nemotron 3.5 Lightning",
  "meterless/deep": "Nightshift · deep route", "meterless/fast": "Nightshift · fast route",
  "meterless/auto": "Nightshift · auto route" };
const modelName = (m) => MODEL_LABEL[m] || m;

async function loadSettings() {
  try { settingsData = await getJSON("/api/settings"); } catch { $("setSources").textContent = "Settings unavailable."; return; }
  for (const k of Object.keys(pending)) pending[k] = {};
  delete pending.apply_recommended;
  $("setStatus").textContent = "";
  renderSettings();
  if (settingsData.readonly) lockSettings();
}

// The public demo (DEMO_READONLY): everything stays visible, nothing can be changed or tested.
function lockSettings() {
  const view = $("settingsView");
  if (!view.querySelector(".set-readonly")) {
    view.querySelector(".settings-head").insertAdjacentHTML("beforeend", `<p class="set-readonly">This public demo is read-only:
      settings cannot be saved or tested here. <a href="https://github.com/stas-gatin/trial-autopsy#quick-start">Run it locally</a> to change them.</p>`);
  }
  view.querySelectorAll("input, select, textarea, button").forEach((el) => { el.disabled = true; });
}

function renderSettings() {
  const d = settingsData;
  $("setSources").innerHTML = d.sources.map((s) => `
    <div class="set-card ${s.coming_soon ? "soon" : ""}">
      <div class="set-row">
        <div><div class="set-name">${esc(s.label)}${s.required ? ' <span class="pill">required</span>' : ""}${s.coming_soon ? ' <span class="pill soon">coming soon</span>' : ""}</div>
          <div class="set-desc">${esc(s.role)}</div></div>
        <label class="switch"><input type="checkbox" data-src="${esc(s.id)}" ${s.enabled ? "checked" : ""} ${s.required || s.coming_soon ? "disabled" : ""}><span></span></label>
      </div>
      ${s.id === "zetaris" ? zetarisFields(d.zetaris) : ""}
      ${s.coming_soon ? "" : `<div class="set-test"><button type="button" class="btn-small" data-test-src="${esc(s.id)}">Test connection</button>${s.id === "zetaris" && d.zetaris.configured ? '<button type="button" class="btn-small danger" data-zclear>Remove key</button>' : ""}<span class="test-out" id="ts-${esc(s.id)}"></span></div>`}
    </div>`).join("");

  $("setProviders").innerHTML = d.providers.map((p) => `
    <div class="set-card">
      <div class="set-row"><div><div class="set-name">${esc(p.label)}</div>
        <div class="set-desc">${p.configured ? `<span class="ok-dot"></span> key saved <span class="mono">${esc(p.key_hint || "")}</span>` : '<span class="no-dot"></span> no key'}
        ${p.docs ? ` · <a href="${esc(p.docs)}" target="_blank" rel="noopener">get a key</a>` : ""}</div></div></div>
      <label class="field"><span>${esc(p.key_label || "API key")}</span>
        <input type="password" autocomplete="new-password" data-1p-ignore data-lpignore="true" data-bwignore spellcheck="false" data-key="${esc(p.id)}" ${p.configured ? savedKeyAttrs(p.key_hint) : `placeholder="${esc(p.key_placeholder || "paste your key")}"`}></label>
      ${p.needs_base_url ? `<label class="field"><span>Base URL</span><input data-base="${esc(p.id)}" value="${esc(p.base_url || "")}" placeholder="${esc(p.base_placeholder || "https://…/v1")}"></label>` : ""}
      <div class="set-test">
        ${p.detect ? `<button type="button" class="btn-small" data-detect="${esc(p.id)}">Auto Detect</button>` : ""}
        <button type="button" class="btn-small" data-test-prov="${esc(p.id)}">${p.id === "meterless" ? "Test connection" : "Test key"}</button>
        ${p.configured ? `<button type="button" class="btn-small danger" data-clear="${esc(p.id)}">Remove key</button>` : ""}
        <span class="test-out" id="tp-${esc(p.id)}"></span>
      </div>
    </div>`).join("");

  renderAgents();
  renderSystemMap();

  $("setRun").innerHTML = Object.entries(d.run).map(([key, o]) => `
    <div class="set-card"><div class="set-row"><div><div class="set-name">${esc(o.label)}</div><div class="set-desc">${esc(o.help)}</div></div>
      ${o.type === "bool" ? `<label class="switch"><input type="checkbox" data-run="${esc(key)}" ${o.value ? "checked" : ""}><span></span></label>` : ""}
    </div>
    ${o.type === "int" ? `<input type="number" data-run="${esc(key)}" min="${o.min}" max="${o.max}" value="${esc(o.value)}" class="num-input">` : ""}
    ${o.type === "str" ? `<input type="email" data-run="${esc(key)}" value="${esc(o.value)}" placeholder="you@example.com">` : ""}
    </div>`).join("");

  const mark = () => { $("setStatus").textContent = "Unsaved changes"; renderSystemMap(); };
  document.querySelectorAll("[data-src]").forEach((el) => el.addEventListener("change", () => { pending.sources[el.dataset.src] = el.checked; mark(); }));
  document.querySelectorAll("[data-key]").forEach((el) => el.addEventListener("input", () => { (pending.providers[el.dataset.key] ||= {}).key = el.value; mark(); }));
  document.querySelectorAll("[data-base]").forEach((el) => el.addEventListener("input", () => { (pending.providers[el.dataset.base] ||= {}).base_url = el.value; mark(); }));

  document.querySelectorAll("[data-z]").forEach((el) => el.addEventListener("input", () => { pending.zetaris[el.dataset.z] = el.value; mark(); }));
  document.querySelectorAll("[data-zclear]").forEach((el) => el.addEventListener("click", async () => {
    pending.zetaris = { clear_key: true }; await saveSettings();
  }));
  document.querySelectorAll("[data-run]").forEach((el) => el.addEventListener("change", () => {
    pending.run[el.dataset.run] = el.type === "checkbox" ? el.checked : el.value; mark();
  }));
  document.querySelectorAll("[data-clear]").forEach((el) => el.addEventListener("click", async () => {
    pending.providers[el.dataset.clear] = { clear_key: true }; await saveSettings();
  }));
  document.querySelectorAll("[data-test-src]").forEach((el) => el.addEventListener("click", async () => {
    const id = el.dataset.testSrc;
    if (id === "zetaris" && Object.keys(pending.zetaris).length) await saveSettings();
    runTest(`/api/settings/test/source/${id}`, `ts-${id}`);
  }));
  document.querySelectorAll("[data-detect]").forEach((el) => el.addEventListener("click", async () => {
    if (Object.keys(pending.providers).length) await saveSettings();
    const out = $(`tp-${el.dataset.detect}`);
    out.className = "test-out"; out.textContent = "searching ports 8088, 8090, 8787, 11434…";
    try {
      const r = await getJSON("/api/settings/meterless/detect", { method: "POST" });
      out.className = `test-out ${r.ok ? "ok" : "bad"}`; out.textContent = r.message;
      if (r.url) { settingsData = await getJSON("/api/settings"); const b = document.querySelector('[data-base="meterless"]'); if (b) b.value = r.url; }
    } catch (e) { out.className = "test-out bad"; out.textContent = e.message; }
  }));
  document.querySelectorAll("[data-test-prov]").forEach((el) => el.addEventListener("click", async () => {
    if (Object.keys(pending.providers).length) await saveSettings();
    const id = el.dataset.testProv;
    const agent = settingsData.agents.find((a) => a.kind === "ai" && a.provider === id);
    const model = agent ? agent.model : "";
    runTest(`/api/settings/test/provider/${id}${model ? `?model=${encodeURIComponent(model)}` : ""}`, `tp-${id}`);
  }));
  // A browser may fill a remembered password (often an old key) into the key fields. Keep only what the user
  // typed or pasted: anything else is cleared, and so is the pending change it caused.
  const keyFields = document.querySelectorAll('input[type="password"][data-key], input[type="password"][data-z="key"]');
  keyFields.forEach((el) => ["keydown", "paste", "drop"].forEach((ev) => el.addEventListener(ev, () => { el.dataset.typed = "1"; })));
  setTimeout(() => keyFields.forEach((el) => {
    if (el.dataset.typed || !el.value) return;
    el.value = "";
    if (el.dataset.z) delete pending.zetaris.key;
    else if (pending.providers[el.dataset.key]) delete pending.providers[el.dataset.key].key;
  }), 800);
}

// A saved key is shown the way password fields usually show one: dots and its last characters. The field itself
// stays empty, so nothing is sent unless a new key is pasted over it.
function savedKeyAttrs(hint) {
  const tail = String(hint || "").replace(/^[•\s]+/, "");
  return `class="key-saved" placeholder="${"•".repeat(16)}${tail ? ` ${esc(tail)}` : ""}   saved · paste a new key to replace" title="A key is saved${tail ? ` (ends ${esc(tail)})` : ""}. Paste a new key to replace it."`;
}

function zetarisFields(z) {
  const state = z.active ? '<span class="ok-dot"></span> switched on: sources are read through Zetaris (Test connection checks the key)'
    : z.configured ? '<span class="no-dot"></span> configured, switched off' : '<span class="no-dot"></span> not configured: sources are read directly';
  return `<div class="set-desc z-state">${state}${z.key_hint ? ` · key <span class="mono">${esc(z.key_hint)}</span>` : ""}</div>
    <label class="field"><span>API URL</span><input data-z="url" value="${esc(z.url || "")}" placeholder="http://localhost:8888/api/v1.0" spellcheck="false"></label>
    <label class="field"><span>Organisation ID</span><input data-z="org" value="${esc(z.org || "")}" placeholder="Management → Organisation → ID" inputmode="numeric"></label>
    <label class="field"><span>API key</span><input type="password" autocomplete="new-password" data-1p-ignore data-lpignore="true" data-bwignore spellcheck="false" data-z="key" ${z.key_hint ? savedKeyAttrs(z.key_hint) : 'placeholder="paste your key" title="Create it in Zetaris: Management → API Tokens → +"'}></label>`;
}

function renderAgents() {
  const d = settingsData;
  const provOptions = (sel) => d.providers.map((p) => `<option value="${esc(p.id)}" ${p.id === sel ? "selected" : ""}>${esc(p.label)}</option>`).join("") +
    `<option value="replay" ${sel === "replay" ? "selected" : ""}>Recorded outputs only</option>`;
  const routes = (d.providers.find((p) => p.id === "meterless") || { models: [] }).models;
  let step = 0;
  $("setAgents").innerHTML = d.agents.map((a) => {
    step += 1;
    if (a.kind === "code") {
      return `<li class="ag-card code"><span class="ag-n">${step}</span><div class="ag-main"><div class="set-name">${esc(a.name)} <span class="pill">code</span></div>
        <div class="set-desc">${esc(a.does)}</div></div><div class="ag-side muted">Runs as code: no model needed</div></li>`;
    }
    // unsaved changes show as they will be saved
    const p = pending.agents[a.name], cur = p && !p.reset && p.provider ? { ...a, provider: p.provider, model: p.model } : a;
    const viaNightshift = cur.provider === "meterless";
    const models = (d.providers.find((x) => x.id === cur.provider) || { models: [] }).models;
    const info = cur.model === a.model && a.model_info ? `${a.model_info.tier} · ${a.model_info.price}` : "custom model";
    const rec = a.recommended, alt = a.alternative;
    // Through Nightshift the app chooses a routing mode and Nightshift picks the Nemotron lane that answers
    const routeList = routes.includes(cur.model) ? routes : [cur.model, ...routes];
    const modelField = viaNightshift
      ? `<label class="field"><span>Routing mode · Nightshift picks the lane</span><select data-ag-model="${esc(a.name)}">${routeList.map((m) =>
          `<option value="${esc(m)}" ${m === cur.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></label>`
      : `<label class="field"><span>Model</span><input list="ag-models-${esc(a.name)}" data-ag-model="${esc(a.name)}" value="${esc(cur.model)}" spellcheck="false"></label>
        <datalist id="ag-models-${esc(a.name)}">${models.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>`;
    return `<li class="ag-card ai tier-${esc(a.tier)}"><span class="ag-n">${step}</span>
      <div class="ag-main">
        <div class="set-name">${providerOf(`${cur.provider}:${cur.model}`) ? logoImg(providerOf(`${cur.provider}:${cur.model}`), 16) + " " : ""}${esc(a.name)} <span class="pill ai">AI</span> ${a.live ? '<span class="pill live">live</span>' : '<span class="pill">recorded</span>'}</div>
        <div class="set-desc">${esc(a.does)}</div>
        <div class="ag-tier"><span class="tier-chip t-${esc(a.tier)}">${esc(a.tier_label)}</span></div>
        <div class="ag-why">${esc(a.why)}</div>
        <div class="ag-rec">Recommended: <b>${esc(modelName(rec.model))}</b> <span class="muted">${esc(rec.tier || "")}${rec.price ? " · " + esc(rec.price) : ""}</span>
          ${a.is_recommended && cur === a ? '<span class="rec-ok">✓ in use</span>' : `<button type="button" class="btn-small" data-use="${esc(a.name)}" data-p="${esc(rec.provider)}" data-m="${esc(rec.model)}">Use it</button>`}
          <span class="ag-alt">or <button type="button" class="link-btn" data-use="${esc(a.name)}" data-p="${esc(alt.provider)}" data-m="${esc(alt.model)}">${esc(modelName(alt.model))}</button> <span class="muted">${esc(alt.price || "")}</span></span>
        </div>
      </div>
      <div class="ag-side">
        <label class="field"><span>Provider</span><select data-ag-prov="${esc(a.name)}">${provOptions(cur.provider)}</select></label>
        ${modelField}
        <div class="ag-info">${viaNightshift ? "Nightshift serves the request from a Nemotron lane; its Apps panel can bind the whole app to one mode" : esc(info)}${a.own_setting ? ` · <button type="button" class="link-btn" data-reset="${esc(a.name)}">reset to default</button>` : ""}</div>
      </div></li>`;
  }).join("");

  const mark = () => { $("setStatus").textContent = "Unsaved changes"; renderSystemMap(); };
  const choose = (name, provider, model) => { pending.agents[name] = { provider, model }; renderAgents(); mark(); };
  document.querySelectorAll("[data-ag-prov]").forEach((el) => el.addEventListener("change", () => {
    // a new provider starts from its recommended routing mode (Nightshift) or its first model
    const name = el.dataset.agProv, agent = settingsData.agents.find((x) => x.name === name);
    const model = el.value === "meterless"
      ? (agent && agent.recommended.provider === "meterless" ? agent.recommended.model : "meterless/fast")
      : el.value === agent.provider && agent.provider !== "meterless" ? agent.model
      : ((settingsData.providers.find((x) => x.id === el.value) || {}).models || [""])[0] || "";
    choose(name, el.value, model);
  }));
  document.querySelectorAll("[data-ag-model]").forEach((el) => el.addEventListener("change", () => {
    const name = el.dataset.agModel;
    pending.agents[name] = { provider: document.querySelector(`[data-ag-prov="${name}"]`).value, model: el.value };
    mark();
  }));
  document.querySelectorAll("[data-use]").forEach((el) => el.addEventListener("click", () => choose(el.dataset.use, el.dataset.p, el.dataset.m)));
  document.querySelectorAll("[data-reset]").forEach((el) => el.addEventListener("click", async () => {
    pending.agents[el.dataset.reset] = { reset: true }; await saveSettings();
  }));
}


/* ---------- system map on the Settings page: data, agents, World Model, model runtime ---------- */

// The pipeline as the report's agent graph shows it; the case builder stands for Genetics, Literature and Extraction.
const MAP_AGENTS = [
  { id: "Intake", label: "Intake", zetaris: true, ot: true },
  { id: "CaseBuilder", label: "Case builder", agent: "Extraction", zetaris: true, ot: true, wm: "write",
    parts: ["Genetics", "Literature", "Extraction"] },
  { id: "MechanismAnalyst", label: "Mechanism analyst", agent: "MechanismAnalyst", wm: "read" },
  { id: "Calculator", label: "Calculator", wm: "read" },
  { id: "Verifier", label: "Verifier", agent: "Verifier", zetaris: "sql", wm: "read" },
  { id: "Investigator", label: "Investigator", agent: "Investigator", wm: "read" },
  { id: "Critic", label: "Critic", agent: "Critic", parts: ["Critic", "Refine"] },
  { id: "ReadThrough", label: "Read-through", agent: "ReadThrough", zetaris: true, ot: true },
  { id: "Reporter", label: "Reporter", agent: "Reporter" },
];
const MAP_SOURCES = [
  { id: "ctgov", label: "ClinicalTrials.gov", sub: "trial registry" },
  { id: "europepmc", label: "Europe PMC", sub: "papers · genetics papers" },
  { id: "sec", label: "SEC EDGAR", sub: "company filings" },
];
const MAP_RUNTIME = {
  meterless: { label: "Meterless Nightshift", sub: ["model runtime on this machine", "deep and fast routes · keys stay here"], logo: "meterless", cls: "rt-ms" },
  nvidia: { label: "NVIDIA Nemotron", sub: ["3 Ultra 550B · deep reasoning", "3.5 Super 120B · fast work"], logo: "nvidia", cls: "rt-nv" },
  anthropic: { label: "Claude (Anthropic)", sub: ["closed model, optional"], logo: "anthropic", cls: "rt-other" },
  openai: { label: "OpenAI-compatible API", sub: ["custom endpoint"], cls: "rt-other" },
  replay: { label: "Recorded outputs", sub: ["no live model: replays answers"], cls: "rt-other" },
};
const MAP_INFO = {
  ctgov: "The trial registry: design, size, endpoints, status. Intake reads the trial record, Read-through finds rival phase 3 trials, and the Verifier checks numbers against the record.",
  europepmc: "The literature: phase 2 results, trial design papers, open full texts and genetics papers published before the cut-off. The case builder and Read-through read it; the Verifier checks numbers against the cited abstracts.",
  sec: "Company filings (8-K / 6-K): the case builder finds the sponsor's result announcement when you do not paste one.",
  opentargets: "Human genetics for the drug's target, the drug's mechanism and rival programmes on the same target. A GraphQL API, read directly rather than through Zetaris.",
  zetaris: "The data layer. Each request to the registry, the literature or the filings becomes a Zetaris REST session table read with Lightning SQL, and the literature date cut-off runs inside the query, so a paper from after the cut-off never reaches an agent. The Verifier has Nemotron write SQL that Zetaris runs over the registry and the papers.",
  wm: "The memory of a run. The case builder writes every source, entity and fact with its provenance: the agent, the run, the source URL and the publication date. Each write goes to the app's World Model store, built on the three services the Meterless guides describe, which the analyst, the calculator, the Verifier and the investigator build their context from, and to the official Meterless World Model engine running on this machine, which keeps the audited, event-sourced record.",
  meterless: "The model runtime. The app asks Nightshift for a route, deep or fast, and Nightshift picks the NVIDIA Nemotron lane that answers. The provider keys stay inside Nightshift. If Nightshift cannot be reached, the call goes straight to NVIDIA's API and the run trace says so.",
  nvidia: "Open models behind the AI agents: Nemotron 3 Ultra for the deep reasoning, Nemotron 3.5 Super for the fast work. Because they are open, the same agents can run on a company's own hardware, such as an NVIDIA DGX Spark.",
  anthropic: "Claude, a closed model from Anthropic. Optional: an agent uses it only if you choose it below.",
  openai: "Any OpenAI-compatible endpoint, set in the provider card above.",
  replay: "No live model: the agent replays answers recorded earlier. Enough for the recorded examples.",
};
let mapPinned = null;

function mapState() {
  const d = settingsData;
  const src = {};
  for (const s of d.sources) src[s.id] = s.id in pending.sources ? pending.sources[s.id] : s.enabled;
  const zetarisOn = src.zetaris !== false && (d.zetaris.active || ("zetaris" in pending.sources && pending.sources.zetaris && d.zetaris.configured));
  const models = {};
  for (const a of d.agents) {
    if (a.kind !== "ai") continue;
    const p = pending.agents[a.name];
    models[a.name] = p && !p.reset && p.provider ? { provider: p.provider, model: p.model } : { provider: a.provider, model: a.model };
  }
  return { src, zetarisOn, wmOn: src.meterless !== false, models };
}

function mapModelLine(m) {
  if (!m) return "code · no model";
  if (m.provider === "meterless") return `Nightshift · ${m.model.replace("meterless/", "")} route`;
  if (m.provider === "replay") return "recorded outputs";
  return modelName(m.model);
}

function renderSystemMap() {
  const el = $("sysMap");
  if (!el || !settingsData) return;
  const st = mapState();
  const AX = 500, AW = 220, AH = 46, AY = 30, STEP = 62, CX = AX + AW / 2;
  const cy = (i) => AY + i * STEP + AH / 2;
  const idx = Object.fromEntries(MAP_AGENTS.map((a, i) => [a.id, i]));
  const svgImg = (key, x, y, h = 16) => { const l = LOGO[key]; return l ? `<image href="${l.src}" x="${x}" y="${y}" width="${l.w * h / 16}" height="${h}"/>` : ""; };
  let edges = "", blocks = "";
  const edge = (cls, d, ids, extra = "") => { edges += `<path class="sm-edge ${cls}" d="${d}" data-l="${ids}" ${extra}/>`; };

  // providers in use, in a fixed order, each with its own rail
  const used = ["meterless", "nvidia", "anthropic", "openai", "replay"].filter((p) => Object.values(st.models).some((m) => m.provider === p));
  const boxes = { meterless: 300, nvidia: 412 };
  let nextY = 524;
  for (const p of used) if (!(p in boxes)) { boxes[p] = nextY; nextY += 88; }
  const height = Math.max(600, nextY);

  // data sources -> Zetaris
  MAP_SOURCES.forEach((s, i) => {
    const y = 30 + i * 70, on = st.src[s.id] !== false;
    blocks += `<g class="sm-block sm-src ${on ? "" : "is-off"}" data-id="${s.id}" tabindex="0" transform="translate(20,${y})"><rect width="180" height="54" rx="9"/>` +
      `<text class="sm-t" x="12" y="22">${esc(s.label)}</text><text class="sm-s" x="12" y="40">${on ? esc(s.sub) : "switched off"}</text></g>`;
    edge("e-z", `M200 ${y + 27} L250 ${y + 27}`, `${s.id} zetaris`, 'marker-end="url(#sm-a-z)"');
  });
  blocks += `<g class="sm-block sm-zetaris ${st.zetarisOn ? "" : "is-off"}" data-id="zetaris" tabindex="0" transform="translate(250,30)"><rect width="170" height="194" rx="11"/>` +
    svgImg("zetaris", 12, 12, 20) + `<text class="sm-t" x="40" y="27">Zetaris</text>` +
    `<text class="sm-s" x="12" y="54">data layer · Lightning SQL</text><text class="sm-s" x="12" y="72">REST session tables</text>` +
    `<text class="sm-s" x="12" y="90">date cut-off inside the query</text><text class="sm-s" x="12" y="108">checks numbers for the Verifier</text>` +
    `<text class="sm-state" x="12" y="176">${st.zetarisOn ? "● on" : "○ off: sources read directly"}</text></g>`;
  const otOn = st.src.opentargets !== false;
  blocks += `<g class="sm-block sm-src ${otOn ? "" : "is-off"}" data-id="opentargets" tabindex="0" transform="translate(250,300)"><rect width="170" height="56" rx="9"/>` +
    `<text class="sm-t" x="12" y="22">Open Targets</text><text class="sm-s" x="12" y="40">${otOn ? "genetics · direct GraphQL" : "switched off"}</text></g>`;

  // Zetaris and Open Targets -> agents
  const zOut = { Intake: 60, CaseBuilder: 104, Verifier: 150, ReadThrough: 196 };
  for (const a of MAP_AGENTS) {
    const y = cy(idx[a.id]);
    if (a.zetaris) {
      const zy = zOut[a.id], sql = a.zetaris === "sql";
      edge(`e-z ${sql ? "e-sql" : ""} ${sql && !st.zetarisOn ? "is-broken" : ""}`, `M420 ${zy} C 462 ${zy}, 458 ${y - 7}, 500 ${y - 7}`, `zetaris ${a.id}`,
        `marker-end="url(#sm-a-z)" ${sql ? 'marker-start="url(#sm-a-z-s)"' : ""}`);
      if (sql) edges += `<text class="sm-elabel" x="460" y="${(zy + y) / 2 + 4}" text-anchor="middle">SQL</text>`;
    }
    if (a.ot) edge("e-ot", `M420 328 C 462 328, 458 ${y + 7}, 500 ${y + 7}`, `opentargets ${a.id}`, 'marker-end="url(#sm-a-ot)"');
  }

  // agents, top to bottom, with the critic's loop back
  MAP_AGENTS.forEach((a, i) => {
    if (i) edge("e-flow", `M${CX} ${AY + (i - 1) * STEP + AH} L${CX} ${AY + i * STEP}`, `${MAP_AGENTS[i - 1].id} ${a.id}`, 'marker-end="url(#sm-a-flow)"');
  });
  edge("e-back", `M${AX + AW} ${cy(idx.Critic)} C ${AX + AW + 40} ${cy(idx.Critic)}, ${AX + AW + 40} ${cy(idx.CaseBuilder)}, ${AX + AW} ${cy(idx.CaseBuilder)}`,
    "Critic CaseBuilder", 'marker-end="url(#sm-a-back)"');
  edges += `<text class="sm-elabel sm-back-l" transform="translate(${AX + AW + 36},${(cy(idx.Critic) + cy(idx.CaseBuilder)) / 2}) rotate(-90)" text-anchor="middle">critic sends work back</text>`;

  MAP_AGENTS.forEach((a, i) => {
    const m = a.agent ? st.models[a.agent] : null, y = AY + i * STEP;
    const logos = [];
    if (a.zetaris && st.zetarisOn) logos.push("zetaris");
    if ((a.wm && st.wmOn) || (m && m.provider === "meterless")) logos.push("meterless");
    if (m && (m.provider === "meterless" || m.provider === "nvidia" || /nemotron/i.test(m.model))) logos.push("nvidia");
    if (m && m.provider === "anthropic") logos.push("anthropic");
    let lx = AW - 10, lg = "";
    for (const k of logos.reverse()) { lx -= LOGO[k].w; lg += svgImg(k, lx, 8); lx -= 5; }
    blocks += `<g class="sm-block sm-agent ${m ? "is-ai" : "is-code"}" data-id="${a.id}" tabindex="0" transform="translate(${AX},${y})"><rect width="${AW}" height="${AH}" rx="10"/>` +
      `<text class="sm-t" x="12" y="20">${esc(a.label)}</text><text class="sm-s" x="12" y="37">${esc(a.id === "CaseBuilder" ? "code + " + mapModelLine(m) : mapModelLine(m))}</text>${lg}</g>`;
  });

  // World Model: the case builder writes, four agents read
  blocks += `<g class="sm-block sm-wm ${st.wmOn ? "" : "is-off"}" data-id="wm" tabindex="0" transform="translate(860,30)"><rect width="280" height="190" rx="11"/>` +
    svgImg("meterless", 12, 12, 20) + `<text class="sm-t" x="40" y="27">Meterless World Model</text>` +
    `<text class="sm-s" x="12" y="54">official engine · local service</text><text class="sm-s" x="12" y="72">sources, entities, facts with provenance</text>` +
    `<text class="sm-s" x="12" y="90">event-sourced · audited · idempotent</text><text class="sm-s" x="12" y="108">facts dated by publication</text>` +
    `<text class="sm-state" x="12" y="172">${st.wmOn ? "● on" : "○ off: facts kept in the app only"}</text></g>`;
  let wmY = 50;
  for (const a of MAP_AGENTS.filter((x) => x.wm)) {
    const y = cy(idx[a.id]) - 8;
    if (a.wm === "write") edge("e-wm", `M${AX + AW} ${y} C 800 ${y}, 800 ${wmY}, 860 ${wmY}`, `${a.id} wm`, 'marker-end="url(#sm-a-wm)"');
    else edge("e-wm", `M860 ${wmY} C 800 ${wmY}, 800 ${y}, ${AX + AW} ${y}`, `wm ${a.id}`, 'marker-end="url(#sm-a-wm)"');
    wmY += 34;
  }

  // model runtime: one rail per provider in use, a stub from each AI agent
  used.forEach((p, k) => {
    const rx = 772 + k * 14, by = boxes[p] + 40;
    const nodes = MAP_AGENTS.filter((a) => a.agent && st.models[a.agent] && st.models[a.agent].provider === p);
    const ys = nodes.map((a) => cy(idx[a.id]) + 8);
    for (const [j, a] of nodes.entries()) {
      edge(`e-model m-${p}`, `M${AX + AW} ${ys[j]} L${rx} ${ys[j]}`, `${a.id} ${p}`);
      edges += `<circle class="sm-joint m-${p}" cx="${rx}" cy="${ys[j]}" r="3" data-l="${a.id} ${p}"/>`;
    }
    edge(`e-model m-${p} is-bus`, `M${rx} ${Math.min(...ys, by)} L${rx} ${Math.max(...ys, by)} M${rx} ${by} L860 ${by}`, `${p} ${nodes.map((a) => a.id).join(" ")}`,
      'marker-end="url(#sm-a-model)"');
  });
  for (const p of ["meterless", "nvidia", ...used.filter((x) => x !== "meterless" && x !== "nvidia")]) {
    const r = MAP_RUNTIME[p], y = boxes[p], inUse = used.includes(p);
    blocks += `<g class="sm-block sm-rt ${r.cls} ${inUse || p === "nvidia" ? "" : "is-idle"}" data-id="${p}" tabindex="0" transform="translate(860,${y})"><rect width="280" height="80" rx="11"/>` +
      (r.logo ? svgImg(r.logo, 12, 12, 20) : "") + `<text class="sm-t" x="${r.logo ? (p === "nvidia" ? 50 : 40) : 12}" y="27">${esc(r.label)}</text>` +
      r.sub.map((s, i) => `<text class="sm-s" x="12" y="${50 + i * 17}">${esc(s)}</text>`).join("") + "</g>";
  }
  edge("e-lane", `M1000 380 L1000 412`, "meterless nvidia", 'marker-end="url(#sm-a-lane)"');
  edges += `<text class="sm-elabel" x="1008" y="400">picks the lane</text>`;

  const mk = (id, color, start) => `<marker id="${id}" viewBox="0 0 10 10" refX="${start ? 1 : 9}" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="${start ? "M10,0 L0,5 L10,10 z" : "M0,0 L10,5 L0,10 z"}" fill="${color}"/></marker>`;
  el.innerHTML = `<svg viewBox="0 0 1160 ${height}" role="img" aria-label="System map: data sources, agents, World Model and model runtime"><defs>` +
    mk("sm-a-z", "#4554e7") + mk("sm-a-z-s", "#4554e7", true) + mk("sm-a-ot", "#8794a8") + mk("sm-a-flow", "#9aa8b8") + mk("sm-a-back", "#a15c07") +
    mk("sm-a-wm", "#0b6e79") + mk("sm-a-model", "#5a8f00") + mk("sm-a-lane", "#5a8f00") + `</defs><g class="sm-edges">${edges}</g>${blocks}</svg>`;

  const svg = el.querySelector("svg");
  const focus = (id) => {
    svg.classList.toggle("is-focus", !!id);
    const linked = new Set(id ? [id] : []);
    svg.querySelectorAll("[data-l]").forEach((e) => {
      const ids = e.dataset.l.split(" "), on = !!id && ids.includes(id);
      e.classList.toggle("hl", on);
      if (on && !e.classList.contains("is-bus")) ids.forEach((x) => linked.add(x));
      if (on && e.classList.contains("is-bus") && ids[0] === id) ids.forEach((x) => linked.add(x));
    });
    svg.querySelectorAll(".sm-block").forEach((b) => b.classList.toggle("hl", linked.has(b.dataset.id)));
    svg.querySelectorAll(".sm-block").forEach((b) => b.classList.toggle("is-pinned", b.dataset.id === mapPinned));
    $("sysMapInfo").innerHTML = mapInfo(id, st);
  };
  svg.querySelectorAll(".sm-block").forEach((b) => {
    b.addEventListener("mouseenter", () => focus(b.dataset.id));
    b.addEventListener("mouseleave", () => focus(mapPinned));
    b.addEventListener("focus", () => focus(b.dataset.id));
    b.addEventListener("click", () => { mapPinned = mapPinned === b.dataset.id ? null : b.dataset.id; focus(mapPinned); });
    b.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); b.dispatchEvent(new Event("click")); } });
  });
  svg.addEventListener("mouseleave", () => focus(mapPinned));
  focus(mapPinned);
}

function mapInfo(id, st) {
  const d = settingsData, chip = (k) => logoImg(k, 15);
  const ai = Object.entries(st.models);
  if (!id) {
    const viaNs = ai.filter(([, m]) => m.provider === "meterless").length;
    return `<div class="smi-title">How a question moves through the system</div>
      <p>Data comes in on the left: the registry, the literature and the filings through ${chip("zetaris")} Zetaris, the genetics straight from Open Targets.
      Nine steps work through it in order, and the critic can send the work back. Every fact goes into the ${chip("meterless")} Meterless World Model.
      Every AI step runs on ${chip("nvidia")} NVIDIA Nemotron${viaNs ? `, served through ${chip("meterless")} Meterless Nightshift` : ""}.</p>
      <p class="smi-now">Now: ${viaNs} of ${ai.length} AI agents through Nightshift · Zetaris ${st.zetarisOn ? "on" : "off"} · World Model ${st.wmOn ? "on" : "off"}.
      The map follows the settings below, including unsaved changes. Point at a block to see what it does and what it connects to; click to keep it selected.</p>`;
  }
  const a = MAP_AGENTS.find((x) => x.id === id);
  if (a) {
    const parts = (a.parts || [a.id]).map((n) => d.agents.find((x) => x.name === n)).filter(Boolean);
    const m = a.agent ? st.models[a.agent] : null;
    const reads = [];
    if (a.id === "Intake") reads.push("ClinicalTrials.gov (the trial record)", "Open Targets (the drug)");
    if (a.id === "CaseBuilder") reads.push("Europe PMC (papers, genetics papers)", "SEC EDGAR (filings)", "Open Targets (genetics)");
    if (a.id === "ReadThrough") reads.push("Open Targets (rival programmes)", "ClinicalTrials.gov (phase 3 trials)", "Europe PMC (phase 2 results)");
    if (a.id === "Verifier") reads.push("ClinicalTrials.gov and Europe PMC as Zetaris tables, with SQL written by the model");
    if (a.wm === "read") reads.push("the Meterless World Model (its context)");
    if (a.id === "Critic") reads.push("the whole analysis so far");
    if (a.id === "Reporter") reads.push("the finished, verified analysis");
    const rows = [];
    if (reads.length) rows.push(`<dt>Reads</dt><dd>${esc(reads.join("; "))}${a.zetaris && a.id !== "Verifier" ? (st.zetarisOn ? " · through Zetaris" : " · directly (Zetaris is off)") : ""}</dd>`);
    if (a.wm === "write") rows.push(`<dt>Writes</dt><dd>every source, entity and fact to the Meterless World Model${st.wmOn ? "" : " (switched off)"}</dd>`);
    rows.push(`<dt>Runs on</dt><dd>${m ? esc(m.provider === "meterless" ? `Meterless Nightshift, ${m.model.replace("meterless/", "")} route: Nightshift picks the Nemotron lane` : `${modelName(m.model)} (${(d.providers.find((p) => p.id === m.provider) || { label: m.provider }).label})`) : "code: no model"}${a.id === "CaseBuilder" ? " (the extraction step); genetics and literature search are code" : ""}</dd>`);
    if (a.id === "Critic") rows.push(`<dt>Loop</dt><dd>a serious problem sends the work back: Refine searches for the evidence asked for, and the analysis runs again, up to ${esc((d.run.MAX_CRITIC_ROUNDS || {}).value || 2)} rounds</dd>`);
    if (a.id === "Verifier") rows.push(`<dt>Without Zetaris</dt><dd>it cannot run; the report then says its numbers were not independently verified</dd>`);
    return `<div class="smi-title">${esc(a.label)}${a.parts && a.id === "CaseBuilder" ? ' <span class="muted">Genetics · Literature · Extraction</span>' : ""}</div>
      <p>${parts.map((x) => esc(x.does)).join(". ")}.</p><dl class="smi-dl">${rows.join("")}</dl>`;
  }
  const titles = { ctgov: "ClinicalTrials.gov", europepmc: "Europe PMC", sec: "SEC EDGAR", opentargets: "Open Targets Platform", zetaris: "Zetaris",
    wm: "Meterless World Model", meterless: "Meterless Nightshift", nvidia: "NVIDIA Nemotron", anthropic: "Claude (Anthropic)", openai: "OpenAI-compatible API", replay: "Recorded outputs" };
  const logo = { zetaris: "zetaris", wm: "meterless", meterless: "meterless", nvidia: "nvidia", anthropic: "anthropic" }[id];
  let state = "";
  if (id in st.src || id === "wm") {
    const on = id === "wm" ? st.wmOn : id === "zetaris" ? st.zetarisOn : st.src[id] !== false;
    state = `<p class="smi-now">${on ? "On" : "Off"}${id === "zetaris" && !on ? ": the sources are read directly and the Verifier cannot run" : ""}${id === "wm" && !on ? ": facts stay in the app's own store, and the official engine gets no copy" : ""}.</p>`;
  }
  const users = ai.filter(([, m]) => m.provider === id || (id === "nvidia" && m.provider === "meterless")).map(([n]) => n);
  if (MAP_RUNTIME[id]) state = `<p class="smi-now">${users.length ? `Used by ${users.length} agent${users.length === 1 ? "" : "s"}: ${esc(users.join(", "))}${id === "nvidia" && ai.some(([, m]) => m.provider === "meterless") ? " (through Nightshift where set)" : ""}.` : "Not used by any agent at the moment."}</p>`;
  return `<div class="smi-title">${logo ? chip(logo) + " " : ""}${esc(titles[id] || id)}</div><p>${esc(MAP_INFO[id] || "")}</p>${state}`;
}

async function runTest(url, outId) {
  const out = $(outId);
  out.className = "test-out"; out.textContent = "testing…";
  try {
    const r = await getJSON(url, { method: "POST" });
    out.className = `test-out ${r.ok ? "ok" : "bad"}`;
    out.textContent = r.message;
  } catch (e) { out.className = "test-out bad"; out.textContent = e.message; }
}

async function saveSettings() {
  const body = {};
  for (const [k, v] of Object.entries(pending)) {
    if (k === "apply_recommended") { if (v) body.apply_recommended = true; continue; }
    if (Object.keys(v).length) body[k] = v;
  }
  if (!Object.keys(body).length) { $("setStatus").textContent = "Nothing to save"; return; }
  $("setStatus").textContent = "Saving…";
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) { $("setStatus").textContent = `Not saved: ${data.detail || r.status}`; return; }
    settingsData = data;
    for (const k of Object.keys(pending)) pending[k] = {};
    delete pending.apply_recommended;
    renderSettings();
    $("setStatus").textContent = "Saved ✓";
    analysesCache = [];
    refreshHealth();
  } catch (e) { $("setStatus").textContent = `Not saved: ${e.message}`; }
}

/* ---------- run + progress ---------- */

async function startRun(req) {
  if (!req.input.trial && !req.input.archive_case) { $("trial").focus(); return; }
  clearInterval(pollTimer);
  $("runBtn").disabled = true;
  $("runBtn").textContent = "Investigating…";
  show("progress"); hide("report"); hide("errorBox"); hide("emptyState");
  renderSteps(null, new Set());
  graphModels = {};
  try {
    const cfg = settingsData || await getJSON("/api/settings");
    for (const a of cfg.agents || []) if (a.kind === "ai") graphModels[a.name] = `${a.provider}:${a.model}`;
  } catch { /* badges are optional */ }
  $("liveGraph").innerHTML = ""; applyGraph($("liveGraph"), [], 0);
  $("liveLog").innerHTML = "";
  const t0 = Date.now();
  try {
    const { run_id } = await getJSON("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) });
    analysesCache = [];
    pollTimer = setInterval(() => poll(run_id, t0), 900);
  } catch (e) { finish(); showError({ type: "Request failed", message: e.message }); }
}

function startPolling(runId) {
  clearInterval(pollTimer);
  show("progress"); hide("report"); hide("emptyState");
  const t0 = Date.now();
  pollTimer = setInterval(() => poll(runId, t0), 900);
}

async function poll(runId, t0) {
  $("elapsed").textContent = `${((Date.now() - t0) / 1000).toFixed(0)} s`;
  try {
    const trace = await getJSON(`/api/runs/${runId}/trace`);
    renderLive(trace);
    const job = await getJSON(`/api/runs/${runId}`);
    if (job.status === "done") {
      clearInterval(pollTimer);
      finish();
      const res = job.result;
      if (res.status === "success") { hide("progress"); currentRun = res.run_id; history.replaceState(null, "", `#/run/${res.run_id}`); render(res); }
      else showError(res.error || { type: res.status, message: "Run did not complete." }, res);
    }
  } catch { /* transient; keep polling */ }
}

function finish() { $("runBtn").disabled = false; $("runBtn").textContent = "Run investigation"; }

function renderSteps(active, done, warn = new Set()) {
  $("agentSteps").innerHTML = AGENTS.map(([n, r]) => {
    const cls = n === active ? "active" : warn.has(n) ? "warn" : done.has(n) ? "done" : "";
    return `<li class="agent-step ${cls}"><div class="nm"><span class="dot"></span>${n}</div><div class="rl">${r}</div></li>`;
  }).join("");
}

function renderLive(events) {
  applyGraph($("liveGraph"), events, events.length);
  const seen = new Set(); const warn = new Set(); let active = null;
  for (const e of events) {
    if (AGENTS.some(([n]) => n === e.agent_name)) { seen.add(e.agent_name); active = e.target_agent || e.agent_name; }
    if (e.status === "warning" || e.status === "retry" || e.status === "needs_revision") warn.add(e.agent_name);
  }
  renderSteps(active, seen, warn);
  const log = $("liveLog");
  log.innerHTML = events.map((e) => {
    const cls = e.status === "error" ? "er" : (e.status === "warning" || e.status === "retry" || e.status === "needs_revision") ? "wr" : "";
    return `<div class="${cls}">[${esc(e.elapsed_s)}s] <span class="ag">${esc(e.agent_name)}</span> ${esc(e.action)} — ${esc(e.output_summary)}</div>`;
  }).join("");
  log.scrollTop = log.scrollHeight;
}

function showError(err, res) {
  const box = $("errorBox");
  // In the offline demo a trial without a recording cannot run; point to the finished analyses instead of an error.
  if (sampleMode && (err.type === "ModelOutputNotRecorded" || err.type === "OfflineCacheMiss")) {
    box.className = "panel offline-note";
    box.innerHTML = `<h3>This demo runs offline</h3>
      <p>No AI model is connected here, so only analyses that were recorded can run. Open one of the 41 finished analyses:
        each opens instantly with the full report, the evidence and a step-by-step replay of the agents.</p>
      <div class="offline-actions"><a class="btn-primary inline" href="#/analyses">Browse all analyses</a>
        <a class="btn-ghost" href="#/run/example-pelacarsen">Open the pelacarsen example</a></div>
      <p class="muted">To analyse any trial live, run Trial Autopsy on your own machine with your keys:
        <a href="https://github.com/stas-gatin/trial-autopsy#quick-start">Quick start</a>.</p>`;
    document.body.classList.add("has-report");
    show("errorBox");
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  box.className = "panel error";
  let extra = "";
  if (err.type === "ModelOutputNotRecorded") extra = "<p>This request needs a live model. Add ANTHROPIC_API_KEY / NVIDIA_API_KEY to .env, or run a recorded case.</p>";
  if (err.type === "OutOfScope") extra = "<p>Trial Autopsy works only on phase 3 <b>cardiovascular outcome trials</b> " +
    "(endpoints such as heart attack, stroke or cardiovascular death). Try a trial of a cholesterol, Lp(a), blood-pressure, " +
    "GLP-1, SGLT2 or anti-inflammatory drug, or open one of the recorded cases on the left.</p>";
  const title = { OutOfScope: "Outside Trial Autopsy's scope", ModelOutputNotRecorded: "Needs a live model",
                  UpstreamSourceError: "A data source did not respond", OfflineCacheMiss: "Not available offline" }[err.type] || err.type;
  box.innerHTML = `<h3>${esc(title)}</h3><p>${esc(err.message)}</p>${extra}${res && res.log_file ? `<p class="mono">${esc(res.log_file)}</p>` : ""}`;
  document.body.classList.add("has-report");
  show("errorBox");
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- report ---------- */

const WHY = {
  target_not_causal: "Human genetics shows the drug's target does not cause heart disease, so blocking it could not help.",
  biomarker_not_causal: "The drug mainly changes a blood marker that does not cause heart attacks, so moving it brings no benefit.",
  effect_too_small: "The drug works in the right direction, but the expected benefit was too small for this trial to detect reliably.",
  off_target_harm: "The drug caused harm through effects unrelated to its intended target.",
  population: "The trial enrolled patients unlikely to benefit.",
  design_or_endpoint: "The trial's design or endpoint made a benefit hard to show.",
  execution_or_nonscientific: "The trial stopped for non-scientific reasons (recruitment, business).",
  insufficient_data: "There is not yet enough public data to say for sure.",
  robust_success: "The drug strongly lowers a marker that causes heart disease, and the trial was large enough to see it.",
  narrow_success: "The benefit is modest, but the trial was large enough to detect it.",
  success_with_caveats: "The trial met its goal, but with important caveats (size of benefit, safety, or doses).",
};

// "Powered by" strip under the case title: only what this run actually used.
function renderPoweredBy(o, events) {
  const byProvider = {};
  for (const [agent, spec] of Object.entries(o.models_used || {})) {
    const p = providerOf(spec);
    if (p) (byProvider[p] ||= { models: new Set(), agents: [] }), byProvider[p].models.add(modelOf(spec)), byProvider[p].agents.push(agent);
  }
  const parts = [];
  if (events.some((e) => e.via === "zetaris")) parts.push(`<span class="pb-item">${logoImg("zetaris", 16)}<b>Zetaris</b> data layer</span>`);
  if (events.some((e) => e.action === "world_model_sync")) parts.push(`<span class="pb-item">${logoImg("meterless", 16)}<b>Meterless</b> World Model</span>`);
  for (const p of ["meterless", "nvidia", "anthropic"]) {
    const g = byProvider[p];
    const via = p === "meterless" ? " via Nightshift" : "";
    if (g) parts.push(`<span class="pb-item" title="${esc(g.agents.join(", "))}">${logoImg(p === "meterless" ? "nvidia" : p, 16)}<b>${esc([...g.models].join(", "))}</b>${via} · ${g.agents.length} agent${g.agents.length === 1 ? "" : "s"}</span>`);
  }
  $("poweredBy").innerHTML = parts.length ? `<span class="pb-label">Powered by</span>${parts.join("")}` : "";
}

// On a phone the wide diagrams scroll sideways inside their box; start them centred, where the answer is.
function centerWide(box, tries = 15) {
  if (box.clientWidth && box.scrollWidth > box.clientWidth) box.scrollLeft = (box.scrollWidth - box.clientWidth) / 2;
  else if (tries) setTimeout(() => centerWide(box, tries - 1), 200);   // not laid out yet (the report is still opening)
}

function render(res) {
  const o = res.output;
  graphModels = o.models_used || {};
  renderPoweredBy(o, []);
  factsById = Object.fromEntries(o.facts.map((f) => [f.fact_id, f]));
  verifyById = Object.fromEntries(((o.verification || {}).results || []).map((r) => [r.fact_id, r]));
  const t = res.trial || {};
  const v = o.verdict, c = o.calculator, ci = o.calculator_inputs;
  const asOf = res.as_of ? ` · evidence as of ${res.as_of}${res.backtest ? " (historical backtest)" : ""}` : "";
  $("caseTitle").textContent = `${t.acronym || t.nct_id || ""} — ${res.drug.name || ""}`;
  $("caseSub").innerHTML = `${esc(t.nct_id || "")} · ${esc(t.sponsor || "")} · ${esc((res.drug.mechanisms || []).join("; "))} · ` +
    `<b>${res.mode === "forecast" ? "Forecast" : "Autopsy"}</b>${esc(asOf)}`;

  const [label, cls] = OUTCOME[v.outcome] || [v.outcome, "o-info"];
  $("outcomeBadge").className = `outcome-badge ${cls}`;
  $("outcomeBadge").innerHTML = `${esc(label)}<small>${res.mode === "forecast" ? "forecast" : "per readout"}</small>`;
  $("headline").textContent = o.report.headline || "";
  renderTimeline(res);

  // 1. What happened
  const rrr = c.causal_rrr !== null && c.causal_rrr !== undefined ? Math.round(c.causal_rrr * 100) : null;
  let what;
  if (res.mode === "forecast") {
    what = `Results are not out yet. ` + (c.expected_hr ? `If the drug works as its earlier data suggest, expect about ${rrr}% fewer heart attacks and strokes (HR ${num(c.expected_hr)}). ` : "") +
      (c.power !== null && c.power !== undefined ? `Chance the trial shows it: ${pct(c.power)}.` : "Whether the trial is big enough to show it cannot be judged yet: its planned size is not public.");
  } else {
    what = { failure: "The trial did not meet its main goal.", success: "The trial met its main goal.", stopped_harm: "The trial was stopped because the drug caused harm." }[v.outcome] || "";
    what = `${what}`;
    if (ci.observed_hr) what += ` Observed effect: HR ${num(ci.observed_hr)}${ci.observed_ci ? ` (95% CI ${num(ci.observed_ci[0], 2)}–${num(ci.observed_ci[1], 2)})` : ""}.`;
    else if (v.outcome_basis && v.outcome_basis.startsWith("readout")) what += " The sponsor has not yet published the size of the effect.";
  }
  $("ansWhat").textContent = what;
  $("ansWhatTitle").textContent = res.mode === "forecast" ? "What to expect" : "The result (from the announcement)";
  const pred = $("ansPredicted");
  if (res.mode !== "forecast" && c.power !== null && c.power !== undefined) {
    pred.textContent = `Before the result, the evidence available at the time gave the trial a ${pct(c.power)} chance of success (calculator).`;
  } else if (res.mode !== "forecast" && !c.expected_hr) {
    pred.textContent = "Before the result, the calculator could not size this drug's effect (see check 2).";
  } else pred.textContent = "";

  // 2. Why
  $("primaryCause").textContent = CATEGORY[v.primary_category] || v.primary_category;
  $("ansWhy").textContent = WHY[v.primary_category] || "";

  // 3. Others
  const rt = o.readthrough || [];
  let others = o.report.readthrough_note || "";
  if (!others && rt.length) others = rt.map((p) => `${p.drug}: ${p.comparison || "assessed"}`).join("; ");
  if (!others) others = res.mode === "forecast" ? `Weakest link: ${v.weakest_link}.` : "No other clinical programmes on this target were analysed.";
  $("ansOthers").innerHTML = rt.length ? rt.map((p) => {
      const t = p.trial || {};
      const cmp = p.expected_hr_5y && c.expected_hr ? (p.expected_hr_5y > c.expected_hr ? "weaker" : "stronger") : null;
      return `<div class="next-row"><div class="next-top"><b>${esc(p.drug)}</b> <span class="muted">${esc((p.stage || "").replace("_", " ").toLowerCase())}${t.acronym ? " · " + esc(t.acronym) : ""}${t.status ? " · " + esc(t.status.toLowerCase()) : ""}</span></div>
        <div class="next-nums"><span>expected HR <b>${p.expected_hr_5y ? num(p.expected_hr_5y) : "–"}</b>${cmp ? ` <span class="next-cmp next-${cmp}">${cmp} than ${esc(res.drug.name)}</span>` : ""}</span>
        ${p.events_needed_80 ? `<span>needs <b>~${p.events_needed_80}</b> events for 80% power${ci.events ? ` (this trial had ${ci.events})` : ""}</span>` : ""}</div></div>`;
    }).join("") + (o.report.readthrough_note ? `<p class="next-note">${esc(o.report.readthrough_note)}</p>` : "")
    : esc(others);

  $("confidenceChip").textContent = `Confidence in this explanation: ${v.confidence}`;
  $("confidenceChip").className = `tag conf-${esc(v.confidence)}`;
  $("confidenceNote").textContent = {
    high: "Set by the Investigator agent (AI): the key numbers are known and all checks agree.",
    medium: "Set by the Investigator agent (AI): the main evidence is in place but some numbers are missing or indirect.",
    low: "Set by the Investigator agent (AI): important numbers are not public yet (e.g. the size of the effect), so this explanation may change.",
  }[v.confidence] || "";

  chainTrial = res.trial || {};
  renderChecks(o);
  renderDivergence(o, res.mode);
  renderCriticReview(res);
  renderTimeMachine(res);
  renderPrereg(res);
  renderCost(res);
  renderPathway(res);
  mapRes = res; if (!$("tabMap").classList.contains("hidden")) renderEvidenceMap();
  const abstain = !o.calculator.expected_hr;
  $("chartTitle").textContent = abstain ? "Why there is no forecast chart for this drug"
    : "If the drug works as expected, would this trial prove it?";
  $("chartCaption").classList.toggle("hidden", abstain);
  document.querySelector(".chart-block .chart-help").classList.toggle("hidden", abstain);
  $("chartCaption").textContent = abstain ? "" : chartCaption(o);
  if (abstain) drawAbstain(o); else drawForest(o);
  renderWhatIf(o, abstain);
  $("secondaryCauses").innerHTML = (v.secondary_categories || []).length
    ? `<span class="muted">Also contributing:</span> ` + v.secondary_categories.map((x) => `<span class="tag">${esc(CATEGORY[x] || x)}</span>`).join("") : "";
  $("summary").textContent = o.report.summary || "";
  $("keyEvidence").innerHTML = (o.report.key_evidence || []).map((k) => `<li>${esc(k)}</li>`).join("");
  $("wouldChange").textContent = o.report.what_would_change_this || "";
  drawGauge(v.probability_of_success);
  renderInputs(o);
  renderHypotheses(o.hypotheses, v.primary_category);
  renderReadthrough(rt, o.report.readthrough_note, res.mode);
  renderFacts(o.facts);
  renderVerification(o.verification);
  $("sources").innerHTML = o.sources.map((s) => `<li><span class="acc acc-${esc(s.access)}">${esc(s.access)}</span>` +
    `${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)} <span class="muted">${esc(s.pub_date || "")}</span></li>`).join("");
  renderCritic(o.critic);
  getJSON(`/api/runs/${res.run_id}/trace`).then((events) => {
    renderTrace(events);
    renderPoweredBy(o, events);
    const viaZetaris = events.filter((e) => e.via === "zetaris").length;
    if (viaZetaris) $("footMeta").textContent += ` · data layer: Zetaris (${viaZetaris} source reads via Lightning SQL)`;
    const wm = events.find((e) => e.action === "world_model_sync");
    if (wm) $("footMeta").textContent += ` · Meterless World Model: ${wm.output_summary.split(" in the ")[0]}`;
  }).catch(() => {});
  const models = Object.entries(o.models_used || {}).map(([k, m]) => `${k}: ${m}`).join(" · ");
  $("footMeta").textContent = `Run ${res.run_id} · ${res.execution_time_seconds}s · ${res.log_file} · ${models}`;
  selectTab("tabReport");
  renderTarget(res);
  annotateReport();
  document.body.classList.add("has-report");
  show("report");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// Hoverable explanations for medical and statistical terms (glossary.js). First occurrence per block.
function annotateReport() {
  if (typeof annotateTerms !== "function") return;
  const blocks = "#headline, .answer-card, .cs, #divergence, #chainIntro, #chartCaption, .legend, .chart-help, #summary, #keyEvidence li, " +
    "#wouldChange, .hyp, .rt, #readthroughNote, #inputsTable, .pane-intro";
  document.querySelectorAll(blocks).forEach(annotateTerms);
}

function renderTimeline(res) {
  const d = (x) => x || "date not stated";
  const forecast = res.mode === "forecast";
  const observed = res.output.calculator_inputs.observed_hr;
  const steps = forecast ? [
    { t: "Phase 1–2 data", s: "biomarker effects, dose", st: "done" },
    { t: "Phase 3 design", s: "size, endpoint, patients", st: "done" },
    { t: "This analysis", s: d(res.analysis_date), st: "now" },
    { t: "Phase 3 result", s: res.trial.primary_completion ? `expected after ${res.trial.primary_completion}` : "not yet known", st: "future" },
  ] : [
    { t: "Phase 1–2 data", s: "biomarker effects, dose", st: "done" },
    { t: "Phase 3 run", s: "the outcome trial", st: "done" },
    { t: "Result announced", s: d(res.readout_date), st: "done" },
    { t: "This analysis", s: res.backtest ? `replayed as of ${d(res.analysis_date)}` : d(res.analysis_date), st: "now" },
    { t: "Full data", s: observed ? "published" : "not yet published", st: observed ? "done" : "future" },
  ];
  $("timeline").innerHTML = steps.map((x) => `<li class="tl ${x.st}"><span class="tl-dot"></span><div class="tl-t">${esc(x.t)}</div><div class="tl-s">${esc(x.s)}</div></li>`).join("");
  $("modeBanner").innerHTML = forecast
    ? "<b>Forecast:</b> made <u>before</u> the phase 3 result. It estimates the chance of success from phase 2 data and the trial design."
    : `<b>Autopsy:</b> made <u>after</u> the phase 3 result was announced. The result itself comes from the announcement; the agents explain <i>why</i> it happened.` +
      (res.backtest ? ` <b>Historical replay:</b> the agents saw only documents published up to ${esc(res.analysis_date)}.` : "");
}

/* ---------- target protein in 3D ---------- */

const NOT_DRUG = new Set(["HOH", "NA", "CL", "K", "MG", "CA", "ZN", "MN", "FE", "SO4", "PO4", "GOL", "EDO", "PEG", "PGE", "1PE",
  "PG4", "P6G", "NAG", "MAN", "FUC", "BMA", "GAL", "NDG", "DLP", "PCW", "PLC", "2OB", "CLR", "OLA", "PLM", "ACT", "DMS", "EPE", "MES", "TRS", "IOD", "BR"]);
let mol = null, molSpinning = true, molSurface = false, molProfile = null, targetSeq = 0;

async function renderTarget(res) {
  const panel = $("targetPanel");
  const target = (res.drug.targets || [])[0];
  const seq = ++targetSeq;
  if (!target || !window.$3Dmol) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  $("targetInfo").innerHTML = `<div class="sk sk-s"></div><div class="sk sk-l"></div><div class="sk"></div><div class="sk"></div><div class="sk sk-m"></div>`;
  molStage(`Finding the best 3D structure of ${target.symbol}`, "Searching RCSB PDB for a structure with the drug bound, then experimental structures, then AlphaFold");
  const drugs = [res.drug.name, ...(res.drug.aliases || []).filter((a) => a.length > 5).slice(0, 2)].join(",");
  let p;
  try { p = await getJSON(`/api/target?symbol=${encodeURIComponent(target.symbol)}&ensembl=${encodeURIComponent(target.id)}&drugs=${encodeURIComponent(drugs)}`); }
  catch { p = { available: false }; }
  if (seq !== targetSeq) return;
  if (!p.available) { panel.classList.add("hidden"); return; }
  molProfile = p;
  setupColorModes(p);
  const g = (res.output.genetics || []).find((x) => x.target === target.symbol);
  const sameDrug = p.drug_bound && !p.related_drug;
  const proteinDrug = /antibody|protein/i.test(res.drug.drug_type || "");
  molProfile.proteinDrug = proteinDrug;
  $("targetInfo").innerHTML = `
    <div class="t-sym">${esc(target.symbol)}</div>
    <div class="t-name">${esc(p.protein_name || target.name || "")}</div>
    <div class="t-block"><div class="label">What it does in the body</div><p>${esc(p.function || "No function summary available.")}</p></div>
    <div class="t-block"><div class="label">What the drug does to it</div><p>${esc(res.drug.name)}: ${esc((res.drug.mechanisms || []).join("; ") || "mechanism not annotated")}.</p></div>
    ${g ? `<div class="t-block"><div class="label">Human genetics</div><p>${g.genetic_association_score > 0
      ? `Open Targets genetic association with coronary disease: <b>${esc(g.genetic_association_score)}</b> on a 0–1 scale (${esc(g.evidence_count)} evidence items).`
      : "Open Targets finds <b>no genetic association</b> between this protein and coronary disease."}</p></div>` : ""}
    <div class="t-block"><div class="label">This 3D model</div><p>${esc(p.label)}.${sameDrug ? (proteinDrug ? " The drug is an antibody, shown in gold, gripping the target." : " The drug is highlighted in magenta.")
        : p.related_drug ? " The highlighted molecule is a drug with the same target, shown for orientation." : ""}
      ${p.source === "alphafold" ? " Predicted by AlphaFold; no experimental structure covers most of this protein." : ""}</p>
      <p class="t-links">${p.links.structure ? `<a href="${esc(p.links.structure)}" target="_blank" rel="noopener">${p.source === "alphafold" ? "AlphaFold" : "RCSB PDB"} entry</a> · ` : ""}<a href="${esc(p.links.uniprot)}" target="_blank" rel="noopener">UniProt ${esc(p.uniprot)}</a></p></div>`;
  annotateTerms($("targetInfo"));
  if (!p.source) { $("mol").innerHTML = '<div class="mol-empty">No 3D structure is available for this protein.</div>'; return; }
  molStage(`Downloading 3D coordinates · ${p.source === "alphafold" ? "AlphaFold" : "PDB"} ${String(p.id).toUpperCase()}`, p.label, 0);
  let text;
  try {
    const r = await fetch(`/api/structure?source=${encodeURIComponent(p.source)}&id=${encodeURIComponent(p.id)}${p.url ? `&url=${encodeURIComponent(p.url)}` : ""}`);
    if (!r.ok) throw new Error(String(r.status));
    const total = Number(r.headers.get("content-length")) || 0;
    if (r.body && total) {
      const reader = r.body.getReader(), chunks = [];
      let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value); got += value.length;
        if (seq === targetSeq) molProgress(got / total, got);
      }
      text = new TextDecoder().decode(await new Blob(chunks).arrayBuffer());
    } else text = await r.text();
  } catch { $("mol").innerHTML = '<div class="mol-empty">The 3D structure could not be loaded (offline or source unavailable).</div>'; return; }
  if (seq !== targetSeq) return;
  molStage("Building the 3D model", `${(text.length / 1024).toFixed(0)} KB of atomic coordinates`, 1);
  await new Promise((r) => setTimeout(r, 30));
  if (seq !== targetSeq) return;
  $("mol").innerHTML = "";
  mol = window.$3Dmol.createViewer($("mol"), { backgroundColor: "white" });
  mol.addModel(text, "pdb");
  molSurface = false;
  styleMolecule();
  mol.zoomTo();
  mol.zoom(1.35);
  mol.render();
  molSpinning = true;
  mol.spin("y", 0.6);
  $("molSpin").textContent = "Pause rotation";
  $("molStyle").textContent = "Show surface";
  setupBindingView();
}

// "Show where the drug binds": zoom to the drug (or the antibody interface) and label the amino acids around it.
let molBinding = false;
function bindingSite() {
  const p = molProfile, target = p.chains && p.chains.length ? { chain: p.chains } : {};
  if (!p.drug_bound || !p.chains || !p.chains.length) return null;   // only when the drug (or one on the same target) is in the structure
  const lig = { hetflag: true, not: { resn: [...NOT_DRUG] } };
  const ligAtoms = p.proteinDrug ? [] : mol.selectedAtoms(lig);
  if (ligAtoms.length) {
    // one copy of the drug (structures often hold one per protein chain)
    const one = { hetflag: true, chain: ligAtoms[0].chain, resi: ligAtoms[0].resi };
    const near = mol.selectedAtoms({ hetflag: false, within: { distance: 4.5, sel: one } });
    return { kind: "ligand", focus: { within: { distance: 7, sel: one } }, near, ligName: ligAtoms[0].resn };
  }
  const others = [...new Set(mol.selectedAtoms({ hetflag: false }).map((a) => a.chain))].filter((c) => p.chains && !p.chains.includes(c));
  if (others.length) {
    const partner = { chain: others, hetflag: false };
    const near = mol.selectedAtoms({ ...target, hetflag: false, within: { distance: 4.5, sel: partner } });
    return { kind: "interface", focus: { ...target, hetflag: false, within: { distance: 4.5, sel: partner } }, near };
  }
  return null;
}

function setupBindingView() {
  molBinding = false;
  const btn = $("molBind"), note = $("molBindNote");
  note.classList.add("hidden");
  const site = mol ? bindingSite() : null;
  btn.classList.toggle("hidden", !site || !site.near.length);
  if (!site) return;
  btn.textContent = site.kind === "ligand" ? "Show where the drug binds" : "Show where the antibody grips";
  btn.onclick = () => {
    molBinding = !molBinding;
    styleMolecule();
    mol.removeAllLabels();
    if (molBinding) {
      molSpinning = false; mol.spin(false); $("molSpin").textContent = "Rotate";
      const resKey = (a) => `${a.chain}:${a.resi}`;
      const residues = [...new Map(site.near.map((a) => [resKey(a), a])).values()];
      residues.forEach((a) => mol.addStyle({ chain: a.chain, resi: a.resi, hetflag: false }, { stick: { colorscheme: "greenCarbon", radius: 0.18 } }));
      residues.slice(0, 6).forEach((a) => mol.addLabel(`${a.resn} ${a.resi}`, { position: { x: a.x, y: a.y, z: a.z }, fontSize: 11, fontColor: "#142133",
        backgroundColor: "#ffffff", backgroundOpacity: 0.85, borderThickness: 0, inFront: true, showBackground: true }));
      mol.zoomTo(site.focus, 900);
      const names = [...new Set(residues.map((a) => a.resn))];
      note.innerHTML = site.kind === "ligand"
        ? `<b>${esc(molProfile.related_drug ? "A drug on the same target" : "The drug")}</b> (magenta) sits in a pocket lined by <b>${residues.length} amino acids</b> of the target (green sticks${residues.length > 6 ? "; 6 are labelled" : ""}).
           Blocking this pocket is how it switches the protein off.${molProfile.related_drug ? " This structure shows a related drug, used for orientation." : ""}`
        : `The antibody (gold) grips the target through <b>${residues.length} amino acids</b> on its surface (green sticks${residues.length > 6 ? "; 6 are labelled" : ""}).
           Covering this patch stops the target from working. Amino acids involved: ${esc(names.slice(0, 6).join(", "))}${names.length > 6 ? "…" : ""}.`;
      note.classList.remove("hidden");
      btn.textContent = "Show the whole protein";
    } else {
      mol.zoomTo(); mol.zoom(1.35);
      note.classList.add("hidden");
      btn.textContent = site.kind === "ligand" ? "Show where the drug binds" : "Show where the antibody grips";
    }
    mol.render();
  };
}

function molStage(title, detail, progress) {
  const bar = progress === undefined ? '<div class="mol-bar indeterminate"><span></span></div>'
    : `<div class="mol-bar"><span style="width:${Math.round(progress * 100)}%"></span></div>`;
  $("mol").innerHTML = `<div class="mol-loading"><div class="mol-spinner" aria-hidden="true"></div>
    <div class="mol-stage">${esc(title)}</div><div class="mol-detail">${esc(detail || "")}</div>${bar}
    <div class="mol-pct" id="molPct"></div></div>`;
}

function molProgress(fraction, bytes) {
  const bar = document.querySelector("#mol .mol-bar span");
  if (bar) bar.style.width = `${Math.round(fraction * 100)}%`;
  const pct = $("molPct");
  if (pct) pct.textContent = `${Math.round(fraction * 100)}% · ${(bytes / 1024).toFixed(0)} KB`;
}

// Categorical slots in fixed order (validated reference palette); domain groups are also named in the legend.
const DOMAIN_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
const PLDDT = [[90, "#0053d6", "very high (>90)"], [70, "#65cbf3", "confident (70–90)"], [50, "#ffdb13", "low (50–70)"], [0, "#ff7d45", "very low (<50)"]];
let molColorMode = "rainbow";

function domainGroups(p) {
  const order = [], byGroup = {};
  for (const f of p.features || []) {
    if (!byGroup[f.group]) { byGroup[f.group] = { name: f.group, parts: [] }; order.push(f.group); }
    byGroup[f.group].parts.push(f);
  }
  // propeptide first so domains inside it are painted over it
  order.sort((a, b) => (byGroup[a].parts[0].kind === "Propeptide" ? -1 : 0) - (byGroup[b].parts[0].kind === "Propeptide" ? -1 : 0));
  return order.slice(0, DOMAIN_COLORS.length).map((g, i) => ({ ...byGroup[g], color: DOMAIN_COLORS[i] }));
}

function setupColorModes(p) {
  const sel = $("molColor");
  const opts = [["rainbow", "start → end of chain"]];
  if ((p.features || []).length) opts.push(["domains", "protein domains (UniProt)"]);
  if (p.source === "alphafold") opts.push(["plddt", "AlphaFold confidence"]);
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  molColorMode = opts.some(([v]) => v === "domains") ? "domains" : "rainbow";
  sel.value = molColorMode;
  sel.parentElement.classList.toggle("hidden", opts.length < 2);
}

function styleMolecule() {
  const p = molProfile;
  const target = p.chains && p.chains.length ? { chain: p.chains } : {};
  mol.removeAllSurfaces();
  mol.setStyle({}, { cartoon: { color: "#d9b44a", opacity: 0.85 } });           // other chains (e.g. an antibody drug)
  const groups = molColorMode === "domains" ? domainGroups(p) : [];
  const painted = new Set();
  if (molColorMode === "plddt") {
    mol.setStyle({ ...target, hetflag: false }, { cartoon: { colorfunc: (a) => (PLDDT.find(([t]) => a.b >= t) || PLDDT[3])[1] } });
  } else if (groups.length) {
    mol.setStyle({ ...target, hetflag: false }, { cartoon: { color: "#c5cfd9" } });
    // UniProt positions → residue numbers in this file (structures often number the mature protein from 1)
    const chains = p.chains && p.chains.length ? p.chains : [...new Set(mol.selectedAtoms({ hetflag: false }).map((a) => a.chain))];
    const offset = {}, present = {};
    for (const c of chains) {
      const resis = [...new Set(mol.selectedAtoms({ chain: c, hetflag: false }).map((a) => a.resi))].sort((x, y) => x - y);
      present[c] = resis;
      const cm = (p.chain_map || {})[c];
      const fileNum = cm && cm.seq_start ? resis[cm.seq_start - 1] : undefined;
      offset[c] = cm && cm.unp_start && fileNum !== undefined ? cm.unp_start - fileNum : 0;
    }
    for (const g of groups) for (const f of g.parts) for (const c of chains) {
      const lo = f.start - offset[c], hi = f.end - offset[c];
      const sel = present[c].filter((r) => r >= lo && r <= hi);
      if (sel.length >= 5) { mol.setStyle({ chain: c, hetflag: false, resi: sel }, { cartoon: { color: g.color } }); painted.add(g.name); }
    }
    if (!painted.size) mol.setStyle({ ...target, hetflag: false }, { cartoon: { color: "spectrum" } });   // no annotated domain in this part of the protein
  } else {
    mol.setStyle({ ...target, hetflag: false }, { cartoon: { color: "spectrum" } });
  }
  mol.setStyle({ resn: "HOH" }, {});
  const het = { hetflag: true, not: { resn: [...NOT_DRUG] } };
  mol.addStyle(het, { stick: { colorscheme: "magentaCarbon", radius: 0.28 }, sphere: { colorscheme: "magentaCarbon", scale: 0.25 } });
  if (molSurface) mol.addSurface(window.$3Dmol.SurfaceType.VDW, { opacity: 0.55, color: "#9fd3d8" }, { ...target, hetflag: false });
  const proteinChains = new Set(mol.selectedAtoms({ hetflag: false }).map((a) => a.chain));
  const antibody = p.chains && p.chains.length && [...proteinChains].some((c) => !p.chains.includes(c));
  const targetLegend = molColorMode === "plddt" ? PLDDT.map(([, c, l]) => `<span><i class="lg-sw" style="background:${c}"></i> ${l}</span>`).join("")
    : painted.size ? groups.filter((g) => painted.has(g.name)).map((g) => `<span><i class="lg-sw" style="background:${g.color}"></i> ${esc(g.name)}${g.parts.length > 1 ? ` ×${g.parts.length}` : ""}</span>`).join("") +
      `<span><i class="lg-sw" style="background:#c5cfd9"></i> rest of the protein</span>`
    : `<span><i class="lg-spec"></i> target protein (rainbow: start → end of the chain)</span>`;
  $("molLegend").innerHTML = targetLegend +
    (p.drug_bound && !p.proteinDrug ? `<span><i class="lg-drug"></i> drug molecule</span>` : "") +
    (antibody ? `<span><i class="lg-ab"></i> ${p.proteinDrug && p.drug_bound ? "the drug (antibody)" : "other protein chains in this structure"}</span>` : "");
}

function setupMolControls() {
  $("molSpin").addEventListener("click", () => { if (!mol) return; molSpinning = !molSpinning; mol.spin(molSpinning ? "y" : false, 0.6); $("molSpin").textContent = molSpinning ? "Pause rotation" : "Rotate"; });
  $("molStyle").addEventListener("click", () => { if (!mol) return; molSurface = !molSurface; styleMolecule(); mol.render(); $("molStyle").textContent = molSurface ? "Hide surface" : "Show surface"; });
  $("molReset").addEventListener("click", () => { if (!mol) return; if (molBinding) $("molBind").click(); mol.zoomTo(); mol.zoom(1.35); mol.render(); });
  $("molColor").addEventListener("change", () => { if (!mol) return; molColorMode = $("molColor").value; styleMolecule(); mol.render(); });
}

function selectTab(id) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
  document.querySelectorAll(".tab-pane").forEach((p) => p.classList.toggle("hidden", p.id !== id));
  if (id === "tabTrace" && $("lgDetails").open) drawGraph();
  if (id === "tabMap") renderEvidenceMap();
}

let graphDrawn = false;
// Restyle LangGraph's own Mermaid export in the app's colours: code nodes grey, model nodes teal, critic orange.
function styleLangGraph(src) {
  return src
    .replace("MechanismAnalyst(MechanismAnalyst)", "MechanismAnalyst(Mechanism analyst)").replace("ReadThrough(ReadThrough)", "ReadThrough(Read-through)")
    .replace("Refine(Refine)", "Refine(Refine: targeted search)")
    .replace(/__start__\(\[<p>__start__<\/p>\]\)/, "__start__([start])").replace(/__end__\(\[<p>__end__<\/p>\]\)/, "__end__([end])")
    .replace(/classDef default[^\n]*/, "classDef default fill:#e3f3f4,stroke:#0b6e79,stroke-width:1.5px,color:#142133")
    .replace(/classDef first[^\n]*/, "classDef first fill:#ffffff,stroke:#9aa8b8,color:#6b7a90")
    .replace(/classDef last[^\n]*/, "classDef last fill:#ffffff,stroke:#9aa8b8,color:#6b7a90") +
    "\n\tclassDef code fill:#ffffff,stroke:#3c4a5e,stroke-width:1.5px,color:#142133" +
    "\n\tclassDef critic fill:#fdf3e2,stroke:#a15c07,stroke-width:2px,color:#142133" +
    "\n\tclass Intake,Genetics,Literature,Calculator,Refine code" +
    "\n\tclass Critic critic\n";
}

async function drawGraph() {
  if (graphDrawn || !window.mermaid) return;
  try {
    const { mermaid: raw } = await getJSON("/api/graph");
    const src = styleLangGraph(raw);
    window.mermaid.initialize({ startOnLoad: false, theme: "base", flowchart: { curve: "basis", nodeSpacing: 28, rankSpacing: 34 },
      themeVariables: { fontFamily: "IBM Plex Sans, sans-serif", fontSize: "14px", lineColor: "#9aa8b8", primaryTextColor: "#142133",
        edgeLabelBackground: "#ffffff", tertiaryColor: "#ffffff" } });
    const { svg } = await window.mermaid.render("agentGraph", src);
    $("graphView").innerHTML = svg;
    graphDrawn = true;
  } catch { $("graphView").textContent = "Graph unavailable offline."; }
}

function chartCaption(o) {
  const c = o.calculator, ci = o.calculator_inputs, t = trialOdds(o);
  if (!c.expected_hr) return "This drug works mainly through a mechanism with no validated size-of-benefit conversion " +
    "(for example weight, glucose or inflammation), so the calculator abstains rather than predicting a false failure; " +
    "the agents judge it from the evidence instead." + (c.partial_expected_hr ? ` Lipid/blood-pressure changes alone would give HR ${num(c.partial_expected_hr)} (indicative only).` : "") +
    (ci.observed_hr ? ` What actually happened: HR ${num(ci.observed_hr)}.` : "");
  if (!t) return `If the drug works as expected, the result should be about HR ${num(c.expected_hr)}. ` +
    "The trial's planned number of events is unknown, so we cannot say how likely it was to prove it." +
    (ci.observed_hr ? ` What actually happened: HR ${num(ci.observed_hr)}.` : "");
  if (c.expected_hr >= 0.98) {
    let s = `The drug was expected to have practically no effect on events (HR ${num(c.expected_hr)}), so the curve sits on 1.0: ` +
      `the trial could pass the mark (HR ${num(t.pass, 3)} or lower, set by its ${ci.events} events) only by luck, ${Math.max(1, Math.round(t.power * 100))}% chance.`;
    if (ci.observed_hr) s += ` What actually happened: HR ${num(ci.observed_hr)}${ci.observed_hr <= t.pass ? " (passed)" : " (missed)"}.`;
    return s;
  }
  let s = `If the drug works as expected, the trial's result will land somewhere under the curve; exactly where is down to chance. ` +
    `The trial proves the drug works only if the result falls left of the pass mark (HR ${num(t.pass, 3)} or lower, set by its ${ci.events} events). ` +
    (t.power < 0.995 ? `Chance to pass: ${Math.round(t.power * 100)}%; ${100 - Math.round(t.power * 100)}% chance to miss even though the drug works.`
      : "Chance to pass: over 99%.");
  if (ci.observed_hr) s += ` What actually happened: HR ${num(ci.observed_hr)}${ci.observed_hr <= t.pass ? " (passed)" : " (missed)"}.`;
  else if (["failure", "success", "stopped_harm"].includes(o.verdict.outcome))
    s += " The real HR is not shown because the announcement used here gave only met / not met, without the number.";
  return s;
}

// Normal distribution helpers for the "chance to pass" chart (same Schoenfeld model as the calculator).
function normCdf(z) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989422804 * Math.exp(-z * z / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z > 0 ? 1 - p : p;
}
function normInv(p) {
  let lo = -10, hi = 10;
  for (let i = 0; i < 80; i++) { const m = (lo + hi) / 2; if (normCdf(m) < p) lo = m; else hi = m; }
  return (lo + hi) / 2;
}

// Where the trial's result is likely to land if the drug works as expected, and the HR it must beat.
function trialOdds(o) {
  const c = o.calculator, ci = o.calculator_inputs;
  if (!c.expected_hr || !ci.events) return null;
  const se = 2 / Math.sqrt(ci.events);
  const z = normInv(1 - (ci.alpha_two_sided || 0.05) / 2);
  const pass = Math.exp(-z * se);
  const power = c.power ?? normCdf((Math.log(pass) - Math.log(c.expected_hr)) / se);
  return { se, pass, power, mu: Math.log(c.expected_hr) };
}

function drawGauge(p) {
  const g = $("gauge");
  if (p === null || p === undefined) { g.innerHTML = `<text x="60" y="50" text-anchor="middle" font-size="11" fill="#6b7a90">n/a</text>`; return; }
  const a = Math.PI * (1 - p);
  const x = 60 + 48 * Math.cos(a), y = 60 - 48 * Math.sin(a);
  const color = p >= 0.7 ? "#087443" : p >= 0.4 ? "#a15c07" : "#b42318";
  g.innerHTML = `<path d="M12 60 A48 48 0 0 1 108 60" fill="none" stroke="#e2e7ee" stroke-width="10" stroke-linecap="round"/>` +
    `<path d="M12 60 A48 48 0 0 1 ${x.toFixed(1)} ${y.toFixed(1)}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"/>` +
    `<text x="60" y="56" text-anchor="middle" font-family="IBM Plex Mono" font-size="20" font-weight="500" fill="#142133">${pct(p)}</text>`;
}

const SUCCESS_CODES = new Set(["robust_success", "narrow_success", "success_with_caveats"]);

// The four checks as a causal chain: each link has a status, an answer and the evidence behind it.
function chainLinks(o) {
  const c = o.calculator, ci = o.calculator_inputs, g = (o.genetics || [])[0] || {};
  const ib = (ci.intended_biomarker || "").toLowerCase();
  const nonCausal = ib.includes("hdl") && !ib.includes("non-hdl") || ib.includes("pla2");
  const rrr = c.causal_rrr !== null && c.causal_rrr !== undefined ? Math.round(c.causal_rrr * 100) : null;
  const target = g.target || "the target";
  return [
    {
      from: `${target} protein`, to: "heart disease",
      q: "Does the drug's target cause heart disease in people?",
      ...({ causal: { a: "Yes", s: "ok", d: `Human genetics links ${target} to coronary disease${g.genetic_association_score ? ` (Open Targets score ${g.genetic_association_score})` : ""}.` },
            not_causal: { a: "No", s: "bad", d: `People born with less ${target} activity are not protected, so blocking it should not help.` } }[ci.target_genetics]
          || { a: "Unclear", s: "unk", d: "Not enough human genetic evidence published by the cut-off date." }),
    },
    {
      from: "drug", to: ci.intended_biomarker || "biomarker",
      q: "Does the drug change something that actually drives risk?",
      ...(nonCausal ? { a: "No", s: "bad", d: `It mainly changes ${ci.intended_biomarker}, which genetic studies show does not affect heart risk.` }
          : c.ldl_equivalent_mmol > 0.1 ? { a: "Yes", s: "ok", d: `It lowers ${ci.intended_biomarker || "a causal marker"} (worth ${num(c.ldl_equivalent_mmol, 2)} mmol/L of LDL-cholesterol).` }
          : c.expected_hr === null ? { a: "Unclear", s: "unk", d: `It acts on ${ci.intended_biomarker || "a pathway"} that has no validated size-of-benefit conversion.` }
          : { a: "Barely", s: "bad", d: "It hardly moves any marker known to cause heart disease." }),
    },
    {
      from: c.basis && c.basis !== "no lipid change" ? c.basis.replace(/ \(.*\)/, "") : ci.intended_biomarker || "biomarker", to: "fewer events",
      q: "How big should the benefit be?",
      ...(rrr === null ? { a: "Not computable", s: "unk", d: c.partial_expected_hr
            ? `The main mechanism has no validated size-of-benefit conversion. Lipid and blood-pressure changes alone would give ~${Math.round((1 - c.partial_expected_hr) * 100)}% fewer events (indicative, understates the drug).`
            : "The main mechanism has no validated size-of-benefit conversion, so the calculator abstains instead of guessing." }
          : rrr >= 20 ? { a: `~${rrr}% fewer events`, s: "ok", d: "A large expected benefit: easy to see in a typical outcome trial." }
          : rrr >= 10 ? { a: `~${rrr}% fewer events`, s: "warn", d: "A moderate expected benefit: needs a large trial to show." }
          : rrr >= 5 ? { a: `~${rrr}% fewer events`, s: "warn", d: "A small expected benefit: needs a very large, long trial." }
          : { a: `~${rrr}% fewer events`, s: "bad", d: "Practically no benefit expected." }),
    },
    {
      from: "fewer events", to: "trial result",
      q: "Could this trial see that benefit?",
      ...(c.power === null || c.power === undefined
          ? { a: "Unknown", s: "unk", d: (() => { const need = ((c.notes || []).find((n) => n.startsWith("events needed")) || "").match(/:\s*(\d+)/);
              return "The trial's planned number of events was not public, so its chance cannot be computed." +
                (need ? ` Proving this effect would take about ${need[1]} events.` : ""); })() }
          : c.power >= 0.8 ? { a: `Yes · ${pct(c.power)} chance`, s: "ok", d: `${ci.events} events give a good chance to detect it.` }
          : c.power >= 0.5 ? { a: `Maybe · ${pct(c.power)} chance`, s: "warn", d: `With ${ci.events} events there is a ${100 - Math.round(c.power * 100)}% chance of missing it even if the drug works as expected.` }
          : { a: `Unlikely · ${pct(c.power)} chance`, s: "bad", d: `${ci.events} events are far too few for an effect this small.` }),
    },
  ];
}

// Which link the diagnosis points at: the first broken one, else the one the primary cause names, else the weakest.
function focusLink(links, primary) {
  const bad = links.findIndex((l) => l.s === "bad");
  if (bad >= 0) return [bad, "broken"];
  const byCause = { target_not_causal: 0, biomarker_not_causal: 1 }[primary];
  if (byCause !== undefined) return [byCause, "broken"];
  const warn = links.findIndex((l) => l.s === "warn");
  if (warn >= 0) return [warn, "weakest"];
  const unk = links.findIndex((l) => l.s === "unk");
  return unk >= 0 ? [unk, "unknown"] : [-1, "intact"];
}

function chainEvidence(o, i) {
  const m = o.mechanism || {}, c = o.calculator, ci = o.calculator_inputs;
  const changeIds = (m.changes || []).flatMap((ch) => ch.fact_ids || []);
  // link 3 uses only the markers that count (causal ones); a non-causal marker's quotes belong to link 2
  const causalIds = (m.changes || []).filter((ch) => !(NON_CAUSAL_UI.test(String(ch.biomarker).replace(/\s/g, "")) && !/non-hdl/i.test(ch.biomarker)))
    .flatMap((ch) => ch.fact_ids || []);
  const idsIn = (text) => (String(text || "").match(/f_[0-9a-f]{8,}/g) || []);
  const byKey = (re) => o.facts.filter((f) => re.test(f.key)).map((f) => f.fact_id);
  const ids = [
    [...byKey(/^genetic/), ...idsIn(m.target_genetics_rationale)],
    changeIds,
    causalIds,
    [...(m.events_fact_ids || []), ...(m.followup_fact_ids || []), ...trialSizeFacts(o)],
  ][i];
  const t = trialOdds(o);
  const how = [
    m.target_genetics_rationale ? `Agent's reasoning: ${m.target_genetics_rationale}` : "",
    biomarkerLines(o),
    c.expected_hr ? [
      `1. Causal lowering (from link 2): ${num(c.ldl_equivalent_mmol, 2)} mmol/L of LDL-cholesterol equivalent.`,
      "2. The rule from dozens of statin trials (CTT): 1 mmol/L lower LDL kept for about 5 years → HR 0.78, i.e. 22% fewer heart attacks, strokes and cardiovascular deaths. More lowering → proportionally more benefit (on a log scale).",
      `3. Time: the benefit builds up over years (year 1 gives only about half of it). Follow-up here is ${ci.followup_years ? num(ci.followup_years, 1) + " years" : "unknown, so 5 years is assumed"}, so the drug gets ${Math.round(c.time_factor * 100)}% of the full 5-year effect (time factor ${num(c.time_factor)}).`,
      `4. Expected HR = 0.78 ^ (${num(c.time_factor)} × ${num(c.ldl_equivalent_mmol, 2)}) = ${num(c.expected_hr)} → about ${Math.round(c.causal_rrr * 100)}% fewer events.` +
        (c.bp_factor && c.bp_factor !== 1 ? ` Blood-pressure effect included (×${num(c.bp_factor)}).` : ""),
      `Rule of thumb: ≥20% is a large benefit, 10–20% moderate (needs a big trial), under 10% small (needs a very large, long trial), under 5% practically none.`,
    ] : (c.notes || []).join(" "),
    t ? [
      `1. Expected effect (from link 3): HR ${num(c.expected_hr)}.`,
      `2. Trial size: ${ci.events} primary events. The more events, the smaller the effect a trial can prove.`,
      `3. Pass mark: with ${ci.events} events the result must come out at HR ${num(t.pass, 3)} or lower to be statistically significant.`,
      `4. Chance to pass if the drug works exactly as expected: ${pct(t.power)} (Schoenfeld formula). The bell chart below the chain shows this.`,
    ] : (() => {
      const need = ((c.notes || []).find((n) => n.startsWith("events needed")) || "").match(/:\s*(\d+)/);
      return ["The trial's planned number of primary events is not known, so its chance of proving the effect cannot be computed.",
        need ? `For comparison: to have an 80% chance of proving HR ${num(c.expected_hr)}, a trial would need about ${need[1]} primary events.` : ""].filter(Boolean);
    })(),
  ][i];
  const assumptions = (i >= 2 ? c.assumptions || [] : []);
  return { ids: [...new Set(ids)].filter((id) => factsById[id]), how, assumptions };
}

// Size facts about THIS trial only: from its registry record, its result announcement, or a paper naming the trial
// (papers about other, older trials of the same drug also report patient numbers, and would only confuse).
let chainTrial = {};
function trialSizeFacts(o) {
  const acr = String(chainTrial.acronym || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const own = new Set((o.sources || []).filter((src) => ["registry", "press_release"].includes(src.type)
    || (acr && String(src.title || "").toLowerCase().replace(/[^a-z0-9]/g, "").includes(acr))).map((src) => src.source_id));
  return o.facts.filter((f) => /^(planned_events|n_randomized|median_followup)$/.test(f.key) && own.has(f.source_id)).map((f) => f.fact_id);
}

// Link 2 explained marker by marker: which measured changes count towards the benefit and which do not.
const NON_CAUSAL_UI = /^(hdl|lp-?pla2|spla2)/i;
function biomarkerLines(o) {
  const c = o.calculator, ci = o.calculator_inputs;
  const fmt = (ch) => {
    if (ch.abs_change !== null && ch.abs_change !== undefined) {
      const sign = ch.abs_change > 0 ? "+" : "−", v = Math.abs(ch.abs_change);
      const mg = /mmol/i.test(ch.unit || "") && /ldl|hdl|non-hdl|chol/i.test(ch.biomarker) ? ` (${sign}${num(v * 38.67, 0)} mg/dL)` : "";
      return `${sign}${num(v, 2)} ${ch.unit || ""}${mg}`;
    }
    return ch.pct_change !== null && ch.pct_change !== undefined ? `${ch.pct_change > 0 ? "+" : "−"}${num(Math.abs(ch.pct_change), 1)}%` : "change not quantified";
  };
  const lines = (ci.changes || []).map((ch) => {
    const nc = NON_CAUSAL_UI.test(ch.biomarker.replace(/\s/g, "")) && !/non-hdl/i.test(ch.biomarker);
    const main = ch.biomarker === ci.intended_biomarker ? " (the drug's main target)" : "";
    return `${ch.biomarker}${main}: ${fmt(ch)} → ${nc ? "NOT counted: human genetics shows this marker does not cause heart disease, so moving it brings no benefit"
      : "counted: a causal marker"}`;
  });
  if (c.ldl_equivalent_mmol !== null && c.ldl_equivalent_mmol !== undefined)
    lines.push(`What counts is converted into one common unit, LDL-cholesterol equivalent: ${num(c.ldl_equivalent_mmol, 2)} mmol/L (from ${c.basis}). Link 3 turns this into fewer heart attacks and strokes.`);
  return lines;
}

let chainState = null;

function renderChecks(o) {
  const links = chainLinks(o);
  const [fi, kind] = focusLink(links, o.verdict.primary_category);
  chainState = { o, links };
  $("chainTitle").innerHTML = kind === "broken" ? "Where the causal chain breaks"
    : kind === "weakest" ? "How we know &mdash; the causal chain and its weakest link"
    : kind === "intact" ? "How we know &mdash; every link in the causal chain holds"
    : "How we know &mdash; the causal chain, link by link";
  $("chainIntro").textContent = "A drug can only work if every link holds: the target causes the disease, the drug moves a causal biomarker, " +
    "that change is big enough to prevent events, and the trial is big enough to see it. Click a link to see the quotes behind it.";
  $("chain").innerHTML = links.map((l, i) => {
    const focus = i === fi ? ` link-focus link-${kind}` : "";
    const tag = i === fi ? `<span class="link-tag">${{ broken: "Broken link", weakest: "Weakest link", unknown: "Unknown link" }[kind] || ""}</span>` : "";
    const n = chainEvidence(o, i).ids.length;
    return `<button type="button" class="link cs cs-${l.s}${focus}" data-link="${i}">
      ${tag}<div class="link-path"><span class="link-n">${i + 1}</span><span>${esc(l.from)}</span><span class="link-arrow">→</span><span>${esc(l.to)}</span></div>
      <div class="cs-q">${esc(l.q)}</div><div class="cs-a">${esc(l.a)}</div><div class="cs-d">${esc(l.d)}</div>
      <div class="link-ev">${n ? `${n} quote${n > 1 ? "s" : ""} &amp; the calculation →` : "The calculation →"}</div></button>`;
  }).join("");
  document.querySelectorAll("#chain .link").forEach((b) => b.addEventListener("click", () => openLink(Number(b.dataset.link))));
}

function openLink(i) {
  const { o, links } = chainState, l = links[i], ev = chainEvidence(o, i);
  const titles = Object.fromEntries((o.sources || []).map((s) => [s.source_id || s.id, s.title]));
  $("drawerBody").innerHTML = `<div class="label">Link ${i + 1} · ${esc(l.from)} → ${esc(l.to)}</div>
    <h3 style="margin:6px 0 2px">${esc(l.q)}</h3><div class="cs-${l.s}"><span class="cs-a">${esc(l.a)}</span></div>
    <p>${esc(l.d)}</p>
    ${ev.how && ev.how.length ? `<div class="label" style="margin-top:14px">How the answer is computed</div>${Array.isArray(ev.how)
      ? `<ul class="how">${ev.how.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : `<p class="how">${esc(ev.how)}</p>`}` : ""}
    ${ev.assumptions.length ? `<div class="label">Assumptions</div><ul>${ev.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
    <div class="label" style="margin-top:14px">Evidence (verbatim quotes, checked against the source by code)</div>
    <p class="ev-legend">Each fact: the number, what it measures (marker · trial phase · time · dose), and how it was measured:
      <b>placebo-adj.</b> = drug group minus placebo group (the drug's own effect); <b>within-arm</b> = change in the drug group only,
      not minus placebo (an upper estimate). Below it, the exact sentence from the source and where it comes from.</p>
    ${ev.ids.length ? ev.ids.map((id) => { const f = factsById[id];
      return `<div class="ev-item"><div><b>${esc(f.value)} ${esc(f.unit || "")}</b> <span class="muted">${esc(f.key)}${factContext(f) ? " · " + esc(factContext(f)) : ""}</span>${verifyChip(id)}</div>
        <blockquote>“${esc(f.quote)}”</blockquote><div class="muted ev-src">${esc(titles[f.source_id] || f.source_id)} · ${esc(f.pub_date || "")}</div></div>`; }).join("")
      : '<p class="muted">No quoted facts for this link.</p>'}`;
  annotateTerms($("drawerBody"));
  $("drawer").classList.remove("hidden");
}

// Rounds of work separated by the critic: what the calculator said, what the critic found, what changed.
function parseRounds(trace) {
  const rounds = [];
  for (const e of trace) {
    const out = e.output_summary || "";
    if (e.action === "four_checks") {
      const m = out.match(/expected HR ([\d.]+|None).*?power ([\d.]+|None).*?-> (\w+)\/(\w+)/);
      rounds.push({ hr: m && m[1] !== "None" ? Number(m[1]) : null, power: m && m[2] !== "None" ? Number(m[2]) : null,
        outcome: m ? m[3] : "", category: m ? m[4] : "", selfCheck: [] });
    } else if (e.action === "self_check" && rounds.length) rounds[rounds.length - 1].selfCheck.push(out);
    else if (e.action === "differential_diagnosis" && rounds.length) {
      const m = out.match(/primary (\w+) \((\w+)\)/);
      if (m) Object.assign(rounds[rounds.length - 1], { primary: m[1], confidence: m[2] });
    }
  }
  return rounds;
}

const agentName = (a) => ({ case_builder: "the case builder", mechanism: "the mechanism analyst", investigator: "the investigator", reporter: "the reporter" }[a]
  || (a ? String(a).replace(/_/g, " ") : "the agents"));

// "8 remaining notes, 2 serious": what the last review still flags (serious = high severity).
function lastNotes(rev) {
  const all = rev.issues || [], serious = all.filter((x) => x.severity === "high").length;
  return `${all.length} remaining note${all.length === 1 ? "" : "s"}, ${serious ? `${serious} serious` : "none serious"}`;
}

async function renderCriticReview(res) {
  const panel = $("criticPanel"), o = res.output, reviews = o.critic || [];
  let trace = [];
  try { trace = await getJSON(`/api/runs/${encodeURIComponent(res.run_id)}/trace`); } catch { trace = []; }
  const rounds = parseRounds(trace);
  if (!reviews.length) { panel.classList.add("hidden"); return; }
  const roundBox = (r, i) => r ? `<div class="cr-round"><div class="label">Round ${i + 1} · calculator + investigator</div>
      <div class="cr-nums"><span><small>expected HR</small><b>${r.hr !== null ? num(r.hr) : "—"}</b></span>
      <span><small>chance to pass</small><b>${r.power !== null ? pct(r.power) : "—"}</b></span></div>
      <div class="cr-cat">${esc(CATEGORY[r.primary] || CATEGORY[r.category] || r.category)}${r.confidence ? ` · ${esc(r.confidence)} confidence` : ""}</div></div>` : "";
  const issues = (rev) => (rev.issues || []).slice().sort((a, b) => ({ high: 0, medium: 1, low: 2 }[a.severity] - { high: 0, medium: 1, low: 2 }[b.severity]));
  const first = reviews[0], high = (first.issues || []).filter((x) => x.severity === "high");
  if (reviews.length > 1 && rounds.length > 1) {
    const [r1, r2] = [rounds[0], rounds[rounds.length - 1]];
    // A material change only: a new diagnosis, a different chance as shown (whole percent) or an HR that moved by 0.01
    // or more. 1.033 -> 1.031 with the same 3% and the same diagnosis means the answer held.
    const moved = (a, b, same) => (a === null) !== (b === null) || (a !== null && !same(a, b));
    const changed = moved(r1.hr, r2.hr, (a, b) => Math.abs(a - b) < 0.01)
      || moved(r1.power, r2.power, (a, b) => Math.round(a * 100) === Math.round(b * 100))
      || (r1.primary || r1.category) !== (r2.primary || r2.category);
    $("criticTitle").textContent = changed ? "The critic changed the answer" : "The critic sent the work back; the answer held";
    $("criticReview").innerHTML = `<div class="cr-flow">
      ${roundBox(r1, 0)}
      <div class="cr-critic"><div class="label">Critic · ${high.length} serious issue${high.length === 1 ? "" : "s"} → sent back to ${esc(agentName(high[0]?.target_agent))}</div>
        ${high.slice(0, 2).map((x) => `<p>“${esc(x.issue)}”</p>`).join("")}</div>
      ${roundBox(r2, rounds.length - 1)}</div>
      <p class="cr-note">${changed ? "After the critic's objection the mechanism analyst re-read the evidence and the calculator ran again. "
        : "The mechanism analyst re-checked the evidence; the diagnosis and the chance stayed the same. "}
      Second review: ${lastNotes(reviews[reviews.length - 1])}.</p>`;
  } else {
    const list = issues(first).slice(0, 3);
    $("criticTitle").textContent = "The critic's review";
    $("criticReview").innerHTML = `<p class="cr-note">A separate critic agent checked every citation and number before the report went out.
      ${first.verdict_ok === false ? "It raised objections that the agents addressed." : "It found no serious problem"}${list.length ? ", and left these notes:" : "."}</p>
      ${list.length ? `<ul class="cr-list">${list.map((x) => `<li><span class="sev sev-${esc(x.severity)}">${esc(x.severity)}</span> ${esc(x.issue)}</li>`).join("")}</ul>` : ""}`;
  }
  panel.classList.remove("hidden");
  annotateTerms($("criticReview"));
}

// Time machine: every document on a date axis; the agents saw only what was published before the cut-off.
async function renderTimeMachine(res) {
  const panel = $("tmPanel");
  let tl;
  try { tl = await getJSON(`/api/runs/${encodeURIComponent(res.run_id)}/timeline`); } catch { panel.classList.add("hidden"); return; }
  if (!tl.as_of || !tl.seen.length) { panel.classList.add("hidden"); return; }
  const day = (d) => Date.parse(String(d).length === 7 ? `${d}-01` : d);
  const docs = [...tl.seen.map((d) => ({ ...d, seen: true })), ...tl.hidden.map((d) => ({ ...d, seen: false }))]
    .filter((d) => !Number.isNaN(day(d.date))).map((d) => ({ ...d, t: day(d.date) }));
  const cut = day(tl.as_of), now = Date.now();
  const t0 = Math.min(...docs.map((d) => d.t)), t1 = Math.max(now, ...docs.map((d) => d.t));
  const y0 = new Date(t0).getUTCFullYear(), y1 = new Date(t1).getUTCFullYear() + 1;
  const lo = Date.UTC(y0, 0, 1), hi = Date.UTC(y1, 0, 1);
  const W = 900, H = 205, L = 20, R = 20, mid = 120;
  const X = (t) => L + (t - lo) / (hi - lo) * (W - L - R);
  // stack dots that fall in the same place
  const slots = {};
  docs.sort((a, b) => a.t - b.t).forEach((d) => {
    const key = `${d.seen}-${Math.round(X(d.t) / 12)}`;
    d.k = slots[key] = (slots[key] ?? -1) + 1;
  });
  const step = Math.max(1, Math.ceil((y1 - y0) / 12));
  let g = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Documents by publication date">`;
  for (let y = y0; y <= y1; y += step) g += `<line class="tm-tick" x1="${X(Date.UTC(y, 0, 1))}" y1="${mid - 4}" x2="${X(Date.UTC(y, 0, 1))}" y2="${mid + 4}"/>` +
    `<text class="tick" x="${X(Date.UTC(y, 0, 1))}" y="${mid + 18}" text-anchor="middle">${y}</text>`;
  g += `<line class="tm-axis" x1="${L}" y1="${mid}" x2="${W - R}" y2="${mid}"/>`;
  g += `<text class="tm-side" x="${L}" y="16">Read by the agents (published before the cut-off)</text>` +
    (X(cut) > 380 ? `<text class="tm-side" x="${L}" y="${mid + 44}">Published later: hidden from the agents →</text>`
      : `<text class="tm-side" x="${W - R}" y="${H - 4}" text-anchor="end">Published later: hidden from the agents</text>`);
  docs.forEach((d, i) => {
    const y = d.seen ? mid - 18 - d.k * 13 : mid + 34 + d.k * 13;
    g += `<circle class="tm-dot ${d.seen ? "tm-seen" : "tm-hidden"}" cx="${X(d.t)}" cy="${y}" r="5" data-i="${i}"/>`;
  });
  g += `<g id="tmCut"><line class="tm-cut" x1="0" y1="24" x2="0" y2="${H - 22}"/><text class="tm-cut-l" x="6" y="34"></text></g>`;
  g += `</svg><div class="cal-tip hidden" id="tmTip"></div>`;
  $("tmChart").innerHTML = g;
  const later = tl.hidden.length;
  $("tmIntro").textContent = `The agents analysed this trial as of ${tl.as_of}. They were given only documents published before that date. ` +
    (later ? `The ${later} documents below the line came out later (full results, genetics) and were never shown to them.` : "Drag the date to see what was known when.");
  const slider = $("tmSlider");
  Object.assign(slider, { min: lo, max: hi, step: 86400000, value: cut });
  const setDate = (t) => {
    $("tmCut").setAttribute("transform", `translate(${X(t)},0)`);
    const label = new Date(t).toISOString().slice(0, 10);
    document.querySelector(".tm-cut-l").textContent = t === cut ? `cut-off ${label}` : label;
    $("tmDate").textContent = label;
    let n = 0;
    $("tmChart").querySelectorAll(".tm-dot").forEach((c) => { const on = docs[Number(c.dataset.i)].t <= t; c.classList.toggle("tm-off", !on); n += on; });
    const notYet = docs.filter((d) => d.t > t);
    $("tmCount").innerHTML = `On <b>${label}</b> an analyst could read <b>${n}</b> of these ${docs.length} documents.` +
      (notYet.length ? ` Not yet published: ${notYet.slice(0, 3).map((d) => `<i>${esc((d.title || "").slice(0, 70))}</i> (${esc(String(d.date).slice(0, 4))})`).join("; ")}${notYet.length > 3 ? ` and ${notYet.length - 3} more` : ""}.` : "");
  };
  slider.oninput = () => setDate(Number(slider.value));
  $("tmReset").onclick = () => { slider.value = cut; setDate(cut); };
  setDate(cut);
  const tip = $("tmTip"), chart = $("tmChart");
  chart.querySelectorAll(".tm-dot").forEach((c) => {
    c.addEventListener("mouseenter", () => {
      const d = docs[Number(c.dataset.i)], box = chart.getBoundingClientRect(), r = c.getBoundingClientRect();
      tip.innerHTML = `<b>${esc(String(d.date).slice(0, 10))}</b> · ${esc(d.type || "")}<br>${esc((d.title || "").slice(0, 90))}<br>${d.seen ? "read by the agents" : "hidden: published after the cut-off"}`;
      tip.style.left = `${r.left - box.left + r.width / 2}px`; tip.style.top = `${r.top - box.top}px`;
      tip.classList.remove("hidden");
    });
    c.addEventListener("mouseleave", () => tip.classList.add("hidden"));
    c.addEventListener("click", () => { const d = docs[Number(c.dataset.i)]; if (d.url) window.open(d.url, "_blank", "noopener"); });
  });
  panel.classList.remove("hidden");
}

// A falsifiable prediction committed to git before the full results: shown with its HR range and check date.
async function renderPrereg(res) {
  const box = $("prereg");
  let list = [];
  try { list = (await getJSON("/api/predictions")).predictions || []; } catch { list = []; }
  const p = list.find((x) => x.nct_id === (res.trial || {}).nct_id);
  if (!p) { box.classList.add("hidden"); return; }
  const lo = 0.6, hi = 1.3, W = 520;
  const X = (v) => (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * W;
  const days = Math.ceil((Date.parse(p.verify_on) - Date.now()) / 86400000);
  const status = p.status === "pending" ? (days > 0 ? `checked in ${days} day${days === 1 ? "" : "s"}` : "awaiting the full results")
    : p.status === "confirmed" ? "confirmed" : "refuted";
  box.innerHTML = `<div class="pr-head"><span class="pr-badge">Pre-registered prediction</span>
      <span class="muted">committed ${esc(p.made_on)} · git <span class="mono">${esc(p.commit)}</span> · ${esc(status)}: ${esc(p.verify_event)} (${esc(p.verify_on)})</span></div>
    <p class="pr-text">${esc(p.statement)}</p>
    <svg viewBox="-10 0 ${W + 20} 58" class="pr-scale" role="img" aria-label="Predicted hazard ratio range">
      <line x1="0" y1="26" x2="${W}" y2="26" class="pr-axis"/>
      <rect x="${X(p.range[0])}" y="16" width="${X(p.range[1]) - X(p.range[0])}" height="20" rx="4" class="pr-range"/>
      <rect x="${X(p.likely[0])}" y="16" width="${X(p.likely[1]) - X(p.likely[0])}" height="20" rx="4" class="pr-likely"/>
      <line x1="${X(1)}" y1="8" x2="${X(1)}" y2="44" class="pr-null"/>
      ${p.observed_hr ? `<circle cx="${X(p.observed_hr)}" cy="26" r="6" class="pr-obs"/>` : ""}
      ${[0.7, 0.8, 0.9, 1.0, 1.1, 1.2].map((v) => `<text x="${X(v)}" y="54" text-anchor="middle" class="tick">${v.toFixed(1)}</text>`).join("")}
    </svg>
    <div class="legend"><span><i class="lg pr-lg-range"></i> predicted range ${p.range[0].toFixed(2)}–${p.range[1].toFixed(2)}</span><span><i class="lg pr-lg-likely"></i> most likely ${p.likely[0].toFixed(2)}–${p.likely[1].toFixed(2)}</span>
      <span class="muted">Proven wrong if: ${esc(p.falsified_if)}.</span></div>`;
  box.classList.remove("hidden");
}

// What the analysis used: model calls and tokens per agent, the run time, and what the same tokens would cost at a
// closed model's list price. Nemotron runs on NVIDIA's developer API, or on the company's own hardware.
async function renderCost(res) {
  const box = $("costPanel"), usage = (res.output || {}).usage;
  if (!usage || !Object.keys(usage).length) { box.innerHTML = '<p class="muted">No token usage recorded for this run.</p>'; return; }
  let info;
  try { info = await getJSON("/api/models"); } catch { box.innerHTML = ""; return; }
  const price = (model) => {
    const m = String((info.models[model] || {}).price || "").match(/\$([\d.]+)\s*\/\s*\$([\d.]+)/);
    return m ? [Number(m[1]), Number(m[2])] : null;   // $ per million input / output tokens; null = no list price
  };
  const cost = (u, model) => { const pr = price(model); return pr ? (u.input_tokens * pr[0] + u.output_tokens * pr[1]) / 1e6 : 0; };
  const ref = "claude-opus-5-5";
  let tokIn = 0, tokOut = 0, calls = 0, refCost = 0, estimated = false;
  const onNvidia = Object.values(usage).every((u) => providerOf(u.model) === "nvidia");
  const viaMeterless = Object.values(usage || {}).some((u) => u && u.provider === "meterless");
  const rows = Object.entries(usage).map(([agent, u]) => {
    tokIn += u.input_tokens; tokOut += u.output_tokens; calls += u.calls; estimated = estimated || u.estimated;
    const c = cost(u, ref); refCost += c;
    return `<tr><td>${esc(agent)}</td><td class="mono">${u.calls}</td><td class="mono">${(u.input_tokens / 1000).toFixed(1)}k / ${(u.output_tokens / 1000).toFixed(1)}k</td>
      <td>${providerOf(u.model) ? logoImg(providerOf(u.model), 14) + " " : ""}${esc(modelName(u.model))}</td><td class="mono">$${c.toFixed(3)}</td></tr>`;
  }).join("");
  const liveSeconds = res.execution_time_seconds > 5 ? res.execution_time_seconds : res.first_live_run_seconds;
  const minutes = liveSeconds ? `${Math.max(1, Math.round(liveSeconds / 60))} min` : "replayed";
  const liveRun = res.execution_time_seconds > 5 ? "live run" : "first live run";
  box.innerHTML = `<div class="cost-tiles">
      <div class="acc-tile"><div class="acc-n">${Math.round((tokIn + tokOut) / 1000)}k</div><div class="acc-l">model tokens, ${calls} calls</div><div class="acc-s">${(tokIn / 1000).toFixed(0)}k read · ${(tokOut / 1000).toFixed(0)}k written</div></div>
      <div class="acc-tile"><div class="acc-n">${esc(minutes)}</div><div class="acc-l">analysis time</div><div class="acc-s">${liveSeconds ? (viaMeterless ? `${liveRun} through Meterless Nightshift` : `${liveRun} on NVIDIA's API`) : "opened from the recording"}</div></div>
      <div class="acc-tile"><div class="acc-n">${onNvidia ? "Open" : "Mixed"}</div><div class="acc-l">${onNvidia ? "NVIDIA Nemotron models" : "models in this run"}</div><div class="acc-s">${onNvidia ? (viaMeterless ? "served by Meterless Nightshift, on a Mac or the company's own DGX Spark" : "NVIDIA's developer API, or the company's own hardware") : ""}</div></div>
      <div class="acc-tile"><div class="acc-n">$${refCost.toFixed(2)}</div><div class="acc-l">the same tokens at ${esc(modelName(ref))} list prices</div><div class="acc-s">for comparison only</div></div></div>
    <div class="cal-table-wrap"><table class="cal-table"><thead><tr><th>Agent</th><th>Calls</th><th>Tokens in / out</th><th>Model</th><th>At ${esc(modelName(ref))} prices</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="home-note">${onNvidia ? `Every agent runs on open NVIDIA Nemotron models ${viaMeterless ? "through Meterless Runtime (Nightshift)" : "through NVIDIA's API"}; the models are open, so a company can run the same analysis on its own hardware and keep trial data inside. ` : ""}The last column prices the same token counts at ${esc(modelName(ref))}'s list price (per million tokens) as a reference.
      ${estimated ? "This run was replayed from recordings without billing data, so token counts are estimated from the text length (~4 characters per token)." : "Token counts are the usage reported by the API."}</p>`;
}

/* ---------- Where the drug acts: a map of the cholesterol and plaque pathway ---------- */
const PATHWAY = {
  NPC1L1: { x: 100, y: 200, name: "NPC1L1", role: "absorbs food cholesterol", drugs: "ezetimibe",
    note: "Ezetimibe blocks NPC1L1 in the gut, so less cholesterol from food reaches the liver and the blood. LDL falls modestly." },
  ACLY: { x: 300, y: 90, name: "ACLY", role: "starts cholesterol making", drugs: "bempedoic acid",
    note: "Bempedoic acid blocks ACLY at the start of the liver's cholesterol factory. The liver then pulls more LDL out of the blood." },
  HMGCR: { x: 475, y: 90, name: "HMGCR", role: "makes cholesterol", drugs: "statins",
    note: "Statins block HMGCR, further down the same factory. This is the benchmark: every 1 mmol/L of LDL lowering cuts events by about 22%." },
  PCSK9: { x: 300, y: 215, name: "PCSK9", role: "destroys LDL receptors", drugs: "evolocumab · alirocumab · inclisiran",
    note: "PCSK9 destroys the liver's LDL receptors. Blocking it leaves more receptors to catch LDL, so blood LDL falls by about 60%." },
  LDLR: { x: 475, y: 215, name: "LDL receptor", role: "pulls LDL from blood", drugs: "", note: "The liver's LDL receptors catch LDL particles from the blood. Most LDL-lowering drugs work by making more of them." },
  LPA: { x: 300, y: 315, name: "LPA gene → apo(a)", role: "makes Lp(a)", drugs: "pelacarsen · olpasiran · lepodisiran",
    note: "The liver builds Lp(a) using the LPA gene. These drugs silence the gene's message, so Lp(a) in the blood falls by 80% or more." },
  CETP: { x: 770, y: 62, name: "CETP", role: "moves cholesterol HDL→LDL", drugs: "torcetrapib · dalcetrapib · evacetrapib · anacetrapib",
    note: "Blocking CETP keeps cholesterol in HDL (“good” cholesterol doubles) and lowers LDL a little. Genetics later showed only the LDL/apoB part protects." },
  HCAR2: { x: 888, y: 135, name: "HCAR2", role: "raises HDL (niacin)", drugs: "niacin",
    note: "Niacin raises HDL and lowers LDL slightly. Raising HDL itself does not prevent heart attacks." },
  PLA2G7: { x: 665, y: 250, name: "Lp-PLA2", role: "plaque enzyme", drugs: "darapladib",
    note: "Darapladib blocks the plaque enzyme Lp-PLA2. People born without working Lp-PLA2 are not protected: the target does not cause disease." },
  PLA2G2A: { x: 885, y: 250, name: "sPLA2-IIA", role: "plaque enzyme", drugs: "varespladib",
    note: "Varespladib lowers sPLA2-IIA, but that enzyme has no proven causal role in heart attacks." },
  IL1B: { x: 665, y: 320, name: "IL-1β", role: "inflammation signal", drugs: "canakinumab", note: "Canakinumab blocks IL-1β, damping the inflammation in plaques. It works without lowering cholesterol." },
  IL6: { x: 885, y: 320, name: "IL-6", role: "inflammation signal", drugs: "ziltivekimab", note: "Ziltivekimab blocks IL-6, the inflammation signal downstream of IL-1β." },
};
const DRUG_TARGET = { niacin: "HCAR2" };

function pathwaySVG(active) {
  const W = 150, H = 44;
  const node = (id) => { const n = PATHWAY[id]; const on = id === active;
    return `<g class="pw-node${on ? " pw-on" : ""}${n.drugs ? "" : " pw-plain"}" data-id="${id}" transform="translate(${n.x - W / 2},${n.y - H / 2})">
      ${on ? `<rect class="pw-halo" x="-6" y="-6" width="${W + 12}" height="${H + 12}" rx="14"/>` : ""}
      <rect width="${W}" height="${H}" rx="9"/><text x="${W / 2}" y="18" text-anchor="middle" class="pw-name">${esc(n.name)}</text>
      <text x="${W / 2}" y="34" text-anchor="middle" class="pw-role">${esc(n.role)}</text></g>`; };
  return `<svg viewBox="0 0 980 372" role="img" aria-label="Cholesterol and plaque pathway with drug targets">
    <defs><marker id="pw-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#9aa8b8"/></marker></defs>
    <rect class="pw-zone pw-gut" x="8" y="8" width="185" height="356" rx="16"/><text class="pw-zt" x="24" y="32">Gut</text>
    <rect class="pw-zone pw-liver" x="203" y="8" width="365" height="356" rx="16"/><text class="pw-zt" x="219" y="32">Liver cell</text>
    <rect class="pw-zone pw-blood" x="578" y="8" width="394" height="170" rx="16"/><text class="pw-zt" x="594" y="32">Blood</text>
    <rect class="pw-zone pw-artery" x="578" y="190" width="394" height="174" rx="16"/><text class="pw-zt" x="594" y="214">Artery wall</text>
    <text class="pw-small" x="100" y="96" text-anchor="middle">cholesterol in food</text>
    <path class="pw-flow" d="M100 104 L100 172" marker-end="url(#pw-arr)"/>
    <path class="pw-flow" d="M176 200 L222 200" marker-end="url(#pw-arr)"/><text class="pw-small" x="199" y="190" text-anchor="middle">to liver</text>
    <text class="pw-small" x="300" y="52" text-anchor="middle">citrate</text><path class="pw-flow" d="M300 57 L300 66" marker-end="url(#pw-arr)"/>
    <path class="pw-flow" d="M376 90 L398 90" marker-end="url(#pw-arr)"/>
    <path class="pw-flow" d="M475 113 L475 138" marker-end="url(#pw-arr)"/><text class="pw-small" x="475" y="153" text-anchor="middle">cholesterol</text>
    <path class="pw-block" d="M376 215 L398 215"/><text class="pw-small" x="387" y="184" text-anchor="middle">breaks receptors</text>
    <circle class="pw-ldl" cx="632" cy="62" r="18"/><text class="pw-pt" x="632" y="66" text-anchor="middle">LDL</text>
    <circle class="pw-lpa" cx="632" cy="130" r="17"/><text class="pw-pt" x="632" y="134" text-anchor="middle">Lp(a)</text>
    <circle class="pw-hdl" cx="922" cy="62" r="18"/><text class="pw-pt" x="922" y="66" text-anchor="middle">HDL</text>
    <path class="pw-flow" d="M903 62 L847 62" marker-end="url(#pw-arr)"/><path class="pw-flow" d="M694 62 L652 62" marker-end="url(#pw-arr)"/>
    <path class="pw-flow" d="M922 113 L922 82" marker-end="url(#pw-arr)"/>
    <path class="pw-flow" d="M614 70 C585 120 575 200 552 214" marker-end="url(#pw-arr)"/><text class="pw-small" x="560" y="140" text-anchor="end">caught</text>
    <path class="pw-flow" d="M376 315 C560 325 592 205 614 140" marker-end="url(#pw-arr)"/><text class="pw-small" x="478" y="338" text-anchor="middle">released into blood</text>
    <ellipse class="pw-plaque" cx="775" cy="285" rx="44" ry="24"/><text class="pw-pt" x="775" y="289" text-anchor="middle">plaque</text>
    <path class="pw-flow" d="M647 140 C700 175 770 190 773 259" marker-end="url(#pw-arr)"/><text class="pw-small" x="782" y="223">LDL &amp; Lp(a) enter</text>
    <text class="pw-small" x="775" y="358" text-anchor="middle">grows → heart attack, stroke</text>
    ${Object.keys(PATHWAY).map(node).join("")}
  </svg>`;
}

function renderPathway(res) {
  const panel = $("pathwayPanel");
  const t = ((res.drug || {}).targets || [])[0];
  const active = (t && PATHWAY[t.symbol] ? t.symbol : null) || DRUG_TARGET[String(res.drug.name || "").toLowerCase()];
  if (!active) { panel.classList.add("hidden"); return; }
  const show = (id, isThis) => {
    const n = PATHWAY[id];
    $("pathway").innerHTML = pathwaySVG(id);
    centerWide($("pathway"));
    $("pathwayNote").innerHTML = `<span class="pw-badge${isThis ? "" : " pw-other"}">${isThis ? `This trial: ${esc(res.drug.name)}` : "Other target"}</span>
      <b>${esc(n.name)}</b>${n.drugs ? ` <span class="muted">· ${esc(n.drugs)}</span>` : ""}<p>${esc(n.note)}</p>` +
      (isThis ? "" : `<button type="button" class="btn-small" id="pwBack">Back to ${esc(res.drug.name)}</button>`);
    $("pathway").querySelectorAll(".pw-node").forEach((g) => g.addEventListener("click", () => show(g.dataset.id, g.dataset.id === active)));
    const back = $("pwBack"); if (back) back.addEventListener("click", () => show(active, true));
  };
  show(active, true);
  panel.classList.remove("hidden");
}

/* ---------- Evidence map: sources → quotes → inputs → calculation → verdict ---------- */
let mapRes = null;

function fmtChangeVal(ch) {
  if (ch.abs_change !== null && ch.abs_change !== undefined) return `${ch.abs_change > 0 ? "+" : "−"}${num(Math.abs(ch.abs_change), 2)} ${ch.unit || ""}`.trim();
  if (ch.pct_change !== null && ch.pct_change !== undefined) return `${ch.pct_change > 0 ? "+" : "−"}${num(Math.abs(ch.pct_change), 1)}%`;
  return "";
}

function buildEvidenceGraph(res) {
  const o = res.output, m = o.mechanism || {}, c = o.calculator, ci = o.calculator_inputs, v = o.verdict;
  const facts = Object.fromEntries(o.facts.map((f) => [f.fact_id, f]));
  const srcs = Object.fromEntries((o.sources || []).map((x) => [x.source_id || x.id, x]));
  const nodes = {}, edges = [];
  const add = (id, col, label, sub, cls, tip) => { if (!nodes[id]) nodes[id] = { id, col, label, sub, cls: cls || "", tip: tip || "", notes: [] }; return id; };
  const link = (a, b) => { if (a && b && !edges.some((e) => e.a === a && e.b === b)) edges.push({ a, b }); };
  const short = (t) => { t = String(t || "").replace(/&lt;\/?i&gt;/g, ""); return t.length > 58 ? t.slice(0, 56).replace(/\s+\S*$/, "") + "…" : t; };
  const factNode = (fid) => {
    const f = facts[fid]; if (!f) return null;
    const src = srcs[f.source_id] || {};
    const sid = add(`s:${f.source_id}`, 0, short(src.title || f.source_id), [src.type, String(f.pub_date || src.pub_date || "").slice(0, 4)].filter(Boolean).join(" · "), "ev-src",
      `${src.title || f.source_id}${src.pub_date ? " · " + src.pub_date : ""}`);
    const val = typeof f.value === "string" && f.value.length > 40 ? f.value.slice(0, 38) + "…" : `${f.value} ${f.unit || ""}`.trim();
    const id = add(`f:${fid}`, 1, val, String(f.key).replace(/_/g, " "), "ev-fact", `“${f.quote}”\n— ${src.title || f.source_id}`);
    link(sid, id);
    return id;
  };
  const L = chainLinks(o);
  const [olabel] = OUTCOME[v.outcome] || [v.outcome];
  const vd = add("v", 4, CATEGORY[v.primary_category] || v.primary_category, `${olabel} · confidence ${v.confidence}`, "ev-verdict",
    o.report && o.report.headline ? o.report.headline : "");
  const c1 = add("c:1", 3, `Link 1 · ${L[0].a}`, "does the target cause the disease?", `ev-calc ev-${L[0].s}`, L[0].d);
  const c2 = add("c:2", 3, `Link 2 · ${L[1].a}`, "does the drug move a causal marker?", `ev-calc ev-${L[1].s}`, L[1].d);
  const ceq = c.ldl_equivalent_mmol !== null && c.ldl_equivalent_mmol !== undefined
    ? add("c:eq", 3, `${num(c.ldl_equivalent_mmol, 2)} mmol/L`, "LDL-cholesterol equivalent (what counts)", "ev-calc", `Counted lowering, converted to one unit (via ${c.basis}).`) : null;
  const c3 = add("c:3", 3, `Link 3 · ${L[2].a}`, c.expected_hr ? `expected HR ${num(c.expected_hr)}` : "how big a benefit?", `ev-calc ev-${L[2].s}`, L[2].d);
  const c4 = add("c:4", 3, `Link 4 · ${L[3].a}`, "could this trial see it?", `ev-calc ev-${L[3].s}`, L[3].d);
  [c1, c2, c3, c4].forEach((x) => link(x, vd));
  if (ceq) { link(ceq, c3); } else link(c2, c3);
  link(c3, c4);

  const gi = add("i:gen", 2, `Target genetics: ${ci.target_genetics}`, "human genetic evidence", "ev-input", m.target_genetics_rationale || "");
  link(gi, c1);
  const genIds = [...o.facts.filter((f) => /^genetic/.test(f.key)).map((f) => f.fact_id), ...(String(m.target_genetics_rationale || "").match(/f_[0-9a-f]{8,}/g) || [])];
  [...new Set(genIds)].slice(0, 4).forEach((fid) => link(factNode(fid), gi));
  (m.changes || []).forEach((ch, i) => {
    const nc = NON_CAUSAL_UI.test(String(ch.biomarker).replace(/\s/g, "")) && !/non-hdl/i.test(ch.biomarker);
    const id = add(`i:ch${i}`, 2, `${ch.biomarker} ${fmtChangeVal(ch)}`, nc ? "not counted: not a cause of heart disease" : "counted: causal marker",
      `ev-input${nc ? " ev-off" : ""}`, ch.method || "");
    link(id, c2);
    if (!nc && ceq) link(id, ceq);
    (ch.fact_ids || []).slice(0, 4).forEach((fid) => link(factNode(fid), id));
  });
  if (ci.followup_years) {
    const id = add("i:fu", 2, `Follow-up ${num(ci.followup_years, 1)} years`, "trial length", "ev-input");
    link(id, c3); (m.followup_fact_ids || []).slice(0, 3).forEach((fid) => link(factNode(fid), id));
  }
  if (ci.events) {
    const id = add("i:ev", 2, `${ci.events} events`, m.events_basis ? `trial size (${m.events_basis})` : "trial size", "ev-input");
    link(id, c4); (m.events_fact_ids || []).slice(0, 3).forEach((fid) => link(factNode(fid), id));
  }
  const outIds = (m.outcome_fact_ids || []).filter((fid) => facts[fid] && /readout|stopped|result/.test(facts[fid].key));
  if (outIds.length) {
    const id = add("i:out", 2, `Announced result: ${olabel}`, "what actually happened", "ev-input ev-result");
    link(id, vd); outIds.slice(0, 2).forEach((fid) => link(factNode(fid), id));
  }
  // the critic's remaining notes, attached to the inputs they are about
  const last = (o.critic || []).slice(-1)[0] || {};
  for (const iss of last.issues || []) {
    const t = String(iss.issue || "").toLowerCase();
    Object.values(nodes).filter((n) => n.col === 2).forEach((n) => {
      const key = n.id === "i:fu" ? "follow-up" : n.id === "i:ev" ? "event" : n.id === "i:gen" ? "genetic" : n.label.split(" ")[0].toLowerCase();
      if (key && t.includes(key)) n.notes.push(`[${iss.severity}] ${iss.issue}`);
    });
  }
  return { nodes: Object.values(nodes), edges };
}

function renderEvidenceMap() {
  if (!mapRes) return;
  const g = buildEvidenceGraph(mapRes);
  const cols = [[], [], [], [], []];
  g.nodes.forEach((n) => cols[n.col].push(n));
  const heads = ["1 · Source documents", "2 · Verbatim quotes", "3 · What the analyst used", "4 · Calculation (code)", "5 · Verdict"];
  $("evMap").innerHTML = `<div class="ev-inner"><svg class="ev-lines" aria-hidden="true"></svg><div class="ev-cols">${cols.map((col, i) =>
    `<div class="ev-col"><div class="ev-h">${heads[i]}</div>${col.map((n) => `<div class="ev-node ${n.cls}" data-id="${esc(n.id)}" tabindex="0">
      <b>${esc(n.label)}</b><span>${esc(n.sub || "")}</span>${n.notes.length ? `<i class="ev-note">critic note${n.notes.length > 1 ? "s" : ""} · ${n.notes.length}</i>` : ""}</div>`).join("")}</div>`).join("")}</div></div>
    <div class="ev-detail" id="evDetail">Click a box to trace where it comes from and what it feeds.</div>`;
  const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
  let onSet = new Set();
  const paintEdges = () => $("evMap").querySelectorAll(".ev-edge").forEach((el) => el.classList.toggle("on", onSet.has(el.dataset.a) && onSet.has(el.dataset.b)));
  const draw = () => {
    const inner = $("evMap").querySelector(".ev-inner"), svg = inner.querySelector(".ev-lines");
    const box = inner.getBoundingClientRect();
    svg.setAttribute("width", inner.scrollWidth); svg.setAttribute("height", inner.scrollHeight);
    svg.innerHTML = g.edges.map((e) => {
      const a = inner.querySelector(`[data-id="${CSS.escape(e.a)}"]`), b = inner.querySelector(`[data-id="${CSS.escape(e.b)}"]`);
      if (!a || !b) return "";
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const x1 = ra.right - box.left, y1 = ra.top + ra.height / 2 - box.top, x2 = rb.left - box.left, y2 = rb.top + rb.height / 2 - box.top;
      const dx = Math.max(24, (x2 - x1) / 2);
      return `<path class="ev-edge" data-a="${esc(e.a)}" data-b="${esc(e.b)}" d="M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}"/>`;
    }).join("");
    paintEdges();
  };
  draw();
  const select = (id) => {
    const up = new Set([id]), down = new Set([id]);
    let grew = true;
    while (grew) { grew = false; for (const e of g.edges) { if (up.has(e.b) && !up.has(e.a)) { up.add(e.a); grew = true; } if (down.has(e.a) && !down.has(e.b)) { down.add(e.b); grew = true; } } }
    const on = new Set([...up, ...down]);
    onSet = on;
    $("evMap").querySelectorAll(".ev-node").forEach((el) => { el.classList.toggle("on", on.has(el.dataset.id)); el.classList.toggle("dim", !on.has(el.dataset.id)); el.classList.toggle("sel", el.dataset.id === id); });
    paintEdges();
    const n = byId[id];
    $("evDetail").innerHTML = `<b>${esc(n.label)}</b> <span class="muted">${esc(n.sub || "")}</span>${n.tip ? `<div class="ev-tip">${esc(n.tip)}</div>` : ""}` +
      (n.notes.length ? `<div class="ev-crit">${n.notes.map((x) => `<div>Critic: ${esc(x)}</div>`).join("")}</div>` : "");
  };
  $("evMap").querySelectorAll(".ev-node").forEach((el) => {
    el.addEventListener("click", () => select(el.dataset.id));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") select(el.dataset.id); });
  });
  select("v");
  window.requestAnimationFrame(draw);
}
window.addEventListener("resize", () => { if (!$("tabMap").classList.contains("hidden")) renderEvidenceMap(); });

// When the result (or the agents) go against the calculator, say so up front and show why.
function renderDivergence(o, mode) {
  const v = o.verdict, c = o.calculator, box = $("divergence");
  const calcSuccess = ["success", "likely_success"].includes(c.outcome);
  const against = (v.consistency || "").includes("remaining")
    || (mode === "forecast" && c.power !== null && calcSuccess && !SUCCESS_CODES.has(v.primary_category) && v.primary_category !== "insufficient_data");
  if (!against) { box.classList.add("hidden"); return; }
  const primary = (o.hypotheses || []).find((h) => h.category === v.primary_category) || {};
  const reasons = [...(c.assumptions || []).map((a) => `Calculator assumption: ${a}.`), ...(primary.for || []).slice(0, 2).map((e) => e.text)];
  const actual = { failure: "the trial failed", success: "the trial succeeded", stopped_harm: "the trial was stopped for harm" }[v.outcome] || "";
  box.innerHTML = `<div class="dv-title">${mode === "forecast" ? "The agents disagree with the calculator" : "The result went against the calculator's prediction"}</div>
    <div class="dv-row"><div><span class="label">Calculator said</span><b>${esc(calcSuccess ? "success" : "failure")}</b> · ${pct(calcSuccess ? c.power : 1 - c.power)} likely</div>
      ${actual ? `<div><span class="label">What happened</span><b>${esc(actual)}</b></div>` : ""}
      <div><span class="label">Agents' diagnosis</span><b>${esc(CATEGORY[v.primary_category] || v.primary_category)}</b></div></div>
    ${reasons.length ? `<ul>${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    <button type="button" class="btn-small" id="dvMore">See every explanation the agents weighed →</button>`;
  box.classList.remove("hidden");
  $("dvMore").addEventListener("click", () => { selectTab("tabHyp"); $("tabHyp").scrollIntoView({ behavior: "smooth", block: "start" }); });
}

// "What if…?" sliders: re-run the calculator's own formulas in the browser.
const CTT_5Y = 0.78, CTT_Y1 = 0.88;
function timeFactor(years) {
  const y1 = Math.log(CTT_Y1) / Math.log(CTT_5Y);
  if (years <= 1) return y1 * Math.max(years, 0);
  return years >= 5 ? 1 : y1 + (1 - y1) * (years - 1) / 4;
}

function renderWhatIf(o, abstain) {
  const box = $("whatIf"), c = o.calculator, ci = o.calculator_inputs;
  if (abstain || c.ldl_equivalent_mmol === null || c.ldl_equivalent_mmol === undefined) { box.classList.add("hidden"); return; }
  const base = { eq: c.ldl_equivalent_mmol, events: ci.events || 1000, years: ci.followup_years || 5 };
  const unknownEvents = !ci.events;
  box.innerHTML = `<div class="wi-head"><b>What if…?</b> <span class="muted">Move a slider: the calculator's own formulas re-run in your browser.</span>
      <button type="button" class="btn-small" id="wiReset">Reset</button></div>
    <div class="wi-grid">
      <label><span>Causal biomarker lowering <b id="wiEqV"></b></span><input type="range" id="wiEq" min="0" max="3" step="0.01" value="${base.eq}">
        <small>LDL-cholesterol equivalent, mmol/L</small></label>
      <label><span>Primary events in the trial <b id="wiEvV"></b></span><input type="range" id="wiEv" min="100" max="5000" step="10" value="${base.events}">
        <small>${unknownEvents ? "Not public for this trial: try a value" : "More events = a more sensitive trial"}</small></label>
      <label><span>Follow-up <b id="wiYrV"></b></span><input type="range" id="wiYr" min="0.5" max="6" step="0.1" value="${base.years}">
        <small>The benefit of lowering LDL builds up over ~5 years</small></label>
    </div>
    <div class="wi-out" id="wiOut"></div>`;
  box.classList.remove("hidden");
  const update = () => {
    const eq = Number($("wiEq").value), events = Number($("wiEv").value), years = Number($("wiYr").value);
    const hr = Math.pow(CTT_5Y, timeFactor(years) * eq) * (c.bp_factor || 1);
    const changed = Math.abs(eq - base.eq) > 0.005 || events !== base.events || Math.abs(years - base.years) > 0.05;
    const oo = { ...o, calculator: { ...c, expected_hr: hr, power: undefined, causal_rrr: 1 - hr },
      calculator_inputs: { ...ci, events, followup_years: years, observed_hr: changed ? null : ci.observed_hr } };
    const t = trialOdds(oo);
    $("wiEqV").textContent = `${num(eq, 2)} mmol/L`; $("wiEvV").textContent = String(events); $("wiYrV").textContent = `${num(years, 1)} y`;
    drawForest(oo);
    $("chartCaption").textContent = (changed || unknownEvents ? "What-if scenario. " : "") + chartCaption(oo);
    const origin = c.power !== null && c.power !== undefined ? `Original analysis: HR ${num(c.expected_hr)}, ${pct(c.power)} chance to pass.` : `Original analysis: HR ${num(c.expected_hr)}, trial size unknown.`;
    $("wiOut").innerHTML = `<span class="wi-now">Expected HR <b>${num(hr)}</b> · ${Math.round((1 - hr) * 100)}% fewer events · chance to pass <b>${t ? pct(t.power) : "–"}</b></span>
      <span class="muted">${esc(origin)}</span>`;
  };
  ["wiEq", "wiEv", "wiYr"].forEach((id) => $(id).addEventListener("input", update));
  $("wiReset").addEventListener("click", () => { $("wiEq").value = base.eq; $("wiEv").value = base.events; $("wiYr").value = base.years; update(); });
  if (unknownEvents) update();
  else { $("wiEqV").textContent = `${num(base.eq, 2)} mmol/L`; $("wiEvV").textContent = String(base.events); $("wiYrV").textContent = `${num(base.years, 1)} y`;
    $("wiOut").innerHTML = `<span class="muted">Original analysis: HR ${num(c.expected_hr)}, ${pct(c.power)} chance to pass.</span>`; }
}

// The calculator abstains (no validated biomarker-to-events rule for this mechanism): explain instead of an empty axis.
function drawAbstain(o) {
  const c = o.calculator, ci = o.calculator_inputs, v = o.verdict;
  const fmt = (ch) => ch.pct_change !== null && ch.pct_change !== undefined
    ? `${ch.pct_change > 0 ? "+" : ""}${num(ch.pct_change, 0)}%` : `${ch.abs_change > 0 ? "+" : ""}${num(ch.abs_change)} ${ch.unit || ""}`;
  const changes = (ci.changes || []).slice(0, 4).map((ch) =>
    `<li><b>${esc(ch.biomarker)}</b> <span class="ab-val">${esc(fmt(ch))}</span>` +
    (ch.biomarker === ci.intended_biomarker ? ' <span class="ab-tag">main mechanism</span>' : "") + "</li>").join("");
  const mech = ci.intended_biomarker || ci.pathway || "this pathway";
  const actual = ci.observed_hr ? `HR ${num(ci.observed_hr)}`
    : { success: "met its primary endpoint", failure: "did not meet its primary endpoint", stopped_harm: "stopped early for harm" }[v.outcome];
  $("forest").innerHTML = `<div class="abstain">
    <div class="ab-row">
      <div class="ab-step"><div class="ab-k">1 · What the drug measurably did</div><ul>${changes || "<li>No placebo-adjusted biomarker change found</li>"}</ul></div>
      <div class="ab-arrow">→</div>
      <div class="ab-step ab-gap"><div class="ab-k">2 · Turn that into fewer heart attacks</div>
        <div class="ab-big">no validated formula</div>
        <p>For LDL cholesterol, dozens of trials give a reliable rule (1 mmol/L lower ≈ 22% fewer events). For <b>${esc(mech)}</b>
        there is no such rule yet, so the calculator does not guess a number.</p></div>
      <div class="ab-arrow">→</div>
      <div class="ab-step"><div class="ab-k">3 · How it was judged instead</div>
        <p>The agents weighed the published evidence directly: see <b>All explanations considered</b> below.</p>
        ${actual ? `<p class="ab-actual">Actual result: <b>${esc(actual)}</b></p>` : ""}</div>
    </div>
    ${c.partial_expected_hr ? `<p class="ab-note">Why not just use the lipid changes? Counting only its cholesterol / blood-pressure effects gives HR
      ${num(c.partial_expected_hr)}, almost no benefit. That ignores the drug's main mechanism and would have predicted a false failure.</p>` : ""}
  </div>`;
  $("chartLegend").innerHTML = "";
}

function drawForest(o) {
  const c = o.calculator, ci = o.calculator_inputs, t = trialOdds(o);
  const W = 900, H = 270, L = 30, R = 30, top = 86, bottom = 210;
  const lo = 0.6, hi = 1.3;
  const X = (hr) => L + (Math.log(hr) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * (W - L - R);
  const clamp = (v) => Math.min(hi, Math.max(lo, v));
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Chance that the trial proves the expected effect">`;
  s += `<line class="axis" x1="${L}" y1="${bottom}" x2="${W - R}" y2="${bottom}"/>`;
  for (const v of [0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3]) {
    s += `<line class="axis" x1="${X(v)}" y1="${bottom}" x2="${X(v)}" y2="${bottom + 5}"/><text class="tick" x="${X(v)}" y="${bottom + 20}" text-anchor="middle">${v.toFixed(1)}</text>`;
  }
  s += `<text class="tick" x="${L}" y="${H - 6}">← bigger benefit</text>` +
    `<text class="tick" x="${W / 2}" y="${H - 6}" text-anchor="middle">trial result, hazard ratio (log scale)</text>` +
    `<text class="tick" x="${W - R}" y="${H - 6}" text-anchor="end">harm →</text>`;
  s += `<line class="null" x1="${X(1)}" y1="${top - 10}" x2="${X(1)}" y2="${bottom}"/>` +
    `<text class="note" x="${X(1) + 6}" y="${bottom - 8}">1.0 = no effect</text>`;

  if (t) {
    // Bell curve of the likely trial result, split at the pass mark.
    const peak = bottom - top, pts = [];
    for (let i = 0; i <= 240; i++) {
      const lx = Math.log(lo) + (Math.log(hi) - Math.log(lo)) * i / 240;
      pts.push([lx, Math.exp(-0.5 * ((lx - t.mu) / t.se) ** 2)]);
    }
    const area = (keep) => {
      const seg = pts.filter(([lx]) => keep(lx));
      if (seg.length < 2) return "";
      const xs = seg.map(([lx]) => X(Math.exp(lx)));
      return `M${xs[0]} ${bottom} ` + seg.map(([, d], i) => `L${xs[i].toFixed(1)} ${(bottom - d * peak).toFixed(1)}`).join(" ") + ` L${xs[xs.length - 1]} ${bottom} Z`;
    };
    const cut = Math.log(t.pass);
    s += `<path class="bell-pass" d="${area((lx) => lx <= cut)}"/><path class="bell-miss" d="${area((lx) => lx >= cut)}"/>`;
    const px = X(clamp(t.pass));
    s += `<line class="pass" x1="${px}" y1="${top - 64}" x2="${px}" y2="${bottom}"/>` +
      `<text class="lbl pass-lbl" x="${px}" y="${top - 70}" text-anchor="middle">pass mark: HR ≤ ${num(t.pass, 3)}</text>`;
    const passPct = Math.round(t.power * 100), works = c.expected_hr < 0.98;
    s += `<text class="big ok-txt" x="${px - 12}" y="${top - 34}" text-anchor="end">${passPct >= 100 ? ">99" : passPct}% pass</text>` +
      `<text class="note" x="${px - 12}" y="${top - 16}" text-anchor="end">${works ? "trial shows the drug works" : "trial shows a benefit (by luck)"}</text>` +
      `<text class="big miss-txt" x="${px + 12}" y="${top - 34}">${passPct >= 100 ? "<1" : 100 - passPct}% miss</text>` +
      `<text class="note" x="${px + 12}" y="${top - 16}">${works ? "drug works, trial can't prove it" : "no benefit shown"}</text>`;
  }
  if (c.expected_hr) {
    const x = X(clamp(c.expected_hr));
    s += `<path class="exp" d="M${x} ${bottom - 9} l9 9 l-9 9 l-9 -9 z"/>`;
    if (!t) s += `<text class="lbl" x="${x + 14}" y="${bottom - 6}">expected ${num(c.expected_hr)}</text>`;
  }
  if (ci.observed_hr) {
    const [a, b] = ci.observed_ci || [ci.observed_hr, ci.observed_hr];
    const y = top + 52;
    s += `<line class="obs" x1="${X(clamp(a))}" y1="${y}" x2="${X(clamp(b))}" y2="${y}"/><circle class="obs-pt" cx="${X(clamp(ci.observed_hr))}" cy="${y}" r="5.5"/>` +
      `<text class="lbl" x="${X(clamp(b)) + 8}" y="${y + 4}">actual ${num(ci.observed_hr)}${ci.observed_ci ? ` (${num(a, 2)}–${num(b, 2)})` : ""}</text>`;
  }
  s += "</svg>";
  $("forest").innerHTML = s;
  centerWide($("forest"));
  const items = [
    t && ["lg-bell", "where the result is likely to land if the drug works as expected"],
    c.expected_hr && ["lg-exp", `most likely result (expected HR ${num(c.expected_hr)})`],
    t && ["lg-pass", "pass mark: the result the trial must beat to prove the drug works"],
    ci.observed_hr && ["lg-obs", ci.observed_ci ? "what actually happened (with 95% range)" : "what actually happened"],
  ].filter(Boolean);
  $("chartLegend").innerHTML = items.map(([k, v]) => `<span><i class="lg ${k}"></i> ${esc(v)}</span>`).join("");
}

// Missing inputs are written by the model; show them in plain words.
const plainInput = (t) => String(t).replace(/_/g, " ").replace(/\bldl c\b/gi, "LDL-C").replace(/\bsbp\b/gi, "blood pressure")
  .replace(/\bmmhg\b/gi, "(mmHg)").replace(/\bobserved ci\b/gi, "observed confidence interval").replace(/\bhr\b/gi, "HR").trim();

// Why the calculator gave no number, from its own basis line.
function notComputedReason(c, ci) {
  const basis = c.basis || "";
  if (c.expected_hr === null || c.expected_hr === undefined) {
    const m = basis.match(/^(.+?) change not measured$/);
    if (m) return `It needs the drug's placebo-adjusted ${m[1]} change, and no verified value was in the documents published before the cut-off.`;
    if (/no lipid change/.test(basis)) return "The drug works mainly through a pathway the calculator does not size, such as inflammation, so it gives no number rather than a misleading one.";
    return "Its key input was not in the documents published before the cut-off.";
  }
  if (!ci.events) return "The expected effect is computed, but the trial's number of events was not public before the cut-off, so the chance to pass is not.";
  return null;
}

function renderInputs(o) {
  const ci = o.calculator_inputs, c = o.calculator, m = o.mechanism || {};
  const none = "not found before the cut-off";
  const rows = [
    ["Target genetics", ci.target_genetics],
    ["Intended biomarker", ci.intended_biomarker],
    ...ci.changes.map((ch) => [`Δ ${ch.biomarker}`, [ch.abs_change !== null ? `${num(ch.abs_change)} (abs)` : null, ch.pct_change !== null ? `${num(ch.pct_change, 1)}%` : null, ch.baseline ? `baseline ${num(ch.baseline, 1)}` : null].filter(Boolean).join(" · ") || none]),
    ["LDL-equivalent", c.ldl_equivalent_mmol !== null && c.ldl_equivalent_mmol !== undefined ? `${num(c.ldl_equivalent_mmol)} mmol/L via ${c.basis}` : `not computed (${c.basis})`],
    ["Follow-up", ci.followup_years ? `${num(ci.followup_years, 2)} y` : "unknown (5 y shown)"],
    ["Events", ci.events ? `${ci.events}` : "unknown"],
    ["Alpha (two-sided)", num(ci.alpha_two_sided, 4)],
    ["Expected HR", c.expected_hr !== null && c.expected_hr !== undefined ? num(c.expected_hr) : "not computed"],
    ["Power", c.power !== null && c.power !== undefined ? pct(c.power) : "not computed"],
  ];
  if (ci.sbp_change_mmhg) rows.push(["Δ systolic BP", `${num(ci.sbp_change_mmhg, 1)} mmHg`]);
  if ((c.assumptions || []).length) rows.push(["Assumptions", c.assumptions.join("; ")]);
  if ((m.missing || []).length) rows.push(["Not found before the cut-off", m.missing.map(plainInput).join("; ")]);
  $("inputsTable").innerHTML = rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  // Not computable: say so plainly instead of an empty gauge
  const why = notComputedReason(c, ci);
  document.querySelector("#tabInputs .gauge-row").classList.toggle("hidden", !!why);
  let note = $("inputsNote");
  if (!note) {
    note = document.createElement("div");
    note.id = "inputsNote";
    note.className = "inputs-note";
    $("inputsTable").before(note);
  }
  note.classList.toggle("hidden", !why);
  const noEffect = c.expected_hr === null || c.expected_hr === undefined;
  note.innerHTML = why ? `<b>${noEffect ? "Not computable." : "Chance not computed."}</b> ${esc(why)} The calculator does not guess${noEffect ? ": the diagnosis comes from the agents' review of the evidence" : ""}.` : "";
}

function refButtons(refs) {
  return (refs || []).map((r) => factsById[r]
    ? `<button class="ref" data-ref="${esc(r)}">${esc(r.slice(0, 8))}</button>`
    : `<span class="ref">${esc(r)}</span>`).join("");
}

function renderHypotheses(hyps, primary) {
  const order = { supported: 0, undetermined: 1, weakened: 2 };
  const sorted = [...(hyps || [])].sort((a, b) => (a.category === primary ? -1 : b.category === primary ? 1 : (order[a.status] ?? 3) - (order[b.status] ?? 3)));
  $("hypotheses").innerHTML = sorted.map((h) => {
    const items = (arr) => (arr || []).length ? `<ul>${arr.map((e) => `<li>${esc(e.text)} ${refButtons(e.refs)}</li>`).join("")}</ul>` : '<span class="muted">—</span>';
    return `<div class="hyp ${h.category === primary ? "primary" : ""}"><div><h4>${esc(CATEGORY[h.category] || h.category)}</h4>` +
      `<span class="status s-${esc(h.status)}">${esc(STATUS_LABEL[h.status] || h.status)}</span></div>` +
      `<div class="ev-col"><div class="label">Evidence for</div>${items(h.for)}</div>` +
      `<div class="ev-col"><div class="label">Evidence against</div>${items(h.against)}</div>` +
      (h.would_settle ? `<div class="would"><b>Would settle it:</b> ${esc(h.would_settle)}</div>` : "") + "</div>";
  }).join("");
  document.querySelectorAll("#hypotheses .ref[data-ref]").forEach((b) => b.addEventListener("click", () => openFact(b.dataset.ref)));
}

function renderReadthrough(rt, note, runMode) {
  const btn = $("tabRtBtn");
  btn.classList.toggle("hidden", runMode === "forecast" && !(rt || []).length);
  $("readthroughNote").textContent = note || (rt && rt.length ? "" : "No other clinical programmes analysed.");
  $("readthrough").innerHTML = (rt || []).map((p) => {
    const t = p.trial || {};
    const b = p.biomarker_change;
    return `<div class="rt"><h4>${esc(p.drug)}</h4><div class="muted">${esc(p.target)} · ${esc((p.stage || "").replace("_", " "))}</div>` +
      `<div class="hr">${p.expected_hr_5y ? `HR ≈ ${num(p.expected_hr_5y)}` : "HR –"}</div>` +
      `<div class="meta">${esc(p.comparison || "")}</div>` +
      (t.nct_id ? `<div class="meta">${esc(t.nct_id)} ${esc(t.acronym || "")} · ${esc(t.status || "")} · n=${esc(t.enrollment || "?")}</div>` : "") +
      (b ? `<div class="meta">${esc(b.biomarker)} ${esc(b.value)}${esc(b.unit || "")} — <i>${esc(b.source_title || b.source)}</i></div>` : "") +
      (p.events_needed_80 ? `<div class="meta">needs ~${p.events_needed_80} events for 80% power</div>` : "") +
      ((p.notes || []).length ? `<div class="notes">${p.notes.map(esc).join("<br>")}</div>` : "") + "</div>";
  }).join("");
}

function factContext(f) {
  const a = f.attrs || {};
  return [a.biomarker, a.phase, a.timepoint, a.dose, a.placebo_adjusted === false ? "within-arm" : a.placebo_adjusted ? "placebo-adj." : null, a.kind]
    .filter(Boolean).join(" · ");
}

const VERIFY_LABEL = { confirmed: ["Checked in Zetaris ✓", "ok"], contradicts: ["Contradicts the source", "bad"],
  not_found: ["Not found in the abstract or registry", "na"], not_checked: ["Not independently verified", "na"] };

function verifyChip(fid) {
  const r = verifyById[fid];
  if (!r) return "";
  const [label, cls] = VERIFY_LABEL[r.status] || [r.status, "na"];
  const tip = [r.found ? `found: ${r.found}` : "", r.note || "", r.sql ? `SQL: ${r.sql}` : ""].filter(Boolean).join("\n");
  return ` <span class="vchip vchip-${cls}" title="${esc(tip)}">${logoImg("zetaris", 12)} ${esc(label)}</span>`;
}

function renderVerification(v) {
  const box = $("verifyBox");
  if (!box) return;
  if (!v || !v.status || v.status === "skipped") { box.innerHTML = ""; return; }
  const head = `<h3 class="sub-title">${logoImg("zetaris", 16)} Independent source check: Verifier, SQL run in Zetaris</h3>`;
  if (v.status === "unavailable") {
    box.innerHTML = head + `<p class="muted">Not run: ${esc(v.reason || "")}. The numbers above were not independently verified, so the
      confidence in the explanation is one level lower.</p>`;
    return;
  }
  const c = v.counts || {};
  box.innerHTML = head + `<p class="muted pane-intro">Nemotron wrote one Lightning SQL query per number; Zetaris ran it over the trial's
    registry record and the cited papers. ${c.confirmed || 0} confirmed, ${c.not_found || 0} not found, ${c.contradicts || 0} contradicted.</p>
    <table class="facts vtable"><thead><tr><th>Number</th><th>Used as</th><th>Result</th><th>Found in the source</th><th>Query</th></tr></thead><tbody>` +
    v.results.map((r) => { const [label, cls] = VERIFY_LABEL[r.status] || [r.status, "na"];
      return `<tr><td class="v">${esc(r.value)} ${esc(r.unit || "")}<div class="fid">${esc(r.fact_id)} · ${esc(r.source_id)}</div></td>
        <td>${esc(r.used_as)}</td><td><span class="vchip vchip-${cls}">${esc(label)}</span></td>
        <td class="q">${esc(r.found || r.note || "")}</td><td><code class="vsql">${esc(r.sql || "")}</code></td></tr>`; }).join("") +
    "</tbody></table>";
}

function renderFacts(facts) {
  $("factRows").innerHTML = facts.map((f) => `<tr data-id="${esc(f.fact_id)}" data-text="${esc((f.key + " " + JSON.stringify(f.attrs) + " " + f.quote + " " + f.source_id).toLowerCase())}">` +
    `<td>${esc(f.key)}<div class="fid">${esc(f.fact_id)}</div></td><td class="v">${esc(f.value)} ${esc(f.unit || "")}${verifyChip(f.fact_id)}</td>` +
    `<td>${esc(factContext(f))}</td><td><span class="acc acc-${esc(f.access)}">${esc(f.access || "")}</span>${esc(f.source_id)}<div class="fid">${esc(f.pub_date || "")}</div></td>` +
    `<td class="q">“${esc(f.quote)}”</td></tr>`).join("");
}

function filterFacts() {
  const q = $("factFilter").value.trim().toLowerCase();
  document.querySelectorAll("#factRows tr").forEach((tr) => { tr.style.display = !q || tr.dataset.text.includes(q) ? "" : "none"; });
}

function renderCritic(rounds) {
  $("critic").innerHTML = (rounds || []).map((r, i) => `<div class="label">Critic round ${i + 1} — verdict ok: ${esc(r.verdict_ok)}</div>` +
    (r.issues || []).map((x) => `<div class="issue ${esc(x.severity)}"><b>${esc(x.severity)}</b> → ${esc(x.target_agent)}: ${esc(x.issue)}` +
      (x.request ? `<div class="muted">request: ${esc(x.request)}</div>` : "") + "</div>").join("")).join("");
}

/* ---------- animated agent graph (live progress and replay) ---------- */

const GRAPH_NODES = {
  Intake: [96, 48, "trial, drug, scope"], CaseBuilder: [288, 48, "registry, genetics, papers"],
  MechanismAnalyst: [480, 48, "picks the evidence"], Calculator: [672, 48, "four checks (code)"],
  Verifier: [864, 48, "checks numbers in Zetaris"],
  Investigator: [864, 178, "weighs every cause"], Critic: [672, 178, "checks, can send back"],
  ReadThrough: [480, 178, "rival drugs"], Reporter: [288, 178, "writes the report"],
};
// Which technology each step runs on ("powered by"), shown as the provider's own logo.
const LOGO = {
  nvidia: { src: "/static/vendor/logos/nvidia-mark.svg", w: 24, label: "NVIDIA Nemotron" },
  anthropic: { src: "/static/vendor/logos/claude.svg", w: 16, label: "Claude (Anthropic)" },
  zetaris: { src: "/static/vendor/logos/zetaris-mark.svg", w: 16, label: "Data read through Zetaris" },
  meterless: { src: "/static/vendor/logos/meterless.svg", w: 16, label: "Facts stored in the Meterless World Model" },
};
// Graph node -> the AI agent whose model it shows (the case builder's AI step is extraction).
const GRAPH_MODEL_AGENT = { CaseBuilder: "Extraction", MechanismAnalyst: "MechanismAnalyst", Verifier: "Verifier", Investigator: "Investigator",
  Critic: "Critic", ReadThrough: "ReadThrough", Reporter: "Reporter" };
let graphModels = {};   // agent -> "provider:model": the models a report actually used, or the configured ones during a live run
function providerOf(spec) {
  const s = (spec || "").toLowerCase();
  if (s.startsWith("meterless:") || s.includes("meterless/")) return "meterless";
  if (s.includes("nemotron") || s.startsWith("nvidia:")) return "nvidia";
  if (s.includes("claude") || s.startsWith("anthropic:")) return "anthropic";
  return null;
}
const modelOf = (spec) => modelName((spec || "").split(":").pop());
function logoImg(key, h = 16, title = "") {
  const l = LOGO[key];
  return l ? `<img class="logo-ic" src="${l.src}" alt="${esc(l.label)}" title="${esc(title || l.label)}" style="height:${h}px">` : "";
}

const GRAPH_NW = 176;   // node width: room for the name and the provider logos
const GRAPH_EDGES = [["Intake", "CaseBuilder"], ["CaseBuilder", "MechanismAnalyst"], ["MechanismAnalyst", "Calculator"],
  ["Calculator", "Verifier"], ["Verifier", "Investigator"], ["Investigator", "Critic"], ["Critic", "ReadThrough"],
  ["ReadThrough", "Reporter"]];

function agentGraphSVG() {
  const nw = GRAPH_NW, nh = 50;
  let g = `<svg viewBox="0 0 960 214" role="img" aria-label="Agent graph"><defs><marker id="ag-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#9aa8b8"/></marker>` +
    `<marker id="ag-arr-back" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#a15c07"/></marker></defs>`;
  for (const [a, b] of GRAPH_EDGES) {
    const [x1, y1] = GRAPH_NODES[a], [x2, y2] = GRAPH_NODES[b];
    const [sx, sy, ex, ey] = y1 === y2 ? [x1 + Math.sign(x2 - x1) * nw / 2, y1, x2 - Math.sign(x2 - x1) * nw / 2, y2] : [x1, y1 + nh / 2, x2, y2 - nh / 2];
    g += `<line class="ag-edge" id="age-${a}-${b}" x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" marker-end="url(#ag-arr)"/>`;
  }
  // the critic's back edge (refinement loop)
  g += `<path class="ag-back" id="ag-back" d="M ${GRAPH_NODES.Critic[0] - 30} ${GRAPH_NODES.Critic[1] - nh / 2} C ${GRAPH_NODES.Critic[0] - 60} 120, ${GRAPH_NODES.CaseBuilder[0] + 60} 120, ${GRAPH_NODES.CaseBuilder[0] + 30} ${GRAPH_NODES.CaseBuilder[1] + nh / 2}" marker-end="url(#ag-arr-back)"/>` +
    `<text class="ag-back-l" id="ag-back-l" x="${GRAPH_NODES.Critic[0] - 14}" y="122">critic sent the work back</text>`;
  for (const [n, [x, y, role]] of Object.entries(GRAPH_NODES)) {
    g += `<g class="ag-node" id="agn-${n}" transform="translate(${x - nw / 2},${y - nh / 2})"><rect width="${nw}" height="${nh}" rx="10"/>` +
      `<text class="ag-name${n === "MechanismAnalyst" || n === "CaseBuilder" ? " ag-name-long" : ""}" data-n="${n}" x="12" y="20">${n === "MechanismAnalyst" ? "Mechanism analyst" : n === "ReadThrough" ? "Read-through" : n === "CaseBuilder" ? "Case builder" : n}</text>` +
      `<text class="ag-role" x="12" y="37" id="agr-${n}">${role}</text><circle class="ag-dot" cx="${nw - 14}" cy="14" r="5"/>` +
      `<g class="ag-badges" id="agb-${n}"></g></g>`;
  }
  return g + "</svg>";
}

// Logos at the top right of a node: the model's provider for AI agents, "code" for code agents,
// and for the case builder also Zetaris (data) and Meterless (facts) once the run has used them.
function drawBadges(g, node, viaZetaris, inMeterless) {
  const items = [];
  const agent = GRAPH_MODEL_AGENT[node];
  if (agent) {
    const spec = graphModels[agent], p = providerOf(spec);
    if (p === "meterless") {   // Nemotron served through Meterless Runtime (Nightshift): both marks
      items.push({ key: "nvidia", title: `${agent}: ${modelOf(spec)}` });
      items.push({ key: "meterless", title: `${agent}: runs through Meterless Runtime (Nightshift)` });
    } else if (p) items.push({ key: p, title: `${agent}: ${modelOf(spec)} (${LOGO[p].label})` });
  } else items.push({ code: true });
  if (node === "CaseBuilder") {
    if (inMeterless) items.unshift({ key: "meterless" });
    if (viaZetaris) items.unshift({ key: "zetaris" });
  }
  if (node === "Verifier") items.unshift({ key: "zetaris", title: "Verifier: Nemotron writes SQL, Zetaris runs it over the registry and papers" });
  const sig = JSON.stringify(items);
  if (g.dataset.sig === sig) return;
  g.dataset.sig = sig;
  let x = GRAPH_NW - 24, html = "";
  for (const it of items.reverse()) {
    if (it.code) { html += `<text class="ag-code" x="${x}" y="18" text-anchor="end">code</text>`; x -= 30; continue; }
    const l = LOGO[it.key];
    x -= l.w;
    html += `<image href="${l.src}" x="${x}" y="6" width="${l.w}" height="16"><title>${esc(it.title || l.label)}</title></image>`;
    x -= 5;
  }
  g.innerHTML = html;
  // The name must end before the first logo: squeeze it when a node carries two or three logos.
  const name = g.parentNode && g.parentNode.querySelector(".ag-name");
  if (name) {
    // estimated from the font size (the graph is often drawn inside a hidden tab, where text cannot be measured);
    // only ever shrinks the font, never stretches the name
    name.style.fontSize = "";
    const fs = name.classList.contains("ag-name-long") ? 11.5 : 13, room = x - 12 - 4, est = name.textContent.length * fs * 0.6;
    if (est > room) name.style.fontSize = `${Math.max(9, fs * room / est).toFixed(1)}px`;
  }
}

// Graph state after the first `upto` events.
function graphState(events, upto) {
  const st = { done: new Set(), warn: new Set(), active: null, facts: 0, docs: 0, back: false, backNow: false, round: 1, labels: {} };
  events.slice(0, upto).forEach((e, i) => {
    const a = e.agent_name, out = e.output_summary || "", last = i === upto - 1;
    if (GRAPH_NODES[a]) { if (st.active && st.active !== a) st.done.add(st.active); st.active = a; }
    if (e.status === "warning" || e.status === "retry" || e.status === "needs_revision") st.warn.add(a);
    if (e.action === "extract_facts") { st.facts += Number((out.match(/^(\d+) facts/) || [0, 0])[1]); st.docs += 1; st.labels.CaseBuilder = `${st.docs} docs · ${st.facts} quotes`; }
    if (e.action === "four_checks") { const m = out.match(/expected HR ([\d.]+|None).*?power ([\d.]+|None)/); if (m) st.labels.Calculator = `HR ${m[1] === "None" ? "–" : m[1]} · ${m[2] === "None" ? "power –" : Math.round(Number(m[2]) * 100) + "% chance"}`; }
    if (e.action === "differential_diagnosis") { const m = out.match(/primary (\w+) \((\w+)\)/); if (m) { const c = CATEGORY[m[1]] || m[1]; st.labels.Investigator = c.length > 24 ? c.slice(0, 23) + "…" : c; } }
    if (e.action === "review") { const m = out.match(/(\d+) issues \((\d+) high\)/); if (m) st.labels.Critic = `${m[1]} notes · ${m[2]} serious`; }
    if (e.action === "verify_inputs") { const m = out.match(/(\d+) confirmed, (\d+) not found, (\d+) contradicted/); if (m) st.labels.Verifier = `${m[1]} confirmed · ${m[3]} contradicted`; }
    if (e.action === "verification_unavailable") st.labels.Verifier = "not verified: no Zetaris";
    if (e.action === "find_programs") { const m = out.match(/^(\d+)/); if (m) st.labels.ReadThrough = `${m[1]} rival programme${m[1] === "1" ? "" : "s"}`; }
    if (e.action === "refinement_loop") { st.back = true; st.round += 1; if (last) st.backNow = true; st.done.delete("CaseBuilder"); st.done.delete("MechanismAnalyst"); st.done.delete("Calculator"); st.done.delete("Verifier"); st.done.delete("Investigator"); st.active = "CaseBuilder"; }
    if (e.action === "finish_run" && st.active) { st.done.add(st.active); st.active = null; }
  });
  return st;
}

function applyGraph(el, events, upto) {
  if (!el.querySelector("svg")) el.innerHTML = agentGraphSVG();
  const st = graphState(events, upto);
  for (const n of Object.keys(GRAPH_NODES)) {
    const node = el.querySelector(`#agn-${n}`);
    node.classList.toggle("is-active", st.active === n);
    node.classList.toggle("is-done", st.done.has(n) && st.active !== n);
    node.classList.toggle("is-warn", st.warn.has(n));
    if (st.labels[n]) node.querySelector(`#agr-${n}`).textContent = st.labels[n];
    else node.querySelector(`#agr-${n}`).textContent = GRAPH_NODES[n][2];
  }
  const seen = events.slice(0, upto);
  const viaZetaris = seen.some((e) => e.via === "zetaris"), inMeterless = seen.some((e) => e.action === "world_model_sync");
  for (const n of Object.keys(GRAPH_NODES)) drawBadges(el.querySelector(`#agb-${n}`), n, viaZetaris, inMeterless);
  for (const [a, b] of GRAPH_EDGES) {
    const edge = el.querySelector(`#age-${a}-${b}`);
    edge.classList.toggle("is-on", st.done.has(a) && (st.done.has(b) || st.active === b));
    edge.classList.toggle("is-flow", st.done.has(a) && st.active === b);
  }
  el.querySelector("#ag-back").classList.toggle("is-on", st.back);
  el.querySelector("#ag-back").classList.toggle("is-now", st.backNow);
  el.querySelector("#ag-back-l").classList.toggle("is-on", st.back);
  return st;
}

const FEED_ICON = { "read via Zetaris": "⛁", query_zetaris: "⛁", write_sql: "⌨", verify_inputs: "✓", register_tables: "⛁", code_completion: "⚙", world_model_sync: "◈", extract_facts: "❝", four_checks: "∑", review: "⚑", refinement_loop: "↩", differential_diagnosis: "⚖", final_synthesis: "✎" };
let replayTimer = null, replaySpeed = 1, replayEvents = [], replayStep = 0;

function stopReplay() { clearTimeout(replayTimer); replayTimer = null; $("replayBtn").textContent = replayStep && replayStep < replayEvents.length ? "▶ Resume" : "▶ Replay the investigation"; }

// One card per recorded step, in order; clicking a card shows the graph as it was at that step.
function feedCard(e, i) {
  const card = document.createElement("div");
  card.className = `rf-card ${e.status === "warning" || e.status === "needs_revision" ? "rf-warn" : ""} ${e.action === "refinement_loop" ? "rf-back" : ""}`;
  card.dataset.i = String(i);
  card.innerHTML = `<div class="rf-top"><span class="rf-n">${i + 1}</span><span class="rf-ic">${FEED_ICON[e.action] || "•"}</span>` +
    `<b>${esc(e.agent_name)}</b>${e.target_agent ? ` <span class="muted">→ ${esc(e.target_agent)}</span>` : ""}` +
    ` <span class="rf-act">${esc(e.action.replace(/_/g, " "))}</span><span class="rf-t mono">${esc(e.elapsed_s ?? "")}s</span></div>` +
    `<div class="rf-body">${esc(e.output_summary || "")}</div>`;
  card.addEventListener("click", () => {
    if (replayTimer) { clearTimeout(replayTimer); replayTimer = null; }
    showStep(i + 1);
    card.classList.toggle("rf-open");
    $("replayBtn").textContent = i + 1 < replayEvents.length ? "▶ Resume" : "↻ Replay again";
  });
  return card;
}

function showStep(n) {
  replayStep = n;
  const st = applyGraph($("replayGraph"), replayEvents, n);
  $("replayStatus").textContent = `step ${n}/${replayEvents.length} · round ${st.round}`;
  document.querySelectorAll("#replayFeed .rf-card").forEach((c) => c.classList.toggle("rf-current", Number(c.dataset.i) === n - 1));
}

function fillFeed(upto) {
  const feed = $("replayFeed");
  feed.innerHTML = "";
  replayEvents.slice(0, upto).forEach((e, i) => feed.appendChild(feedCard(e, i)));
  $("replayCount").textContent = replayEvents.length ? `${upto} of ${replayEvents.length} steps` : "";
}

function startReplay() {
  if (replayTimer) { stopReplay(); return; }
  const events = replayEvents;
  if (!events.length) { $("replayStatus").textContent = "No trace recorded for this run."; return; }
  const feed = $("replayFeed");
  // resume from a paused step, otherwise start from the beginning
  let i = replayStep > 0 && replayStep < events.length ? replayStep : 0;
  if (i === 0) { fillFeed(0); applyGraph($("replayGraph"), events, 0); }
  else fillFeed(i);
  $("replayBtn").textContent = "❚❚ Pause";
  const tick = () => {
    i += 1;
    const e = events[i - 1];
    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
    feed.appendChild(feedCard(e, i - 1));
    $("replayCount").textContent = `${i} of ${events.length} steps`;
    showStep(i);
    if (nearBottom) feed.scrollTop = feed.scrollHeight;   // follow new steps unless the reader scrolled up
    if (i < events.length) replayTimer = setTimeout(tick, (e.action === "extract_facts" || e.action === "search_literature" ? 260 : e.action === "refinement_loop" || e.action === "review" ? 1500 : 750) / replaySpeed);
    else { replayTimer = null; $("replayBtn").textContent = "↻ Replay again"; replayStep = 0; }
  };
  tick();
}

function initReplay(events) {
  clearTimeout(replayTimer); replayTimer = null;
  replayEvents = events || [];
  replayStep = 0;
  $("replayBtn").textContent = "▶ Replay the investigation";
  applyGraph($("replayGraph"), replayEvents, replayEvents.length);
  fillFeed(replayEvents.length);   // the whole recorded sequence is browsable before any replay
  $("replayFeed").scrollTop = 0;
  $("replayStatus").textContent = replayEvents.length ? `${replayEvents.length} recorded steps` : "";
}

function renderTrace(events) {
  initReplay(events);
  $("trace").innerHTML = events.map((e) => `<li class="${esc(e.status)}"><span class="t">${esc(e.elapsed_s)}s</span>` +
    `<span class="a">${esc(e.agent_name)}${e.target_agent ? " → " + esc(e.target_agent) : ""}</span>` +
    `<span>${esc(e.action)}: ${esc(e.output_summary)}</span></li>`).join("");
}

function openFact(id) {
  const f = factsById[id];
  if (!f) return;
  $("drawerBody").innerHTML = `<div class="label">${esc(f.key)} · ${esc(id)}</div><h3 style="margin:6px 0 2px">${esc(f.value)} ${esc(f.unit || "")}</h3>` +
    `<div class="muted">${esc(factContext(f))}</div><blockquote>“${esc(f.quote)}”</blockquote>` +
    `<div><span class="acc acc-${esc(f.access)}">${esc(f.access)}</span>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.source_id)}</a>` : esc(f.source_id)} ` +
    `<span class="muted">${esc(f.pub_date || "")}</span></div><p class="muted" style="font-size:12px">Quote verified verbatim against the source text by code.</p>`;
  $("drawer").classList.remove("hidden");
}

// "Where do I find an ID?": how to look one up, and a library of verified IDs that fill the form in one click.
let trialIdsCache = null;
async function openIdHelp() {
  if (!trialIdsCache) { try { trialIdsCache = await getJSON("/api/trial-ids"); } catch { trialIdsCache = { groups: [] }; } }
  const row = (t) => `<tr><td><b>${esc(t.acronym)}</b><div class="muted">${esc(t.drug)}</div></td>
      <td class="mono">${esc(t.nct)}</td><td class="muted">${esc(t.stage)}</td>
      <td class="id-actions"><button type="button" class="btn-small" data-use="${esc(t.nct)}">Use ID</button>
        ${t.archive ? `<button type="button" class="btn-small" data-archive="${esc(t.archive)}" data-nct="${esc(t.nct)}" title="Replay from the frozen archive: no API key needed">Backtest</button>` : ""}
        ${t.run_id ? `<a class="id-open" href="#/run/${esc(t.run_id)}">Open finished analysis →</a>` : ""}</td></tr>`;
  $("drawerBody").innerHTML = `<div class="label">New analysis</div><h3 style="margin:6px 0 8px">Where do I find a trial ID?</h3>
    <p>Every clinical trial has a public ID in the US registry <b>ClinicalTrials.gov</b>: the letters <span class="mono">NCT</span> and 8 digits,
      for example <span class="mono">NCT04023552</span>. Trial Autopsy needs the ID of a <b>phase 3 cardiovascular outcome trial</b>
      (a trial that counts heart attacks, strokes and cardiovascular deaths).</p>
    <div class="id-how"><b>To look one up yourself</b><ol>
      <li>Open <a href="https://clinicaltrials.gov/search?aggFilters=phase:3&cond=Cardiovascular%20Diseases" target="_blank" rel="noopener">ClinicalTrials.gov: phase 3 cardiovascular trials</a>
        and type the drug name (e.g. <i>semaglutide</i>) in “Intervention / treatment”.</li>
      <li>Open the trial; its ID is shown at the top as <b>“ClinicalTrials.gov ID NCT…”</b>.</li>
      <li>Company press releases about results also quote it, usually as “ClinicalTrials.gov identifier: NCT…”.</li></ol></div>
    <p><b>Or pick one below.</b> <i>Use ID</i> fills the form (needs an AI key for a live run);
      <i>Backtest</i> replays the trial from its frozen archive, as of its result day, with no key; <i>Open finished analysis</i> shows the saved report.</p>
    ${trialIdsCache.groups.map((g) => `<div class="id-group"><div class="id-gh">${esc(g.name)} <span class="muted">· ${esc(g.depth)}</span></div>
      <table class="id-table">${g.trials.map(row).join("")}</table></div>`).join("")}`;
  $("drawerBody").querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => fillCase(b.dataset.use, "")));
  $("drawerBody").querySelectorAll("[data-archive]").forEach((b) => b.addEventListener("click", () => fillCase(b.dataset.nct, b.dataset.archive)));
  $("drawerBody").querySelectorAll(".id-open").forEach((a) => a.addEventListener("click", closeDrawer));
  $("drawer").classList.add("wide");
  $("drawer").classList.remove("hidden");
}

function fillCase(nct, archive) {
  $("trial").value = nct;
  $("archiveCase").value = archive;
  $("readout").value = "";
  $("asOf").value = "";
  setMode(archive ? "autopsy" : "auto");
  if (archive) document.querySelector("details.advanced").open = true;
  closeDrawer();
  $("trial").focus();
}

function closeDrawer() { $("drawer").classList.add("hidden"); $("drawer").classList.remove("wide"); }
function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

init();
