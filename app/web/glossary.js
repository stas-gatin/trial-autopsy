"use strict";

// Plain-language explanations shown when a judge hovers over a term in the report.
// Each entry: aliases (matched as whole words, case-insensitive unless `exact`), title, what, why.
const GLOSSARY = [
  { aliases: ["hazard ratio", "HR"], exact: ["HR"], title: "Hazard ratio (HR)",
    what: "How often bad events (heart attack, stroke, death) happen on the drug compared with placebo. 1.00 means no difference; 0.80 means 20% fewer events; above 1 means more events.",
    why: "It is the headline number of almost every cardiovascular trial." },
  { aliases: ["95% CI", "confidence interval", "CI"], exact: ["CI"], title: "95% confidence interval",
    what: "The range where the true effect most likely lies. If the whole range is below 1.0, the benefit is statistically convincing; if it crosses 1.0, the trial could not prove a benefit.",
    why: "A trial ‘fails’ when this interval includes 1.0." },
  { aliases: ["statistical power", "power"], title: "Statistical power",
    what: "The chance that a trial detects an effect that really exists. 80–90% is the usual target; 50% is a coin flip even if the drug works.",
    why: "A real but small benefit can be missed by a trial that is too small: this is a common, under-appreciated cause of ‘failure’." },
  { aliases: ["pass mark"], title: "Pass mark",
    what: "The hazard ratio the trial's result has to reach (or beat) to count as statistically significant. It depends only on the number of events: more events, the closer the pass mark gets to 1.0.",
    why: "If the drug's expected effect sits close to the pass mark, ordinary chance can push the result to the wrong side and the trial fails even though the drug works." },
  { aliases: ["minimum detectable", "smallest detectable", "weakest effect"], title: "Smallest detectable effect",
    what: "The weakest effect a trial of this size can reliably see (with 80% power). Effects weaker than this are likely to be missed.",
    why: "Compare it with the expected effect: if the drug's expected effect is weaker, the trial was set up to fail." },
  { aliases: ["primary endpoint", "primary outcome", "main goal"], title: "Primary endpoint",
    what: "The single main question a trial is designed and registered to answer before it starts, e.g. ‘fewer heart attacks, strokes and cardiovascular deaths’.",
    why: "A trial succeeds or fails on this endpoint alone; it cannot be changed afterwards without losing credibility." },
  { aliases: ["MACE", "MACE-4", "3P-MACE"], exact: ["MACE", "MACE-4", "3P-MACE"], title: "MACE",
    what: "Major adverse cardiovascular events: a combined count of cardiovascular death, heart attack, stroke and sometimes urgent heart procedures.",
    why: "The standard ‘hard outcome’ regulators use to judge heart drugs." },
  { aliases: ["placebo-adjusted", "placebo"], title: "Placebo / placebo-adjusted",
    what: "Placebo is an inactive look-alike treatment. ‘Placebo-adjusted’ means the drug's change minus the change seen on placebo, i.e. the effect of the drug itself.",
    why: "Within-arm changes overstate a drug's effect; the agents prefer placebo-adjusted numbers." },
  { aliases: ["phase 3", "phase III"], title: "Phase 3 trial",
    what: "The final, large (thousands of patients), expensive stage that tests whether a drug actually prevents disease events. Costs hundreds of millions of dollars.",
    why: "This is the decision that the autopsy explains or the forecast is about." },
  { aliases: ["phase 2", "phase II"], title: "Phase 2 trial",
    what: "A mid-size study (hundreds of patients) that measures whether the drug changes a biomarker (e.g. cholesterol) and finds the dose.",
    why: "Phase 2 biomarker results are what the forecast uses to predict phase 3." },
  { aliases: ["phase 1"], title: "Phase 1 trial",
    what: "First-in-human study, usually small and in healthy volunteers, mostly for safety and dosing.",
    why: "Its biomarker numbers are the least representative of real patients." },
  { aliases: ["outcomes trial", "outcome trial", "cardiovascular outcomes trial"], title: "Cardiovascular outcomes trial",
    what: "A trial that counts real clinical events (heart attacks, strokes, deaths) rather than lab values.",
    why: "Only an outcomes trial proves that changing a biomarker actually helps patients." },
  { aliases: ["LDL-C", "LDL cholesterol", "LDL"], exact: ["LDL-C", "LDL"], title: "LDL cholesterol (‘bad’ cholesterol)",
    what: "Cholesterol carried in LDL particles, which build up in artery walls. Lowering LDL by 1 mmol/L cuts heart events by about 22% over 5 years (CTT 2010).",
    why: "The best-proven causal biomarker; the calculator converts other effects into ‘LDL-equivalents’." },
  { aliases: ["LDL-equivalent", "LDL-cholesterol"], title: "LDL-equivalent",
    what: "The calculator's common currency: a drug's effect on any causal lipid marker, translated into the LDL reduction that would give the same benefit.",
    why: "Lets one well-proven benefit curve (CTT) size the effect of very different drugs." },
  { aliases: ["HDL-C", "HDL cholesterol", "HDL"], exact: ["HDL-C", "HDL"], title: "HDL cholesterol (‘good’ cholesterol)",
    what: "Cholesterol in HDL particles. People with high HDL have fewer heart attacks, but genetic studies show raising HDL itself does not protect.",
    why: "Several drugs failed because they raised HDL, a marker that does not cause disease." },
  { aliases: ["apoB", "apolipoprotein B"], exact: ["apoB"], title: "Apolipoprotein B (apoB)",
    what: "One apoB molecule sits on every artery-clogging lipid particle, so it counts the particles directly. Often a more reliable measure than LDL.",
    why: "When LDL and apoB disagree, the calculator trusts apoB (CETP inhibitors distort LDL lab tests)." },
  { aliases: ["non-HDL-C", "non-HDL"], exact: ["non-HDL-C", "non-HDL"], title: "Non-HDL cholesterol",
    what: "Total cholesterol minus HDL: all the cholesterol in potentially harmful particles.",
    why: "A robust alternative when LDL measurements are unreliable." },
  { aliases: ["Lp(a)", "lipoprotein(a)", "lipoprotein (a)"], exact: ["Lp(a)"], title: "Lipoprotein(a), Lp(a)",
    what: "An inherited LDL-like particle. About 1 in 5 people have high levels, set mostly by genes. Genetics shows it causes heart disease; there is no approved drug yet.",
    why: "Genetic studies suggest you must lower it by roughly 100 mg/dL to get a clear benefit, more than some drugs achieve." },
  { aliases: ["triglycerides", "triglyceride"], title: "Triglycerides",
    what: "Blood fats carried in particles that also contain apoB; their harm seems to run through the apoB particles.",
    why: "Explains why triglyceride drugs are judged by their apoB effect." },
  { aliases: ["hsCRP", "C-reactive protein", "CRP"], exact: ["hsCRP", "CRP"], title: "C-reactive protein (hsCRP)",
    what: "A blood marker of inflammation. It predicts risk, but genetics shows CRP itself does not cause heart disease: it is a smoke signal, not the fire.",
    why: "Lowering CRP alone should not help; what matters is the inflammatory pathway upstream." },
  { aliases: ["IL-6", "interleukin-6"], exact: ["IL-6"], title: "Interleukin-6 (IL-6)",
    what: "A signalling molecule that drives inflammation. People whose IL-6 signalling is genetically weaker have fewer heart attacks.",
    why: "Makes IL-6 a genetically supported, but hard to size, drug target (ZEUS, ziltivekimab)." },
  { aliases: ["IL-1β", "IL-1beta", "interleukin-1β"], exact: ["IL-1β", "IL-1beta"], title: "Interleukin-1β",
    what: "An upstream inflammation signal. Blocking it (canakinumab, CANTOS) reduced heart events modestly but raised infection risk.",
    why: "The calculator cannot size inflammation drugs, so agents judge them qualitatively." },
  { aliases: ["Lp-PLA2", "Lp-PLA2 activity"], exact: ["Lp-PLA2"], title: "Lp-PLA2",
    what: "An enzyme in blood that tracked heart risk in observational studies. People born with an inactive version are not protected.",
    why: "Darapladib blocked it strongly and still failed twice: the target was not causal." },
  { aliases: ["sPLA2-IIA", "sPLA2"], exact: ["sPLA2-IIA", "sPLA2"], title: "Secretory phospholipase A2 (sPLA2-IIA)",
    what: "An inflammatory enzyme. Genetic studies found lowering it does not reduce heart disease.",
    why: "Varespladib (VISTA-16) blocked it and the trial showed possible harm." },
  { aliases: ["CETP inhibitor", "CETP"], exact: ["CETP"], title: "CETP",
    what: "A protein that moves cholesterol between particles. Blocking it raises HDL a lot and lowers LDL/apoB a little.",
    why: "Four CETP drugs: two failed, one harmed, one gave a small benefit that matched its small apoB effect." },
  { aliases: ["PCSK9"], exact: ["PCSK9"], title: "PCSK9",
    what: "A protein that removes LDL receptors from the liver. People born with broken PCSK9 have very low LDL and far fewer heart attacks.",
    why: "A textbook case of genetics predicting a successful drug (evolocumab, alirocumab)." },
  { aliases: ["antisense oligonucleotide", "antisense", "ASO"], exact: ["ASO"], title: "Antisense oligonucleotide",
    what: "A short synthetic strand of genetic material that blocks the instructions for making one protein, here apo(a), the core of Lp(a).",
    why: "Pelacarsen works this way; siRNA drugs do the same job more strongly and less often." },
  { aliases: ["siRNA"], exact: ["siRNA"], title: "siRNA",
    what: "Small interfering RNA: silences one gene's message in the liver; often lowers the target by over 90% with injections every few months.",
    why: "Olpasiran and lepodisiran are siRNAs, which is why they lower Lp(a) more than pelacarsen." },
  { aliases: ["Mendelian randomization", "Mendelian randomisation", "MR"], exact: ["MR"], title: "Mendelian randomization",
    what: "Nature's own clinical trial: people randomly inherit gene variants that raise or lower a protein. If those people have less disease, lowering the protein with a drug should help too.",
    why: "The strongest evidence the agents use for ‘does the target cause the disease?’." },
  { aliases: ["genetic association", "human genetics", "genetics"], title: "Human genetic evidence",
    what: "Studies of DNA in hundreds of thousands of people linking a gene to a disease. Drugs with genetic support are about 2.6× more likely to succeed (Minikel 2024).",
    why: "Check 1 of the four checks." },
  { aliases: ["loss-of-function"], title: "Loss-of-function variant",
    what: "A natural mutation that switches a gene off, a lifelong ‘drug’ experiment in the people who carry it.",
    why: "If carriers are protected, a drug that switches the gene off should protect too." },
  { aliases: ["Open Targets"], title: "Open Targets",
    what: "A public database that scores the evidence linking each human gene to each disease, built by EMBL-EBI, the Wellcome Sanger Institute and partners.",
    why: "The agents' source for target-disease genetics." },
  { aliases: ["biomarker"], title: "Biomarker",
    what: "Something measurable in blood or imaging (cholesterol, inflammation markers) that drugs change faster than they change disease.",
    why: "Check 2: a drug only helps if the biomarker it moves actually causes the disease." },
  { aliases: ["relative risk reduction", "RRR"], exact: ["RRR"], title: "Relative risk reduction",
    what: "How much a treatment cuts the rate of events, in percent. HR 0.85 = 15% relative risk reduction.",
    why: "Check 3 estimates it before the results come out." },
  { aliases: ["futility"], title: "Stopped for futility",
    what: "An independent committee looked at the data mid-trial and concluded the drug could not show a benefit even if the trial ran to the end.",
    why: "Futility is a statement about the result, not about the reason; the autopsy supplies the reason." },
  { aliases: ["DSMB", "data monitoring committee"], exact: ["DSMB"], title: "Data monitoring committee",
    what: "Independent experts who look at unblinded results during a trial and can stop it early for benefit, harm or futility.",
    why: "" },
  { aliases: ["off-target"], title: "Off-target effect",
    what: "An effect of the drug that has nothing to do with its intended target, e.g. torcetrapib raising blood pressure.",
    why: "Can make a drug harmful even if its target is a good one." },
  { aliases: ["read-through"], title: "Read-through",
    what: "What one drug's trial result implies for competitors that share its target or mechanism. Investors use this term when a rival's failure moves other stocks.",
    why: "Pelacarsen's failure cost Amgen about 10% of its share price in a day." },
  { aliases: ["statin", "statins"], title: "Statins",
    what: "The standard cholesterol-lowering pills taken by most heart patients. New drugs are usually tested on top of them.",
    why: "A drug added to a statin starts from a lower LDL, so its extra benefit is smaller." },
  { aliases: ["myocardial infarction", "heart attack"], title: "Myocardial infarction",
    what: "A heart attack: part of the heart muscle dies because its artery is blocked.",
    why: "" },
  { aliases: ["revascularization", "revascularisation"], title: "Revascularization",
    what: "A procedure to reopen a blocked heart artery (stent or bypass).",
    why: "Sometimes counted in MACE; ‘urgent’ revascularization is a softer endpoint than death or heart attack." },
  { aliases: ["ASCVD", "atherosclerotic cardiovascular disease"], exact: ["ASCVD"], title: "ASCVD",
    what: "Atherosclerotic cardiovascular disease: heart attack, stroke or artery disease caused by plaque in the arteries.",
    why: "" },
  { aliases: ["topline"], title: "Topline result",
    what: "The first short announcement of whether a trial met its primary endpoint, usually a press release, before full data are published.",
    why: "Fresh failures often have only a topline, so some numbers are still unknown." },
  { aliases: ["backtest"], title: "Backtest",
    what: "Replaying history: the agent sees only documents published before the result day, and its answer is compared with what actually happened.",
    why: "It is how the system's accuracy is measured honestly." },
  { aliases: ["within-arm"], title: "Within-arm change",
    what: "The change in the drug group alone, without subtracting placebo. Overstates the drug's true effect.",
    why: "" },
  { aliases: ["monotherapy"], title: "Monotherapy",
    what: "The drug given alone, not on top of other treatment. Effects look bigger than when the drug is added to a statin.",
    why: "" },
];

