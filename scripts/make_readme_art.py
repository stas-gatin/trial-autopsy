"""Draw the animated SVG pictures of the README from the recorded results.

    python scripts/make_readme_art.py

Writes docs/images/art/{hero,pipeline,results}.svg. The numbers come from data/library/ and the latest
eval/results/backtest_*.json, so the pictures stay true after a new set of runs. The animations are CSS and SMIL
inside the SVG (no scripts), which GitHub renders in a README image.
"""
import base64
import glob
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images" / "art"
FONT = "'IBM Plex Sans','Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'IBM Plex Mono',SFMono-Regular,Menlo,Consolas,monospace"
INK, INK2, MUTED, LINE = "#142133", "#3c4a5e", "#6b7a90", "#dfe5ec"
TEAL, TEAL2, FAIL, OK, WARN = "#0b6e79", "#2fd3c4", "#b42318", "#087443", "#a15c07"
ZET, NV, MS = "#4554e7", "#5a8f00", "#171717"


def logo(name: str) -> str:
    raw = (ROOT / "app" / "web" / "vendor" / "logos" / name).read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode()


def logo_defs(*names: str) -> str:
    """Each logo embedded once, then placed with <use href="#logo-name">."""
    return "".join(f'<image id="logo-{n.split(".")[0]}" href="{logo(n)}" width="1" height="1" preserveAspectRatio="xMidYMid meet"/>'
                   for n in names)


def use(name: str, x, y, w, h) -> str:
    return f'<use href="#logo-{name}" transform="translate({x},{y}) scale({w},{h})"/>'


def esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def library() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((ROOT / "data" / "library").glob("*.json"))]


def latest_scores() -> dict:
    path = sorted(glob.glob(str(ROOT / "eval" / "results" / "backtest_????-??-??.json")))[-1]
    return json.loads(pathlib.Path(path).read_text())["summary"]


def run_numbers(runs: list[dict]) -> dict:
    reasoning = {"MechanismAnalyst", "Verifier", "Investigator", "Critic", "Reporter"}
    ns = direct = checked = 0
    for run in runs:
        out = run.get("output") or {}
        # Each model call is one step in the run's trace, which names the provider that answered it.
        trace = ROOT / "data" / "library" / "traces" / f"trace-{run.get('run_id')}.jsonl"
        for line in (trace.read_text().splitlines() if trace.exists() else []):
            step = json.loads(line)
            if step.get("agent_name") in reasoning and step.get("model"):
                if step["model"].startswith("meterless:"):
                    ns += 1
                else:
                    direct += 1
        checked += len((out.get("verification") or {}).get("results") or [])
    return {"runs": len(runs), "nightshift": ns, "reasoning": ns + direct, "checked": checked}


