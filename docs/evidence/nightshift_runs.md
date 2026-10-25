# Runs through Meterless Nightshift

The 41 recorded analyses in `data/library/` (36 blind backtests and 5 examples; the sixth example is the ACCELERATE
backtest itself). Every model answer was recorded live on 24 and 25 October 2026 with Meterless Nightshift M1-M4 0.92.1
on 127.0.0.1:8088, one analysis at a time, into a fresh recording folder. Nightshift lanes: Nemotron 3 Ultra 550B A55B and
Nemotron 3.5 Super VL 120B A12B, both on its NVIDIA provider. Nightshift settings: tier-split off, the app
(`meterless-app: trial-autopsy`) bound to `fast`.

The five reasoning steps (mechanism analyst, Verifier, investigator, critic, reporter) were served by Nightshift. Extraction and
read-through called NVIDIA's API directly with reasoning off, because Nightshift does not pass the reasoning switch on.
Counts come from each run's step log, `logs/trace-<run>.jsonl`: every model call is one step that names the provider and
the lane that answered (the `model` field of Nightshift's response).

Time is the first full live run of each analysis (median 12 minutes, 5 to 36). Later passes reused the
recorded answers of every call whose prompt had not changed (`LLM_REPLAY_FIRST`): after fixes to how the outcome of an
autopsy is read and how a source's metadata is stored, and a final pass on 25 October with the Meterless World Model engine
running. Those steps are marked `"cached": true` in the step log; each recording in `data/llm_cache/` keeps the provider,
the model, the time it was recorded and the token usage of the live call.

| Run | Time of the first full run (s) | Reasoning calls through Nightshift | Lane that answered (calls) | Direct NVIDIA fallback | Extraction and read-through calls (NVIDIA API) |
|---|---|---|---|---|---|
| bt-accelerate | 1513 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-accord_lipid | 686 | 9 | nemotron-3-ultra-550b-a55b (9) | 0 | 14 |
| bt-aim_high | 647 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 13 |
| bt-allhat_llt | 562 | 4 | nemotron-3-ultra-550b-a55b (4) | 0 | 12 |
| bt-ascend_omega3 | 1037 | 10 | nemotron-3-ultra-550b-a55b (10), nemotron-3-ultra-550b-a55b (direct) (1) | 1 | 13 |
| bt-aurora | 795 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-cantos | 1251 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-cards | 617 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 13 |
| bt-cirt | 437 | 9 | nemotron-3-ultra-550b-a55b (9) | 0 | 17 |
| bt-clear_outcomes | 848 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 17 |
| bt-corona | 463 | 9 | nemotron-3-ultra-550b-a55b (9) | 0 | 16 |
| bt-dal_outcomes | 1432 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 16 |
| bt-fourier | 991 | 10 | nemotron-3-ultra-550b-a55b (9), nemotron-3-ultra-550b-a55b (direct) (1), nemotron-3.5-super-vl-120b-a12b (1) | 1 | 14 |
| bt-gissi_hf | 615 | 4 | nemotron-3-ultra-550b-a55b (4) | 0 | 17 |
| bt-hope3 | 1925 | 9 | nemotron-3-ultra-550b-a55b (8), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 16 |
| bt-hps2_thrive | 396 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 12 |
| bt-ideal | 1588 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 14 |
| bt-illuminate | 2214 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-improve_it | 1093 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 17 |
| bt-jupiter | 1286 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 17 |
| bt-odyssey_outcomes | 1328 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 16 |
| bt-omemi | 779 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 13 |
| bt-prominent | 1326 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-prove_it | 845 | 11 | nemotron-3-ultra-550b-a55b (9), nemotron-3.5-super-vl-120b-a12b (2) | 0 | 12 |
| bt-reduce_it | 355 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 12 |
| bt-reveal | 777 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-sharp | 582 | 10 | nemotron-3-ultra-550b-a55b (8), nemotron-3-ultra-550b-a55b (direct) (1), nemotron-3.5-super-vl-120b-a12b (2) | 1 | 15 |
| bt-solid_timi52 | 956 | 5 | nemotron-3-ultra-550b-a55b (5), nemotron-3-ultra-550b-a55b (direct) (1) | 1 | 15 |
| bt-sparcl | 478 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 14 |
| bt-spire2 | 433 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-stability | 607 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-strength | 581 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 12 |
| bt-tnt | 1529 | 9 | nemotron-3-ultra-550b-a55b (8), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 14 |
| bt-vesalius_cv | 551 | 6 | nemotron-3-ultra-550b-a55b (5), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 16 |
| bt-vista16 | 1385 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-vital_omega3 | 411 | 6 | nemotron-3-ultra-550b-a55b (5), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 12 |
| example-inclisiran | 479 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 10 |
| example-lepodisiran | 356 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 1 |
| example-obicetrapib | 760 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 10 |
| example-olpasiran | 328 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 10 |
| example-pelacarsen | 880 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 13 |

**All 41 runs:** 373 of 377 reasoning calls through Nightshift, 4 direct fallbacks; lanes: nemotron-3-ultra-550b-a55b 357, nemotron-3-ultra-550b-a55b (direct) 4, nemotron-3.5-super-vl-120b-a12b 16. 568 extraction and read-through calls went to NVIDIA's API.

**The direct fallback.** If Nightshift cuts the same request twice because no first token arrives in time (its 45-second
cloud limit), the app calls NVIDIA directly and the step log names that provider. It answered 4 calls in these runs.
I reported the limit to Meterless, who are making it configurable.

**Signed receipts.** Nightshift's `GET /v1/meterless/receipts` answers this build with HTTP 501 (`not_available_in_web`:
signed receipts come from the desktop daemon key), so the runs carry no signed receipts; the step logs and recordings are
the evidence.
