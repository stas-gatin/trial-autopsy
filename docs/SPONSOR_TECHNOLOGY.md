# Sponsor technology in Trial Autopsy

How Zetaris, Meterless and NVIDIA are used, and what is distinctive about each. Every claim points to the code and to a way to check it. The numbers come from the 41 recorded analyses in `data/library/` (36 blind backtests and 5 examples; the sixth example is the ACCELERATE backtest itself). Every model answer was recorded live through Nightshift and the NVIDIA API on 24 and 25 October 2026; a final pass on 25 October ran every analysis again with the World Model engine running, reusing those recorded answers. Their logs are in `logs/`.

| Sponsor | Role in the product | Code |
|---|---|---|
| **Zetaris** | Data layer: the trial registry, the literature and company filings are read through Zetaris with SQL, and the date cut-off runs inside the query. The Verifier has Nemotron write the SQL that checks every number the calculator uses against its source | `app/tools/zetaris.py`, `app/tools/http.py`, `app/tools/europepmc.py`, `app/agents/verifier.py` |
| **Meterless** | Two roles. **Power:** Meterless Nightshift is the app's primary model provider. **Build:** the official World Model engine stores every source, entity and fact with its provenance | `app/llm/meterless_runtime.py`, `app/llm/generator.py`, `services/meterless/`, `app/engines/meterless.py`, `app/engines/world_model.py` |
| **NVIDIA** | Every AI step runs on NVIDIA Nemotron (3 Ultra and 3.5 Super) | `app/config.py`, `app/llm/generator.py` |

## Zetaris: the data layer

**What it does.** Trial Autopsy reads ClinicalTrials.gov, Europe PMC and SEC EDGAR through a Zetaris instance instead of calling those APIs directly. Each request is registered as a Zetaris REST session table and read back with Lightning SQL over the Zetaris REST API:

```sql
CREATE REST SESSION TABLE ta_<id> REQUEST (endpoint "<source URL>", method "GET", ...) HEADER (user-agent "...") BODY ()
SELECT to_json(r) FROM session.ta_<id> LATERAL VIEW explode(resultList.result) AS r
  WHERE r.firstPublicationDate <= '<as_of>'
```

**Why it matters here.** A blind analysis may only see documents published before the trial's result. For the literature, Zetaris applies that cut-off inside the SQL query, so a paper from after the cut-off is filtered out by the data layer before any agent sees it.

**Resilience.** If Zetaris is unreachable, the read falls back to the direct request and the run trace records a warning. Open Targets (a GraphQL POST API) is read directly.

**The Verifier: Nemotron writes SQL, Zetaris checks the numbers.** After the calculator has run, the Verifier takes each number it used (a biomarker change, planned events, follow-up) and checks it against its source through Zetaris:
1. The registry record and the cited papers are registered as Zetaris REST session tables (aliases `registry` and `papers`).
2. Nemotron writes one Lightning SQL query per number, for example:

   ```sql
   SELECT regexp_extract(r.abstractText, '([^.]*993[^.]*)', 1) FROM papers
     LATERAL VIEW explode(resultList.result) AS r WHERE r.pmid = '40185318'
   ```

3. Code accepts only one SELECT over the known tables, with no comments or other statements, and adds a row limit. In a blind backtest it also refuses fields known only after the readout: posted results, status, actual enrolment.
4. Zetaris runs the query. If Zetaris returns an error, Nemotron gets one chance to fix the SQL.
5. Nemotron judges each number from the rows that came back: confirmed, not found or contradicts. Code adds one rule: a number with no rows is never confirmed.

A contradiction goes to the critic as a serious issue, and the critic can send the work back (up to two rounds). Without Zetaris, the report says its numbers were not independently verified and lowers its confidence one level. Each report's *Numbers used* tab shows the verdict, the sentence found and the SQL for every number.

**Across the 41 analyses** the Verifier checked 146 numbers: 99 confirmed, 46 not found in the abstract or registry, 1 contradiction, which reached the critic:
- **dal-OUTCOMES:** "2.5 years" of follow-up meant that 80% of patients were followed for *at least* 2.5 years, not the median. The critic flagged it as a serious issue and sent the work back for a better follow-up estimate.

**How to check it.**
- Settings page → Zetaris card → *Test connection*; `scripts/smoke_test.py` runs `SELECT 1` over the REST API.
- Any live run's step log shows `read via Zetaris`, `register_tables`, `write_sql` and `query_zetaris` lines.
- The report footer says `data layer: Zetaris (N source reads via Lightning SQL)`; the *Numbers used* tab shows the Verifier's SQL and verdicts.
- `tests/test_verifier.py` covers the SQL guard, the cut-off rule, the run without Zetaris and a contradiction reaching the critic.

## Meterless Nightshift: the model runtime (Power)