// Build one regex from all aliases, longest first so "LDL-C" wins over "LDL".
const _GLOSS_INDEX = new Map();
const _GLOSS_RE = (() => {
  const parts = [];
  for (const entry of GLOSSARY) {
    for (const alias of entry.aliases) {
      _GLOSS_INDEX.set(alias.toLowerCase(), entry);
      parts.push(alias);
    }
  }
  parts.sort((a, b) => b.length - a.length);
  const escaped = parts.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`(?<![A-Za-z0-9-])(${escaped.join("|")})(?![A-Za-z0-9])`, "gi");
})();

function _glossEntry(match) {
  const entry = _GLOSS_INDEX.get(match.toLowerCase());
  if (!entry) return null;
  // Short acronyms must match exactly (so "hr" inside prose or "ci" are not underlined by accident).
  if (entry.exact && entry.exact.some((e) => e.toLowerCase() === match.toLowerCase()) && !entry.exact.includes(match)) return null;
  return entry;
}

// Wrap the first occurrence of each glossary term inside `root` with a hoverable span.
function annotateTerms(root) {
  if (!root) return;
  const seen = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const p = node.parentElement;
      if (!p || p.closest(".term, button, svg, script, style, .tip, input, textarea")) return NodeFilter.FILTER_REJECT;
      return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const text = node.nodeValue;
    _GLOSS_RE.lastIndex = 0;
    let match, last = 0, frag = null;
    while ((match = _GLOSS_RE.exec(text))) {
      const entry = _glossEntry(match[1]);
      if (!entry || seen.has(entry.title)) continue;
      seen.add(entry.title);
      frag = frag || document.createDocumentFragment();
      frag.appendChild(document.createTextNode(text.slice(last, match.index)));
      const span = document.createElement("span");
      span.className = "term";
      span.tabIndex = 0;
      span.dataset.term = entry.title;
      span.textContent = match[1];
      frag.appendChild(span);
      last = match.index + match[1].length;
    }
    if (frag) {
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }
  }
}