# --------------------------------------------------------------------------------------------------- hero
def hero(case: dict) -> str:
    out = case["output"]
    calc, verdict, checks = out["calculator"], out["verdict"], out.get("checks") or {}
    hr, power = calc.get("expected_hr"), calc.get("power")
    fewer = round((1 - hr) * 100) if hr else None
    good, warn, bad = "#3ddc97", "#f5b84d", "#ff8a80"   # readable on the dark card
    rows = [("Target causes disease", "Yes", good),
            ("Drug moves a causal marker", "Yes", good),
            ("Benefit big enough", f"~{fewer}% fewer events" if fewer is not None else "not computable", warn),
            ("Trial big enough", f"Unlikely · {round(power * 100)}% chance" if power is not None else "–", bad)]
    cat = (verdict.get("primary_category") or "").replace("_", " ")
    diag = {"effect too small": "Effect too small for this trial"}.get(cat, cat.capitalize())
    row_svg = ""
    for i, (label, value, color) in enumerate(rows):
        y = 140 + i * 46
        delay = 0.6 + i * 0.55
        row_svg += f"""
    <g class="row" style="animation-delay:{delay}s">
      <rect x="900" y="{y}" width="324" height="38" rx="9" fill="#ffffff" fill-opacity="0.06" stroke="{color}" stroke-opacity="0.5"/>
      <circle cx="920" cy="{y + 19}" r="6" fill="{color}"/>
      <text x="934" y="{y + 24}" class="t" font-size="14" fill="#e8eef5">{esc(label)}</text>
      <text x="1212" y="{y + 24}" class="t" font-size="14" font-weight="600" fill="{color}" text-anchor="end">{esc(value)}</text>
    </g>"""
    ecg = ("M0 70 L120 70 L140 70 L152 40 L164 100 L176 20 L190 112 L204 70 L330 70 L350 70 L362 52 L374 88 L386 30 "
           "L400 104 L414 70 L560 70 L580 70 L592 44 L604 96 L616 24 L630 108 L644 70 L760 70")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 400" width="1280" height="400" role="img"
  aria-label="Trial Autopsy: AI agents that explain why a cardiovascular drug trial failed. Live example: Lp(a)HORIZON, pelacarsen, failed; diagnosis {esc(diag)}.">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0f1b2c"/><stop offset="1" stop-color="#0b3b45"/>
    </linearGradient>
    {logo_defs('nvidia-mark.svg', 'meterless.svg', 'zetaris-mark.svg')}
    <filter id="glow" x="-20%" y="-50%" width="140%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    <style>
      .t {{ font-family: {FONT}; }}
      .m {{ font-family: {MONO}; }}
      .ecg {{ stroke-dasharray: 1400; stroke-dashoffset: 1400; animation: draw 4.2s linear infinite; }}
      @keyframes draw {{ 0% {{ stroke-dashoffset: 1400; opacity: 1; }} 80% {{ stroke-dashoffset: 0; opacity: 1; }} 100% {{ stroke-dashoffset: 0; opacity: 0; }} }}
      .heart {{ transform-box: fill-box; transform-origin: center; animation: beat 1.4s ease-in-out infinite; }}
      @keyframes beat {{ 0%,60%,100% {{ transform: scale(1); }} 15% {{ transform: scale(1.22); }} 30% {{ transform: scale(0.96); }} 45% {{ transform: scale(1.12); }} }}
      .row {{ opacity: 0; animation: rowin 9s ease-out infinite; }}
      @keyframes rowin {{ 0% {{ opacity: 0; transform: translateX(14px); }} 8% {{ opacity: 1; transform: translateX(0); }} 88% {{ opacity: 1; }} 96%,100% {{ opacity: 0; }} }}
      .diag {{ opacity: 0; animation: rowin 9s ease-out infinite; animation-delay: 2.9s; }}
      .badge {{ opacity: 0; animation: fade 9s ease-out infinite; animation-delay: 3.4s; }}
      @keyframes fade {{ 0% {{ opacity: 0; }} 6% {{ opacity: 1; }} 86% {{ opacity: 1; }} 94%,100% {{ opacity: 0; }} }}
      .pill {{ opacity: 0; animation: pin 0.6s ease-out forwards; }}
      @keyframes pin {{ to {{ opacity: 1; }} }}
    </style>
  </defs>
  <rect width="1280" height="400" rx="24" fill="url(#bg)"/>
  <text x="56" y="70" class="m" font-size="14" letter-spacing="3" fill="{TEAL2}">OPEN AGENT HACKATHON 2026 · THE AGENT THAT CAN EXPLAIN WHY</text>
  <text x="56" y="132" class="t" font-size="60" font-weight="700" fill="#ffffff">Trial Autopsy</text>
  <path class="heart" d="M478 128 C 460 114, 454 100, 464 92 C 472 86, 478 92, 478 98 C 478 92, 484 86, 492 92 C 502 100, 496 114, 478 128 Z" fill="#e5484d"/>
  <text x="56" y="176" class="t" font-size="24" fill="#cfe9ee">Why did a heart-drug trial fail, and who is next?</text>
  <text x="56" y="212" class="t" font-size="16" fill="#9fb4c4">Nine steps read the registry, human genetics, the literature and company filings,</text>
  <text x="56" y="236" class="t" font-size="16" fill="#9fb4c4">run four fixed checks and write a diagnosis in which every number links to a verbatim quote.</text>
  <g transform="translate(56,252) scale(1,0.8)">
    <path class="ecg" d="{ecg}" fill="none" stroke="{TEAL2}" stroke-width="3" stroke-linejoin="round" filter="url(#glow)"/>
  </g>
  <g class="t" font-size="13" font-weight="600">
    <g class="pill" style="animation-delay:0.2s"><rect x="56" y="346" width="186" height="30" rx="15" fill="#ffffff" fill-opacity="0.08"/>
      {use('nvidia-mark', 68, 353, 24, 16)}<text x="98" y="366" fill="#d6f0a8">NVIDIA Nemotron</text></g>
    <g class="pill" style="animation-delay:0.5s"><rect x="252" y="346" width="196" height="30" rx="15" fill="#ffffff" fill-opacity="0.08"/>
      {use('meterless', 264, 353, 16, 16)}<text x="288" y="366" fill="#e8eef5">Meterless Nightshift</text></g>
    <g class="pill" style="animation-delay:0.8s"><rect x="458" y="346" width="214" height="30" rx="15" fill="#ffffff" fill-opacity="0.08"/>
      {use('meterless', 470, 353, 16, 16)}<text x="494" y="366" fill="#e8eef5">Meterless World Model</text></g>
    <g class="pill" style="animation-delay:1.1s"><rect x="682" y="346" width="180" height="30" rx="15" fill="#ffffff" fill-opacity="0.08"/>
      {use('zetaris-mark', 694, 353, 16, 16)}<text x="718" y="366" fill="#c9cdfb">Zetaris SQL checks</text></g>
  </g>
  <rect x="880" y="40" width="364" height="320" rx="16" fill="#ffffff" fill-opacity="0.05" stroke="#ffffff" stroke-opacity="0.12"/>
  <text x="900" y="72" class="m" font-size="11" letter-spacing="2" fill="{TEAL2}">● REAL EXAMPLE · LIVE RUN</text>
  <g class="badge"><rect x="1158" y="56" width="66" height="24" rx="6" fill="none" stroke="#ff8a80"/>
    <text x="1191" y="73" class="t" font-size="12" font-weight="700" fill="#ff8a80" text-anchor="middle">FAILED</text></g>
  <text x="900" y="112" class="t" font-size="21" font-weight="700" fill="#ffffff">Lp(a)HORIZON · pelacarsen</text>
  {row_svg}
  <g class="diag"><text x="900" y="{140 + 4 * 46 + 16}" class="t" font-size="14" fill="#9fb4c4">Diagnosis</text>
    <text x="972" y="{140 + 4 * 46 + 16}" class="t" font-size="14" font-weight="700" fill="#ffffff">{esc(diag)}</text></g>
