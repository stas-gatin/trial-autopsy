# Data schema

All curated data is JSON. Every number must carry its source and, where possible, an exact quote.

## Fact object

Every factual value in a case file uses this shape:

```json
{
  "value": 12092,
  "unit": "patients",
  "source_id": "S2",
  "quote": "A total of 12,092 patients underwent randomization",
  "location": "Abstract",
  "verified_primary": true,
  "note": ""
}
```

- `value`: number, string or null. Use null with a `note` when the fact was searched for and NOT FOUND.
- `quote`: verbatim text from the source. Use an empty string only when the value comes from a table or figure you could not quote; say so in `note`.
- `verified_primary`: true only when you read the number in the primary source yourself (journal article, registry record, company press release or SEC filing). Set it to false when the number came from a secondary summary, search snippet or news article.

## Source object

```json
{
  "id": "S2",
  "type": "journal | registry | press_release | sec_filing | database | conference | news | other",
  "title": "Evacetrapib and Cardiovascular Outcomes in High-Risk Vascular Disease",
  "url": "https://www.nejm.org/doi/full/10.1056/NEJMoa1609581",
  "doi": "10.1056/NEJMoa1609581",
  "pmid": "28514624",
  "pmcid": null,
  "pub_date": "2017-05-18",
  "access": "open_full_text | abstract_only | paywalled | registry | sec | press_release",
  "open_full_text_url": null
}
```

- `access` records how the number can be reached without payment. `abstract_only` means only the abstract is free. If a free full text exists (PMC, Europe PMC, publisher open access), put its link in `open_full_text_url`.
- `pub_date` is the first public date. For a journal article, use the online-first date if known. It is used for the "time machine" cut-off.

## Case file: `data/golden_set/<case_id>.json`

See `data/golden_set/_TEMPLATE.json`. Sections:

- `meta`: case id, trial name, NCT id, drug, sponsor, target, mechanism, modality, indication, `readout_date` (first public topline announcement), `outcome_label` (`failure | success | stopped_harm | pending`), `set` (`known | fresh | forecast`).
- `design`: n_randomized, median_followup, primary_endpoint, inclusion_key_criteria, planned_events, assumed_rrr, power, alpha, stopped_early (with reason).
- `baseline`: baseline levels of relevant biomarkers.
- `biomarker_effects`: list of placebo-adjusted on-treatment changes `{biomarker, change_pct, change_abs, unit, timepoint, method, phase}`. `phase` is `phase2` or `phase3`. `method` records how a value was measured, for example LDL by Friedewald, by direct assay or by beta-quantification.
- `outcome`: hr, ci_low, ci_high, p, events_treatment, events_control.
- `safety_signals`: list of facts.
- `genetics`: human genetic evidence on the target and on the causal biomarker, each item with `pub_date`.
- `consensus_explanation`: `{category, secondary_categories, summary, source_ids}` holding the field's accepted explanation. This is the **answer key** and is never shown to the agent.
  - `category` is the single primary reason.
  - `secondary_categories` lists contributing reasons.
  - Failure codes: `target_not_causal | biomarker_not_causal | effect_too_small | off_target_harm | population | design_or_endpoint | execution_or_nonscientific | insufficient_data`.
  - Success codes: `robust_success | narrow_success | success_with_caveats`.
  - See `docs/DATA_PLAN.md`, section 4.
- `pre_readout`: which key facts were publicly available before `readout_date` (source `pub_date` < `readout_date`).
- `access_summary`: how many key numbers were found in open full text, abstract only, registry or SEC, versus paywalled only.
- `sources`: list of source objects.
- `open_questions`: anything not found or contradictory.

## Reference table: `data/reference/coefficients.json`

One entry per biomarker:
`{biomarker, causal: yes|no|uncertain, effect: {measure, value, ci_low, ci_high, per_unit, timescale}, evidence_type: trial_meta_analysis|mendelian_randomization|..., sources, measurement_pitfalls, note}`.