// One floating card, positioned above the hovered term (below if there is no room).
const _tip = document.createElement("div");
_tip.className = "tip hidden";
_tip.setAttribute("role", "tooltip");
document.body.appendChild(_tip);

function _showTip(el) {
  const entry = GLOSSARY.find((g) => g.title === el.dataset.term);
  if (!entry) return;
  _tip.innerHTML = `<div class="tip-title">${esc(entry.title)}</div><div class="tip-what">${esc(entry.what)}</div>` +
    (entry.why ? `<div class="tip-why"><span>Why it matters here</span>${esc(entry.why)}</div>` : "");
  _tip.classList.remove("hidden", "below");
  const r = el.getBoundingClientRect();
  const tw = _tip.offsetWidth, th = _tip.offsetHeight;
  let left = r.left + r.width / 2 - tw / 2;
  left = Math.max(10, Math.min(left, window.innerWidth - tw - 10));
  let top = r.top - th - 10;
  if (top < 70) { top = r.bottom + 10; _tip.classList.add("below"); }
  _tip.style.left = `${left + window.scrollX}px`;
  _tip.style.top = `${top + window.scrollY}px`;
  _tip.style.setProperty("--arrow-x", `${r.left + r.width / 2 - left}px`);
  requestAnimationFrame(() => _tip.classList.add("show"));
}

function _hideTip() { _tip.classList.remove("show"); _tip.classList.add("hidden"); }

document.addEventListener("mouseover", (e) => { const t = e.target.closest(".term"); if (t) _showTip(t); });
document.addEventListener("mouseout", (e) => { if (e.target.closest(".term")) _hideTip(); });
document.addEventListener("focusin", (e) => { const t = e.target.closest(".term"); if (t) _showTip(t); });
document.addEventListener("focusout", (e) => { if (e.target.closest(".term")) _hideTip(); });
document.addEventListener("click", (e) => {   // touch screens: tap to toggle
  const t = e.target.closest(".term");
  if (t) { e.preventDefault(); _tip.classList.contains("show") ? _hideTip() : _showTip(t); }
  else _hideTip();
});
window.addEventListener("scroll", _hideTip, { passive: true });