</svg>
"""


# --------------------------------------------------------------------------------------------------- pipeline
def pipeline() -> str:
    steps = [("Intake", "code"), ("Case builder", "extraction"), ("Mechanism analyst", "Nemotron"),
             ("Calculator", "code"), ("Verifier", "SQL in Zetaris"), ("Investigator", "Nemotron"),
             ("Critic", "can send back"), ("Reporter", "Nemotron")]
    w, h, gap, x0, y0 = 136, 62, 16, 40, 168
    nodes, cx = "", []
    for i, (name, sub) in enumerate(steps):
        x = x0 + i * (w + gap)
        cx.append(x + w / 2)
        ai = sub not in ("code",)
        stroke = TEAL if ai else "#b8c4d2"
        nodes += f"""
    <g class="node" style="animation-delay:{i * 0.45:.2f}s">
      <rect x="{x}" y="{y0}" width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="{stroke}" stroke-width="2"{' stroke-dasharray="5 4"' if not ai else ''}/>
      <text x="{x + w / 2}" y="{y0 + 27}" class="t" font-size="13" font-weight="700" fill="{INK}" text-anchor="middle">{esc(name)}</text>
      <text x="{x + w / 2}" y="{y0 + 46}" class="t" font-size="12" fill="{MUTED}" text-anchor="middle">{esc(sub)}</text>
    </g>"""
    line_y = y0 + h / 2
    flow = f"M{x0 + w} {line_y} " + " ".join(f"L{x0 + i * (w + gap)} {line_y} M{x0 + i * (w + gap) + w} {line_y}" for i in range(1, len(steps)))
    path = f"M{x0 + w} {line_y} L{x0 + (len(steps) - 1) * (w + gap)} {line_y}"
    arrows = "".join(f'<path d="M{x0 + i * (w + gap) - 7} {line_y - 5} L{x0 + i * (w + gap)} {line_y} L{x0 + i * (w + gap) - 7} {line_y + 5}" fill="none" stroke="#9aa8b8" stroke-width="2"/>'
                     for i in range(1, len(steps)))
    dots = "".join(f'<circle r="5" fill="{TEAL2}"><animateMotion dur="6s" begin="{d}s" repeatCount="indefinite" path="{path}"/></circle>'
                   for d in (0, 1.5, 3, 4.5))
    # critic loop back to the analyst
    c_crit, c_ma = cx[6], cx[2]
    loop = f"M{c_crit} {y0 + h} C {c_crit} {y0 + h + 70}, {c_ma} {y0 + h + 70}, {c_ma} {y0 + h + 4}"
    # sources -> Zetaris -> case builder ; verifier <-> zetaris
    zx, zy = cx[1] - 10, 52
    sources = f"""
    <g class="t">
      <rect x="{x0}" y="{zy}" width="250" height="60" rx="12" fill="#f3f4fe" stroke="#9ba3f2" stroke-width="1.5"/>
      {use('zetaris-mark', x0 + 14, zy + 12, 20, 20)}
      <text x="{x0 + 42}" y="{zy + 28}" font-size="15" font-weight="700" fill="{INK}">Zetaris · Lightning SQL</text>
      <text x="{x0 + 14}" y="{zy + 49}" font-size="12" fill="{MUTED}">registry · papers · filings · date cut-off in SQL</text>
    </g>
    <path d="M{x0 + 125} {zy + 60} C {x0 + 125} {zy + 90}, {cx[1]} {zy + 80}, {cx[1]} {y0}" fill="none" stroke="{ZET}" stroke-width="2" class="pulse"/>
    <path d="M{x0 + 230} {zy + 60} C {x0 + 380} {zy + 90}, {cx[4]} {zy + 70}, {cx[4]} {y0}" fill="none" stroke="{ZET}" stroke-width="2.4" stroke-dasharray="6 5" class="dash"/>
    <text x="{cx[4] + 10}" y="{y0 - 12}" class="t" font-size="12" font-weight="700" fill="{ZET}">SQL written by Nemotron</text>"""
    wm = f"""
    <g class="t">
      <rect x="{cx[5] - 20}" y="{zy}" width="300" height="60" rx="12" fill="#eef7f8" stroke="#8cc3c9" stroke-width="1.5"/>
      {use('meterless', cx[5] - 6, zy + 12, 20, 20)}
      <text x="{cx[5] + 22}" y="{zy + 28}" font-size="15" font-weight="700" fill="{INK}">Meterless World Model</text>
      <text x="{cx[5] - 6}" y="{zy + 49}" font-size="12" fill="{MUTED}">every fact with its source and date</text>
    </g>
    <path d="M{cx[2]} {y0} C {cx[2]} {zy + 100}, {cx[5]} {zy + 110}, {cx[5] + 40} {zy + 60}" fill="none" stroke="{TEAL}" stroke-width="2" class="pulse"/>"""
    ns = f"""
    <g class="t">
      <rect x="{x0 + 250}" y="{y0 + h + 92}" width="560" height="56" rx="12" fill="#f3f9e8" stroke="#a8d36a" stroke-width="1.5"/>
      {use('meterless', x0 + 266, y0 + h + 110, 20, 20)}
      <text x="{x0 + 294}" y="{y0 + h + 126}" font-size="15" font-weight="700" fill="{INK}">Meterless Nightshift</text>
      <text x="{x0 + 456}" y="{y0 + h + 126}" font-size="14" fill="{MUTED}">serves every reasoning step from</text>
      {use('nvidia-mark', x0 + 678, y0 + h + 110, 30, 20)}
      <text x="{x0 + 714}" y="{y0 + h + 126}" font-size="15" font-weight="700" fill="{NV}">Nemotron</text>
    </g>"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 420" width="1280" height="420" role="img"
  aria-label="The pipeline: intake, case builder, mechanism analyst, calculator, Verifier with SQL in Zetaris, investigator, critic that can send the work back, reporter; facts in the Meterless World Model; reasoning on NVIDIA Nemotron through Meterless Nightshift.">
  <defs>{logo_defs('nvidia-mark.svg', 'meterless.svg', 'zetaris-mark.svg')}<style>
    .t {{ font-family: {FONT}; }}
    .node {{ opacity: 0; animation: up 0.6s ease-out forwards; }}
    @keyframes up {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    .loop {{ stroke-dasharray: 7 6; animation: run 1.2s linear infinite; }}
    @keyframes run {{ to {{ stroke-dashoffset: -26; }} }}
    .dash {{ animation: run 1.4s linear infinite; }}
    .pulse {{ animation: pulse 2.4s ease-in-out infinite; }}
    @keyframes pulse {{ 0%,100% {{ stroke-opacity: 0.35; }} 50% {{ stroke-opacity: 1; }} }}
  </style></defs>
  <rect width="1280" height="420" rx="24" fill="#f7f9fb" stroke="{LINE}"/>
  {sources}
  {wm}
  <path d="{flow}" fill="none" stroke="#9aa8b8" stroke-width="2"/>
  {arrows}
  {dots}
  <path d="{loop}" fill="none" stroke="{WARN}" stroke-width="2.2" class="loop"/>
  <text x="{(c_crit + c_ma) / 2}" y="{y0 + h + 66}" class="t" font-size="12" font-weight="700" fill="{WARN}" text-anchor="middle">a serious issue sends the work back</text>
  {nodes}
  {ns}
</svg>
"""


# --------------------------------------------------------------------------------------------------- results
def results(scores: dict, numbers: dict) -> str:
    items = [
        (scores["prediction_correct"], scores["prediction_scored"], "outcomes called right", "where the trial could be sized", TEAL),
        (scores["category_correct"], scores["completed"], "experts' main cause", "named as the main cause", TEAL),
        (scores["category_overlap"], scores["completed"], "experts' cause named", "among the causes given", TEAL),
        (scores["expected_hr_within_0_03"], scores["expected_hr_compared"], "effect size on target", "expected HR within 0.03", TEAL),
        (numbers["nightshift"], numbers["reasoning"], "reasoning calls", "through Meterless Nightshift", NV),
    ]
    cards = ""
    for i, (a, b, title, sub, color) in enumerate(items):
        x = 40 + i * 244
        frac = a / b if b else 0
        cards += f"""
    <g class="card" style="animation-delay:{i * 0.35:.2f}s">
      <rect x="{x}" y="58" width="224" height="132" rx="16" fill="#ffffff" stroke="{LINE}"/>
      <text x="{x + 20}" y="108" class="t" font-size="38" font-weight="700" fill="{INK}">{a}<tspan font-size="22" fill="{MUTED}" font-weight="500">/{b}</tspan></text>
      <text x="{x + 20}" y="134" class="t" font-size="14" font-weight="700" fill="{INK2}">{esc(title)}</text>
      <text x="{x + 20}" y="154" class="t" font-size="12" fill="{MUTED}">{esc(sub)}</text>
      <rect x="{x + 20}" y="168" width="184" height="6" rx="3" fill="#edf1f5"/>
      <rect x="{x + 20}" y="168" width="0" height="6" rx="3" fill="{color}">
        <animate attributeName="width" from="0" to="{184 * frac:.1f}" dur="1.4s" begin="{0.4 + i * 0.35:.2f}s" fill="freeze"/>
      </rect>
    </g>"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 230" width="1280" height="230" role="img"
  aria-label="Blind backtest on {scores['completed']} finished trials: outcome right {scores['prediction_correct']} of {scores['prediction_scored']}, main cause {scores['category_correct']} of {scores['completed']}, cause named {scores['category_overlap']} of {scores['completed']}, expected hazard ratio within 0.03 {scores['expected_hr_within_0_03']} of {scores['expected_hr_compared']}; {numbers['nightshift']} of {numbers['reasoning']} reasoning calls through Meterless Nightshift.">
  <defs><style>
    .t {{ font-family: {FONT}; }}
    .card {{ opacity: 0; animation: up 0.7s ease-out forwards; }}
    @keyframes up {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  </style></defs>
  <rect width="1280" height="230" rx="24" fill="#f7f9fb" stroke="{LINE}"/>
  <text x="40" y="38" class="t" font-size="13" font-weight="700" letter-spacing="2" fill="{TEAL}">BLIND BACKTEST · {scores['completed']} FINISHED TRIALS · SCORED AGAINST THE EXPERTS' LATER EXPLANATIONS</text>
  {cards}
  <text x="40" y="214" class="t" font-size="12" fill="{MUTED}">From eval/results/backtest_{scores['date']}.json and the {numbers['runs']} recorded runs in data/library/. Regenerate with python scripts/make_readme_art.py.</text>
</svg>
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    runs = library()
    pelacarsen = next(r for r in runs if r["run_id"] == "example-pelacarsen")
    (OUT / "hero.svg").write_text(hero(pelacarsen))
    (OUT / "pipeline.svg").write_text(pipeline())
    (OUT / "results.svg").write_text(results(latest_scores(), run_numbers(runs)))
    for p in sorted(OUT.glob("*.svg")):
        print(p.relative_to(ROOT), f"{p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
