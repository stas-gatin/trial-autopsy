# Runs through Meterless Nightshift

The 26 recorded analyses in `data/library/` (24 blind backtests and 2 examples; the third example is the ACCELERATE
backtest itself), run live on 22 and 23 October 2026 with Meterless Nightshift M1-M4 0.92.1 on 127.0.0.1:8088.
Nightshift lanes: Nemotron 3 Ultra 550B A55B and Nemotron 3.5 Super VL 120B A12B, both on its NVIDIA provider.
Nightshift settings: tier-split off, the app (`meterless-app: trial-autopsy`) bound to `fast`.

The five reasoning steps (mechanism analyst, Verifier, investigator, critic, reporter) were served by Nightshift. Extraction and
read-through called NVIDIA's API directly with reasoning off, because Nightshift does not pass the reasoning switch on.
Counts come from each run's step log, `logs/trace-<run>.jsonl`: every model call is one step that names the provider and
the lane that answered (the `model` field of Nightshift's response). Extraction counts come from each run's `output.usage`.
Runs repeated after a fix reused the recordings of unchanged steps (`LLM_REPLAY_FIRST`), so their time is shorter.

| Run | Time (s) | Reasoning calls through Nightshift | Lane that answered (calls) | Direct NVIDIA fallback | Extraction and read-through calls (NVIDIA API) |
|---|---|---|---|---|---|
| bt-accelerate | 1500 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-accord_lipid | 1576 | 10 | nemotron-3-ultra-550b-a55b (9), nemotron-3.5-super-vl-120b-a12b (1) | 1 | 15 |
| bt-aim_high | 498 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 12 |
| bt-aurora | 1426 | 9 | nemotron-3-ultra-550b-a55b (9) | 2 | 15 |
| bt-cantos | 459 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-clear_outcomes | 549 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-dal_outcomes | 671 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-fourier | 739 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-hps2_thrive | 1544 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 11 |
| bt-ideal | 1586 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-illuminate | 596 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 14 |
| bt-improve_it | 610 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-jupiter | 1237 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-odyssey_outcomes | 601 | 5 | nemotron-3-ultra-550b-a55b (4), nemotron-3.5-super-vl-120b-a12b (1) | 2 | 15 |
| bt-prominent | 1504 | 11 | nemotron-3-ultra-550b-a55b (10), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-reduce_it | 1242 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 12 |
| bt-reveal | 359 | 9 | nemotron-3-ultra-550b-a55b (9) | 0 | 15 |
| bt-sharp | 862 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-solid_timi52 | 373 | 6 | nemotron-3-ultra-550b-a55b (5), nemotron-3.5-super-vl-120b-a12b (1) | 0 | 15 |
| bt-spire2 | 1323 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-stability | 459 | 6 | nemotron-3-ultra-550b-a55b (6) | 0 | 15 |
| bt-strength | 641 | 11 | nemotron-3-ultra-550b-a55b (9), nemotron-3.5-super-vl-120b-a12b (2) | 0 | 12 |
| bt-tnt | 605 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 15 |
| bt-vista16 | 318 | 4 | nemotron-3-ultra-550b-a55b (4) | 0 | 14 |
| example-olpasiran | 799 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 10 |
| example-pelacarsen | 526 | 11 | nemotron-3-ultra-550b-a55b (11) | 0 | 12 |

**All 26 runs:** 228 of 233 reasoning calls through Nightshift, 5 direct fallbacks; lanes: nemotron-3-ultra-550b-a55b 222, nemotron-3.5-super-vl-120b-a12b 6. 367 extraction and read-through calls went to NVIDIA's API.

**The direct fallbacks.** Each came after Nightshift cut the same request twice because no first token arrived in time
(its 45-second cloud limit). The step log names the provider that answered, and the recording of each such answer
(`data/llm_cache/`) says "Nightshift cut or stalled the request twice: direct NVIDIA API". I reported the limit to Meterless, who are making it configurable.

**Signed receipts.** Nightshift's `GET /v1/meterless/receipts` answers this build with HTTP 501 (`not_available_in_web`:
signed receipts come from the desktop daemon key), so the runs carry no signed receipts; the step logs and recordings are
the evidence.