**What it does.** Nightshift is the app's primary AI provider, following the Meterless Runtime provider contract (`app/llm/meterless_runtime.py`):
- it calls Nightshift's OpenAI-compatible endpoint (default `http://127.0.0.1:8088/v1`) with the runtime token from Nightshift's Console;
- every request carries the `meterless-app: trial-autopsy` header, and chat requests also carry `meterless-scope: user`;
- each step asks for a routing mode, chosen per step on the Settings page from Nightshift's `GET /v1/models` list (`meterless/deep`, `meterless/fast`, `meterless/auto`); Nightshift picks the Nemotron lane that answers;
- Settings has Auto Detect (ports 8088, 8090, 8787, 11434), paste of a connect link, and a connection test that reads `/v1/models` and sends one request.

The provider keys stay in Nightshift. Each response's `model` field names the lane that answered, and the trace records it. Answers are stored under the model that actually answered, so one lane's answer is never replayed as another model's.

**Through Nightshift in the recorded runs.** Nightshift ran with two lanes on its NVIDIA provider, Nemotron 3 Ultra and Nemotron 3.5 Super, and served the five reasoning steps: the mechanism analyst, the Verifier, the investigator, the critic and the reporter. In the 41 recorded runs 373 of the 377 calls went through Nightshift; the other 4 went to NVIDIA directly after Nightshift cut them twice. 361 answers came from Nemotron 3 Ultra and 16 from Nemotron 3.5 Super. By the amount of model work Nightshift is the main route: 2.54 million of the 3.6 million tokens (71%) and 76% of the generated tokens; by count, more calls go straight to NVIDIA (572 of 945), because extraction reads each document in many short calls with reasoning off. A full live analysis took a median of 13 minutes (5 to 37). The per-run table is in `docs/evidence/nightshift_runs.md`.

**What I learned running a pipeline through Nightshift.** Nightshift is built first for chat. A pipeline of strict steps needs a few settings, and I measured each:

| What I found | Effect on the pipeline | How the app handles it |
|---|---|---|
| Build 0.92.0 hid NVIDIA's models in the lane picker: `web-env.js` left `nvidia` out of `byokProviders` | Nemotron could not be added to a lane | Reported with the one-line fix ([Meterless/Meterless#4](https://github.com/Meterless/Meterless/issues/4)); Meterless shipped 0.92.1 the same night |
| *Tier-split*, on by default, adds a planner phase ("You are a careful planner...") before the request | Strict extraction rules were diluted: one document gave 9 facts straight through NVIDIA and 0 through Nightshift | Tier-split off (*Setup → Preferences*). The model then sees the app's own system prompt, which we confirmed |
| `chat_template_kwargs` (the reasoning switch) is not among the parameters Nightshift passes on | Extraction with reasoning on found 0 to 3 facts where reasoning off found 9, and took minutes instead of seconds | Extraction and read-through call NVIDIA's API directly with reasoning off; every reasoning step goes through Nightshift |
| For an identified app, the mode bound in the Console's connected apps decides the routing; the requested mode is recorded | One routing mode for the whole app | The app is bound to `fast`; Nightshift picks the lane |
| A cloud request with no first token after 45 s is cut, and the cut learns from observed latency | On a busy Nemotron 3 Ultra, long reasoning prompts were cut | Streamed requests, a watchdog for silent streams, retries, and a direct NVIDIA call after two cuts, recorded in the trace. In the final runs it was needed 0 times (5 times in earlier runs). I reported it, and Meterless is making the limit configurable |
| An answer cache keyed on the last 4,000 characters of the prompt, with replay for near-identical prompts | Two runs of one case differ mostly in their numbers, and one run got the previous run's report back word for word; a repeated connection test was answered from the cache in 0.3 s | Every request, and the connection test, carries a unique reference, so its cache key is new and its similarity stays below the replay floor |

**How to check it.**
- Settings page → Meterless card → *Auto Detect*, then *Test connection*: the routing modes Nightshift serves and the lane that answered.
- `scripts/smoke_test.py`: "Nemotron replies through Meterless Nightshift" passes only if a Nemotron model answered.
- In a run's step log, every reasoning step shows `meterless:nvidia/nemotron-...`; the report's *What this analysis used* panel lists the models.

## Meterless World Model: the evidence store (Build)

**What it does.** The official Meterless World Model reference engine (from github.com/Meterless/Meterless, unchanged, Apache-2.0) runs on the machine as a local service in `services/meterless/`. `run.py` starts it, and the Docker image includes it. Every source, entity and fact the agents produce is written to it, one namespace per run, with provenance:
- the step that found the fact;
- the run;
- the source URL;
- the source's publication date, used as the observation time.

Facts whose quote failed the word-for-word check are stored too, marked unverified with low confidence, so the record of everything extracted is complete; only verified facts reach the calculator and the report. The engine keeps an event-sourced, audited, idempotent world state in `data/meterless/.world/<run>.json`. `app/engines/world_model.py` implements the same three services the Meterless engine guides describe (WorldStore, IngestPipeline, ContextBuilder), and the analyst, the calculator, the Verifier and the investigator build their context from them.

