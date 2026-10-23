# Runs through Meterless Nightshift

The 26 recorded analyses in `data/library/` (24 blind backtests and 2 examples; the third example is the ACCELERATE
backtest itself), run live in one fresh pass on the night of 23 to 24 October 2026 with Meterless Nightshift M1-M4
0.92.1 on 127.0.0.1:8088, one analysis at a time, every model answer recorded anew into an empty folder.
Nightshift lanes: Nemotron 3 Ultra 550B A55B and Nemotron 3.5 Super VL 120B A12B, both on its NVIDIA provider.
Nightshift settings: tier-split off, the app (`meterless-app: trial-autopsy`) bound to `fast`.

The five reasoning steps (mechanism analyst, Verifier, investigator, critic, reporter) were served by Nightshift. Extraction and
read-through called NVIDIA's API directly with reasoning off, because Nightshift does not pass the reasoning switch on.
Counts come from each run's step log, `logs/trace-<run>.jsonl`: every model call is one step that names the provider and
the lane that answered (the `model` field of Nightshift's response). Extraction counts come from each run's `output.usage`.

Time is the full analysis in that pass (median 14 minutes, 4 to 37). After a fix to how the
outcome of an autopsy is read, 21 analyses repeated only their final report step, reusing every other recorded answer,
so their run record (`execution_time_seconds`) shows that short repeat.

| Run | Time of the full run (s) | Reasoning calls through Nightshift | Lane that answered (calls) | Direct NVIDIA fallback | Extraction and read-through calls (NVIDIA API) |
|---|---|---|---|---|---|
| bt-accelerate | 833 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-accord_lipid | 946 | 11 | nemotron-3-ultra-550b-a55b (9), nemotron-3.5-super-vl-120b-a12b (2) | 0 | 15 |
| bt-aim_high | 1351 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 11 |
| bt-aurora | 1099 | 6 | nemotron-3-ultra-550b-a55b (5), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 14 |
| bt-cantos | 1506 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-clear_outcomes | 1062 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-dal_outcomes | 1433 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-fourier | 928 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-hps2_thrive | 2030 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 12 |
| bt-ideal | 1293 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-illuminate | 2224 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 14 |
| bt-improve_it | 947 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-jupiter | 874 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-odyssey_outcomes | 544 | 4 | nemotron-3-ultra-550b-a55b (3), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-prominent | 727 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-reduce_it | 785 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 11 |
| bt-reveal | 826 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 14 |
| bt-sharp | 385 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-solid_timi52 | 441 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-spire2 | 520 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-stability | 475 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-strength | 282 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 12 |
| bt-tnt | 595 | 9 | nemotron-3-ultra-550b-a55b (9) | 0 | 15 |
| bt-vista16 | 395 | 9 | nemotron-3-ultra-550b-a55b (9) | 0 | 15 |
| example-olpasiran | 221 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 10 |
| example-pelacarsen | 398 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 12 |

**All 26 runs:** 235 of 235 reasoning calls through Nightshift, 0 direct fallbacks; lanes: nemotron-3-ultra-550b-a55b 228, nemotron-3.5-super-vl-120b-a12b 7. 365 extraction and read-through calls went to NVIDIA's API.

**The direct fallback.** If Nightshift cuts the same request twice because no first token arrives in time (its 45-second
cloud limit), the app calls NVIDIA directly and the step log names that provider. It was not needed in this pass; in earlier
runs it answered 5 calls. I reported the limit to Meterless, who are making it configurable.

**Signed receipts.** Nightshift's `GET /v1/meterless/receipts` answers this build with HTTP 501 (`not_available_in_web`:
signed receipts come from the desktop daemon key), so the runs carry no signed receipts; the step logs and recordings are
the evidence.
