# Pre-registered prediction: Lp(a)HORIZON, pelacarsen (NCT04023552), 23 October 2026

Made before the full results are public. The git commit that adds this file is the timestamp; do not edit it after the
commit, add a new dated file instead.

**What is known.** On 4 September 2026 Novartis announced that Lp(a)HORIZON missed its primary endpoint (4-point MACE in
patients with elevated Lp(a) and established cardiovascular disease). The hazard ratio, its confidence interval and the
Lp(a) lowering achieved were not disclosed. The full data are expected at the AHA Scientific Sessions on 7 November 2026.

**Our analysis.** The recorded run `example-pelacarsen` (data/library/example-pelacarsen.json, log
logs/trace-example-pelacarsen.jsonl) made on 22–23 October 2026 through Meterless Nightshift on NVIDIA Nemotron:

| Input (verified quotes, checked in Zetaris) | Value |
|---|---|
| Lp(a) change, phase 2, placebo-adjusted | −58.9% |
| LDL-C change, phase 2, placebo-adjusted | −19% (−8 mg/dL) |
| Planned primary events | 993 |
| Follow-up used | 3.25 years |

Calculator: expected HR **0.887** (about 11% fewer events), probability of success 47%. Diagnosis: **effect too small**
for this trial (high confidence): Lp(a) is causal, the drug lowers it, but the lowering buys too little benefit for 993
events to show.

**Prediction (falsifiable).** With 993 events the standard error of the log hazard ratio is about 0.063, so a missed
primary endpoint means an observed HR above about 0.883. Given that, and an expected HR of 0.887:

- the overall-population point estimate should fall between **HR 0.89 and 1.02** (95% predictive range);
- most likely between **0.90 and 0.95**: a real but undersized benefit, with a confidence interval crossing 1.

**Proven wrong if** the point estimate is above 1.02: no benefit at all would mean lowering Lp(a) does not reduce events
in these patients, the alternative explanation ("the biomarker is not causal"), and our diagnosis would be wrong.

The subgroup with Lp(a) ≥ 90 mg/dL gets a larger absolute lowering, so its hazard ratio should be lower than overall.