Across the 41 analyses the engine stored 869 entities, 2,983 facts, 845 links and 7,440 audit entries; each run's audit log is `logs/world-<run_id>.jsonl`.

**How to check it.**
- `npm --prefix services/meterless run conformance` runs the official conformance suite: 8 of 8 checks pass (`docs/evidence/meterless_world_model_conformance.txt`).
- Settings page → Meterless World Model → *Test connection*; `scripts/smoke_test.py` checks the service.
- Each run's step log ends with a `world_model_sync` line, for pelacarsen "18 entities · 58 facts · 19 links · 147 audit entries in the Meterless World Model engine". The report footer shows the same counts.

## NVIDIA: the models

**What it does.** Every AI step runs on NVIDIA Nemotron.

| Step | Model in the recorded runs | Route | Reasoning |
|---|---|---|---|
| Mechanism analyst: picks the numbers the calculator uses | Nemotron 3 Ultra (550B) | Nightshift, deep route | on |
| Verifier: writes the SQL that checks each number in Zetaris | Nemotron 3 Ultra (550B) | Nightshift, fast route | on |
| Investigator: weighs every possible cause | Nemotron 3 Ultra (550B) | Nightshift, deep route | on |
| Critic: checks the work and can send it back | Nemotron 3 Ultra (550B) | Nightshift, fast route | on |
| Reporter | Nemotron 3 Ultra (550B) | Nightshift, fast route | requested off; Nightshift does not pass the switch |
| Extraction: copies numbers with verbatim quotes | Nemotron 3.5 Super (120B) | NVIDIA API | off |
| Read-through: rival drugs on the same target | Nemotron 3.5 Super (120B) | NVIDIA API | off |

Nightshift picks the lane for each request; with the app bound to `fast`, it served the reasoning steps almost entirely from Ultra.

**Built for open models.**
- The critic has its own instructions and reviews the analyst's work, and the Verifier checks the numbers against the sources, so the steps check each other.
- Calls are paced under the API's rate limit; malformed JSON is repaired by the same model, and a reasoning reply that holds no JSON (one cut off mid-thought) is asked once more.

**Code does the checks a model could get wrong.** Numbers come from verified quotes, and code:
- sets the direction of a change from the source's own words, reading the start of the sentence when a quote begins mid-sentence;
- converts units;
- fills planned events or a baseline from cited facts.

The run trace records each such step.

**Measured.** 36 finished trials, replayed blind and scored against the explanations experts published later (`eval/results/backtest_2026-10-25.md`). Six were added after the analysis code was frozen (`eval/results/backtest_2026-10-25_frozen6.md`):

| What is scored | All 36 | 6 added after the freeze |
|---|---|---|
| Experts' cause as the main cause | **26/36** | **4/6** |
| Success or failure forecast right, where the trial could be sized | **16/22** | **1/1** |
| Expected hazard ratio within 0.03 of the answer key | 8/25 | 0/1 |
| *Softer:* one of the causes named matches one of the experts' causes | 35/36 | 6/6 |

The calculator's probability of success has a Brier score of 0.137 on the 18 trials it could size (0.250 for always saying 50%); all 9 trials it gave below 20% or above 80% went the way it said.

**Why open models.** A drug company cannot send unpublished trial data to a cloud AI it does not control. Because every step runs on open Nemotron models, the same analysis can run on the company's own hardware, for example an NVIDIA DGX Spark, which Nightshift supports. In Trial Autopsy the switch is one setting, the model address; the agents, prompts and checks stay the same. I ran on NVIDIA's API and on Nightshift on a Mac; I have not run the system on a DGX Spark.

**How to check it.**
- Settings page: every AI step shows the NVIDIA logo and its Nemotron route or model.
- Each report's agent graph and *What this analysis used* panel show the model each step actually used.
- `eval/results/` holds the scores; `logs/` holds every recorded run's step log, and `scripts/check_replay.py` replays each run offline and checks it reproduces the live result.

## What is distinctive

- **Zetaris enforces the time machine at the data layer.** A blind analysis cannot leak future information, because SQL removes later papers before any agent sees them.
- **Zetaris is how the numbers are checked.** Nemotron writes the SQL, code guards it, Zetaris runs it against the registry and the papers, and a contradiction goes to the critic. In dal-OUTCOMES that loop showed that "2.5 years" of follow-up was a minimum for 80% of patients, not the median.
- **Meterless in both required roles.** Nightshift is the primary model runtime: 373 of the 377 reasoning calls in the recorded runs went through it. The World Model engine runs unchanged and passes its conformance suite. Along the way I found a Nightshift bug, reported its cause and fix (Meterless/Meterless#4), and Meterless shipped the fix the same night; I also reported the first-token limit, which Meterless is making configurable, and measured how Nightshift's tier-split, routing bind, reasoning switch, time limit and answer cache affect a pipeline, and documented the settings that work.
- **The whole pipeline runs on open NVIDIA models, and it is measured** on blind backtests against expert explanations, with every recorded run reproducible offline.
