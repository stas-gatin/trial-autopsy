# Pre-registered forecasts: PREVAIL, VICTORION-2P, ACCLAIM-Lp(a), 25 October 2026

Made before any of these trials has read out. The git commit that adds this file is the timestamp; do not edit it after
the commit, add a new dated file instead. Each forecast is a recorded run made on 24–25 October 2026 through Meterless
Nightshift on NVIDIA Nemotron, in Forecast mode (no announcement exists yet).

The predictive ranges below are 95% ranges around the calculator's expected hazard ratio. The trials have not published
their event counts, so the ranges assume each trial reaches the number of events the calculator says it needs for 80%
power; a larger trial gives a narrower range.

## 1. PREVAIL, obicetrapib (NCT05202509)

Run `example-obicetrapib` (data/library/example-obicetrapib.json). 9,541 patients with atherosclerotic cardiovascular
disease, MACE primary endpoint.

| Input | Value |
|---|---|
| LDL-C change, phase 3, placebo-adjusted (10 mg, day 84) | −31.9% |
| apoB change, phase 2, placebo-adjusted | −26.3% |
| LDL-C-equivalent reduction used | 0.807 mmol/L |
| Events needed for 80% power | 781 |

Calculator: expected HR **0.818** (about 18% fewer events). Diagnosis: success with caveats. The benefit comes from LDL-C
and apoB lowering; the large HDL-C rise is not counted, because raising HDL-C is not causal (the CETP inhibitor
lesson from ILLUMINATE, dal-OUTCOMES and ACCELERATE). Follow-up and event count are unknown.

**Prediction.** The primary endpoint is met; the point estimate falls between **HR 0.71 and 0.94**.
**Proven wrong if** the primary endpoint is missed or the point estimate is above 0.94.

## 2. VICTORION-2P, inclisiran (NCT05030428)

Run `example-inclisiran` (data/library/example-inclisiran.json). 17,004 patients with established cardiovascular
disease, 3-point MACE primary endpoint.

| Input | Value |
|---|---|
| LDL-C change, phase 3, placebo-adjusted (ORION-11, time-averaged) | −41% |
| LDL-C-equivalent reduction used | 0.82 mmol/L |
| Events needed for 80% power | 757 |

Calculator: expected HR **0.816**. Diagnosis: robust success (high confidence): PCSK9 is genetically validated and the
LDL-C lowering is consistent across the phase 3 programme. Follow-up and event count are unknown.

**Prediction.** The primary endpoint is met; the point estimate falls between **HR 0.71 and 0.94**.
**Proven wrong if** the primary endpoint is missed or the point estimate is above 0.94.

## 3. ACCLAIM-Lp(a), lepodisiran (NCT06292013)

Run `example-lepodisiran` (data/library/example-lepodisiran.json). 17,300 adults with Lp(a) ≥ 175 nmol/L.

**No prediction: the analysis abstained.** The calculator found no verified placebo-adjusted Lp(a) change at the phase 3
dose ("no measured change in Lp(a): the effect is unknown, not zero"), so it does not estimate a hazard ratio. This is
recorded as an abstention, not as a forecast; it is listed so that the record shows every forecast that was run.
