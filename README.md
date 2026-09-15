# de-pipeline

A FHIR-based clinical data platform: batch ingestion of synthetic patient records into Postgres, a FastAPI backend for both automated ingestion and manual clinical intake, a Streamlit dashboard built to feel like something a clinician would actually use during a visit, and a Tableau-fed analytics layer on top. This README also doubles as a build log — the "why" behind the choices below mattered more to me while building this than it will to most readers, but it's here because most of what's actually worth learning from a project like this lives in the reasoning, not the final diff.

## What it does

1. Synthetic FHIR patient bundles (generated via Synthea) land in object storage (Cloudflare R2) as a compressed archive.
2. A batch worker streams through that archive, extracts the clinically relevant fields out of each bundle, and upserts them into Postgres — idempotently, so re-running the same archive twice never duplicates or corrupts data.
3. A FastAPI service sits in front of that database, exposing both the automated ingestion path and a manual clinical-entry path a front desk or clinician could use directly.
4. A Streamlit dashboard consumes that API and presents it the way an EHR would: look up a patient, see their chart, log a new visit.
5. A separate export script flattens the same data into CSVs for a Tableau cohort-analysis dashboard, since Tableau Public can't connect to Supabase live.

```
Synthea (synthetic FHIR generation)
        |
        v
Cloudflare R2 (raw bundle archive)
        |
        v
worker/batch_ingest.py  --(bad records)--> R2 dead-letter queue
        |
        v
Postgres (Supabase)  <---------------------+
        |                                  |
        v                                  |
api/main.py (FastAPI)  ---(manual intake)--+
        |
        v
dashboard/app.py (Streamlit)          tableau/csv_export.py --> Tableau
```

## See it in action

**Looking up a patient and reviewing their chart** — vitals trend charts, BMI/BP badges, and the tabbed Overview / Vitals Trends / Record Visit layout:

![Patient chart walkthrough](assets/patient-chart.gif)

**The identity-collision safety net, end to end:** looking up a patient whose name and birth date match an existing record, confirming it's actually a different person, and seeing the new chart tracked separately with its own vitals.

![Identity collision confirmation flow](assets/identity-collision.gif)

**Cohort-level analytics (Tableau, embedded in the dashboard):**

![Cohort overview](assets/cohort-dashboard.png)
![Per-patient drill-down](assets/cohort-dashboard-detail.png)

## Tech stack

Python (FastAPI, asyncpg, Pydantic, Streamlit, Plotly, boto3, Locust, slowapi), Postgres via Supabase, Cloudflare R2 for object storage, Tableau for cohort analytics, Docker Compose for local/reviewer setup, GitHub Actions for CI, pytest for testing.

## Running it locally

**Fastest path -- Docker Compose, no cloud accounts needed:**

```bash
docker compose up --build
```

This starts a disposable local Postgres, the API, and the dashboard together (`db` -> `api` -> `dashboard`, in that dependency order, with the API waiting on a real Postgres health check rather than a fixed sleep). Dashboard: `http://localhost:8501`. Interactive API docs: `http://localhost:8000/docs`. It's a local stand-in for the real architecture -- the deployed version runs against managed Supabase Postgres and Cloudflare R2, and the connection-pooling investigation below was run against that managed pooler specifically -- but it means anyone reviewing this project can run the application layer without provisioning either.

**Running it natively instead** (needed for the batch worker, or to reproduce the pooling experiment against a real managed Postgres):

Copy `.env.example` to `.env` and fill in `DATABASE_URL` (a Postgres connection string), an `API_KEY` of your choosing (required by the API's write endpoints -- the dashboard reads the same value from `.env` and sends it automatically), and the `R2_*` credentials if you're running the batch worker.

```bash
pip install -r requirements.txt

# apply the schema to your database
psql "$DATABASE_URL" -f db/schema.sql

# run the API
uvicorn api.main:app --reload

# run the dashboard (in a separate terminal)
streamlit run dashboard/app.py

# run the batch worker against an archive already sitting in R2
python -m worker.batch_ingest
```

## Testing

```bash
pytest tests/ -v
```

`tests/test_extraction.py` covers the pure parsing/calculation logic with no network or database dependency. `tests/test_api.py` runs against a real (test) database and covers the full API surface, including the identity-collision flow end to end. Both run automatically on every push and PR to `main` via `.github/workflows/ci.yml`, which spins up a throwaway Postgres 16 container for the duration of the run.

To reproduce the load-testing numbers above:

```bash
# terminal 1
POOL_MAX_SIZE=10 uvicorn api.main:app

# terminal 2
locust -f tests/locustfile.py --host http://127.0.0.1:8000 \
  --headless --users 100 --spawn-rate 10 --run-time 1m
```

Swap `POOL_MAX_SIZE` and `DATABASE_URL` (direct connection vs. the transaction-pooler connection string, port `6543`) to reproduce the other configurations.

## Repo layout

| Path | What it is |
|---|---|
| `worker/batch_ingest.py` | Batch ETL entry point — pulls the archive from R2, parses each bundle, chunks upserts to Postgres, routes failures to a dead-letter queue. |
| `shared/extraction.py` | FHIR parsing: pulls vitals and blood-pressure components out of raw bundle JSON, computes BMI, generates deterministic child-row IDs. |
| `shared/queries.py` | All raw SQL — upserts for the batch worker, reads for the API. |
| `shared/models.py` | Pydantic request/response schemas shared between the API and its tests. |
| `api/main.py` | The FastAPI service — ingestion, manual intake, patient lookup, observation history, patient snapshot, cohort stats, recent activity. |
| `dashboard/app.py` | The Streamlit clinical dashboard. |
| `db/schema.sql` | Table definitions and indexes for `patients`, `observations`, `conditions`. |
| `tableau/csv_export.py` | Flattens the DB into CSVs for the Tableau workbook. |
| `tests/` | Unit tests (pure extraction logic, no DB) and integration tests (real test DB, run in CI). |
| `tests/locustfile.py`, `tests/locustfile_baseline.py` | Load-test definitions used in the connection-pooling investigation below. |
| `tests/test_ingestion_cap.py` | Unit tests for the scheduled-ingestion batch-size math (boundary cases: cap already reached, small headroom, clamping against a forced random draw). |
| `.github/workflows/ci.yml` | Spins up an ephemeral Postgres container and runs the full test suite on every push/PR to `main`. |
| `.github/workflows/scheduled-ingestion.yml` | Passive, scheduled ingestion -- a cron-triggered Actions job that generates a fresh batch of synthetic patients via Synthea and ingests them automatically, capped and lineage-tagged. See "Scheduled, unattended ingestion" below. |
| `scripts/check_ingestion_cap.py` | Decides whether a scheduled run should do anything: queries the real patient count and computes a capped, randomized batch size. |
| `scripts/stage_synthea_batch.py` | Packages a scheduled run's Synthea output into an archive and uploads it to R2. |
| `scripts/validate_extraction_coverage.py` | Independent, second-opinion reconciliation: re-derives what extraction should have produced directly from a real archive's raw FHIR JSON and compares it against `extract_clinical_data()`'s actual output. See "Verifying extraction completeness, independently" below. |
| `docker-compose.yml`, `api/Dockerfile`, `dashboard/Dockerfile` | Runs the whole stack (a disposable local Postgres, the API, the dashboard) with one command -- see "Running it locally" below. |

## Design decisions worth explaining

**Idempotent upserts, not inserts.** Every write in `shared/queries.py` is an `ON CONFLICT (id) DO UPDATE`, using `COALESCE(new_value, existing_value)` so a partial or repeated record updates a row instead of duplicating it or nulling out fields the new record simply didn't include. Combined with `uuid.uuid5` (deterministic UUIDs derived from stable input, not random) for the blood-pressure sub-observations, this means the entire batch pipeline can be re-run against the same source data any number of times and converge to the same end state. That property — safe to re-run — mattered more to me than making the happy path fast, because in a real pipeline the happy path isn't the one that determines whether you trust the system.

**A dead-letter queue instead of a crash.** `batch_ingest.py` treats a malformed bundle as expected, not exceptional: encoding errors, JSON errors, and extraction failures are all caught individually, and the offending record (plus the reason) gets written to a `dlq_errors/` prefix in R2 instead of taking down the whole run. One bad file three thousand records into an archive shouldn't cost you the other 2,999.

**The name+DOB collision problem, solved with a human in the loop instead of a heuristic.** Two different real patients can share a first name, last name, and birth date — it's a genuine identity-resolution problem, not an edge case to shrug off. Rather than silently merging on that match (risky — could conflate two different people's charts) or silently creating a duplicate every time (defeats the point of matching at all), `/api/patients/lookup` surfaces a candidate match to the person entering data and lets *them* confirm whether it's the same patient. If they say no, `force_new: true` bypasses the match and creates a distinct record anyway. The match logic lives in the database; the judgment call about whether two candidate records are really the same human stays with a person, which is where it belongs.

**The dashboard is built around a visit, not a data table.** Early versions just listed rows. The current one asks: who is this patient, have we seen them before, what's their trend over time, what happened at this visit — the actual sequence of a clinical encounter. That's a sidebar-navigated, lookup-then-chart flow using `st.session_state` to carry the active patient across Streamlit's rerun-on-every-interaction execution model, with vitals pivoted into wide format for trend charts. It's a small thing, but it's the difference between "a script that shows data" and "a tool someone could plausibly open during their workday."

**A shared query is a shared contract.** `UPSERT_QUERY` in `shared/queries.py` is called from two places: the batch worker and the API's `/api/patients/ingest` endpoint. Adding an 11th column (`ingestion_run_id`) for the scheduled pipeline meant updating the batch worker's call site -- and it was easy to stop there, since that's the one actually being changed for the new feature. The API endpoint's `record_tuple` was still building 10 values, and CI caught it immediately: every request through that endpoint started failing with `asyncpg.exceptions._base.InterfaceError: the server expects 11 arguments for this query, 10 were passed`, because `test_ingest_valid_bundle` actually exercises that endpoint on every push. The fix was one line (pass `None`, since single-bundle API ingestion isn't a tagged scheduled run), but the lesson isn't about this one query -- it's that a query shared across call sites is a contract those call sites all depend on, and changing it means checking every place that calls it, not just the one you're actively working on.

**A measured decision to cap the dataset at 2,000 patients, not 10,000.** `SyntheaGeneratorNotebook.ipynb` documents the actual generation process behind the original historical batch -- it started at 10,000 synthetic patients, compressed into the same `.tar.gz` format the batch worker consumes. The compressed archive's actual storage footprint at that scale was the reason the batch was cut down to 2,000 instead: a deliberate call to keep a purposeful, storage-conscious dataset rather than a bloated one accumulated just because generation was cheap and easy. It's the same instinct that later shaped the scheduled ingestion pipeline's hard 3,000-patient cap below -- know your actual constraint (Supabase's free-tier storage, in both cases) and design to it, rather than letting a dataset grow just because nothing stopped it.

## The connection-pooling investigation

This is the part of the project I'd actually walk an interviewer through, because it has a real hypothesis, a controlled experiment, a corrected methodology, and raw output anyone can check for themselves in [`load-test-results/`](load-test-results/).

**The question:** the API's database pool defaulted to a single connection (`max_size=1`) as a Supabase free-tier accommodation. How much does that actually cost under concurrent load, and does routing through Supabase's managed PgBouncer-based transaction pooler (as opposed to just raising the pool size on a direct connection) help further?

**The method:** `POOL_MAX_SIZE` was made configurable via environment variable specifically so the pool size could change between runs without editing and reverting application code. Four Locust runs were captured, all at an identical load profile — 100 simulated users, ramped at 10/s, sustained for 60 seconds — hitting `GET /api/health` and `GET /api/patients`. Raw CSV exports for every run are committed in [`load-test-results/`](load-test-results/), not just summarized here.

| Configuration | Requests | Failures | Median | p99 | Throughput |
|---|---|---|---|---|---|
| No database (`/openapi.json` only — the ceiling) | 10,848 | 0 | 2 ms | 10 ms | 183.6 req/s |
| Direct connection, `max_size=1` | 226 | 0 | 22.0 s | 26.0 s | 3.8 req/s |
| Direct connection, `max_size=10` | 1,983 | 0 | 2.2 s | 3.0 s | 33.5 req/s |
| Supabase transaction pooler, `max_size=10` | 2,198 | 0 | 2.1 s | 2.3 s | 37.2 req/s |

**What it shows:** going from a single connection to ten dropped the median response time from 22.0 seconds to 2.2 seconds — exactly a 10x drop — and raised throughput about 8.8x (3.8 → 33.5 req/s). That's not a subtle effect — with one connection, every concurrent request queues behind whichever request currently holds it, and under sustained load that queue never drains; latency climbs for as long as the load is sustained rather than settling anywhere. Ten connections gave the system enough slack to reach a stable state instead of an ever-growing one.

Routing that same workload through Supabase's transaction-mode pooler (Supavisor, PgBouncer-compatible) — this time with the client-side pool size held constant at 10 on *both* sides, deliberately, to isolate the pooling mechanism as the only variable — improved throughput by about 11% (33.5 → 37.2 req/s), cut p99 latency by about 23% (3.0s → 2.3s), and improved median latency by a smaller ~5%. That's a real win on every axis, but a modest one, and it's most visible at the tail rather than the median — consistent with what a pooler is actually good at (absorbing bursty, uneven demand) rather than raising the ceiling for already-steady load.

Even the best-performing configuration here reaches only about 20% of the zero-database ceiling (37.2 of 183.6 req/s), which is itself the more durable finding across both versions of this experiment: a network round trip and a real query execution against Postgres is an irreducible cost that no amount of pool tuning eliminates — tuning only changes how gracefully the system degrades under concurrent load, not whether that cost exists at all. `statement_cache_size=0` (needed for asyncpg compatibility with transaction-mode pooling, since prepared statements are tied to a specific physical connection and PgBouncer can route different queries on the same logical connection to different physical backends) held up cleanly across every run — zero query errors in any configuration.

*A note on why this table looks different from an earlier version of this section: an earlier run compared `max_size=10` direct against `max_size=15` pooler -- an unequal comparison whose raw output was also never saved, so when the ~46% figure it produced got questioned, it couldn't be independently verified. This re-run fixes both problems: pool sizes matched at 10 on both sides, and every run's raw CSV/HTML output committed to [`load-test-results/`](load-test-results/) instead of discarded.*

## Hardening pass

Two gaps got fixed after the initial build:

**Transactional batch writes.** The batch worker's per-chunk writes (a patient row plus its observations and conditions) previously ran as three independent `executemany` calls. A crash between them could leave a patient written without its vitals. Each chunk is now wrapped in `async with conn.transaction():` — the three writes commit together or not at all.

**API key auth on the write endpoints.** The API had no authentication at all — anyone who could reach it could write data. `POST /api/patients/ingest` and `POST /api/patients/manual-entry` now require an `X-API-Key` header matching the `API_KEY` environment variable (checked via a FastAPI dependency, `require_api_key`, that fails closed if `API_KEY` isn't configured at all). Read endpoints stay open, since the dashboard consumes them without a login flow. This is a pragmatic middle ground for a portfolio project, not a production auth story — a real deployment would want per-user identity and scoped permissions, not one shared static key.

A second pass added three more things a real service needs and a demo doesn't, plus one bug a reviewer caught by actually reading the dashboard:

**Per-IP rate limiting on the write endpoints, on top of the API key.** `slowapi` caps `/api/patients/ingest` and `/api/patients/manual-entry` at 30 requests/minute per client IP. This is a different failure mode than the API key protects against — the key stops someone without credentials, the limiter stops a misbehaving or compromised *authorized* client from hammering the write path (a stuck retry loop, a bug in a script using a valid key). Defense in depth: two independent, narrower controls instead of one broad one.

**Request-id and timing middleware.** Every response now carries an `X-Request-ID` header, and every request is logged with its method, path, status code, and duration. Small addition, but it's exactly the tool you reach for the moment a load test (like the one below) raises "which request was slow?" instead of just "the p99 was slow" — the load-testing section already needed this in spirit; now the app actually has it.

**A real stats endpoint, replacing a page-size bug.** The dashboard's Home view originally computed "patients tracked" by fetching up to 100 patient rows and counting what came back — correct only as long as the table stays under 100 rows, and silently wrong the moment it doesn't. `GET /api/stats` runs the count and the BMI average as actual SQL aggregates (`COUNT(*)`, `AVG(bmi)`, one `COUNT(*) FILTER (...)` per BMI category) against the whole table, and the dashboard now calls that instead of estimating from a page of results. `GET /api/patients/recent-activity` is the same idea applied to a feed instead of a summary: the most recent observations across every patient, joined back to who they belong to, for the dashboard's new activity list.

## Making the dashboard read like clinical software, not a form

The first visual pass (custom CSS, badges, Plotly charts) made the dashboard look intentional instead of default-Streamlit, but it still didn't resemble software built for a clinical workflow. Closing that gap meant looking at two different references and being honest about which parts of each actually apply here:

Real EHR software (Epic, specifically, since that's what I had screenshots of) is dense because it represents an entire hospital's scheduling, billing, labs, and medication state at once — that density is earned by the amount of real state behind it, not a style choice to copy. This project tracks demographics and four vitals. Reproducing Epic's information density here would just mean empty-looking panels with nothing behind them. So instead of cloning the density, two specific, proportional patterns came over: **colored clinical alert banners** (`clinical_alerts()` flags an Obese BMI or an elevated/high blood pressure reading with an amber or red callout, instead of leaving the interpretation to whoever's reading a number), and **tabbed chart navigation** (an existing patient's chart is now `Overview` / `Vitals Trends` / `Record Visit` instead of one long scrolling page).

Separately, a friend's portfolio sites (marketing landing pages, built to sell a product in fifteen seconds) had one thing worth borrowing even though they're a different category of deliverable entirely: a confident first impression instead of dropping straight into a form. The new **Home view** is that pattern applied honestly — a one-line pitch, three live stat tiles pulled from the real database, and a three-card grid describing what the tool actually does — landing-page confidence backed by real data instead of a mockup.

The last addition is a **persistent patient identity rail**: once a patient is loaded, a compact card in the sidebar keeps showing who's active (name, age, DOB) no matter which tab or view you switch to. It's a small thing, but it's the difference between a patient's identity being a page you were just on versus context that's always visible — which is the whole point of the identity-collision safeguard elsewhere in this project actually mattering in the UI, not just in the API.

## Scheduled, unattended ingestion

Everything above runs on demand -- `batch_ingest.py` processes whatever archive you point it at. `.github/workflows/scheduled-ingestion.yml` puts that same worker on an actual passive schedule: a GitHub Actions cron job firing every 4 hours, with `workflow_dispatch` also enabled so a run can be triggered manually and verified before trusting it to the schedule.

Getting here meant running into a real dead end first. The obvious way to run Synthea (the Java tool this project's synthetic FHIR data comes from) cheaply and repeatably in CI would be a maintained Docker image -- there isn't one. The project's own Docker Hub image, and every community one I checked, are 8-10 years old and predate Synthea's Ruby-to-Java rewrite, and the current maintainers' wiki says outright there are no plans to containerize the Java version. What actually works doesn't need Docker at all: Synthea publishes a prebuilt, self-contained `synthea-with-dependencies.jar` directly on its GitHub Releases, and `ubuntu-latest` runners already ship with Java -- so the workflow downloads the jar and runs it directly, no build step, no image to maintain.

Each scheduled run:

1. `scripts/check_ingestion_cap.py` queries the real patient count in Postgres directly, since the API isn't deployed anywhere persistent that a scheduled job could reach. A small pure function, `compute_batch_size` (unit-tested for its boundary cases in `tests/test_ingestion_cap.py`, independent of any database), decides whether there's headroom left under a fixed cap and, if so, how many patients to generate -- a random draw between 100 and 200, clamped so a run can never push the total past the cap.
2. If there's headroom, Synthea generates that many synthetic patients, and `scripts/stage_synthea_batch.py` packages the FHIR output into an archive and uploads it to R2 -- filtering out Synthea's non-patient reference files (`hospitalInformation*`, `practitionerInformation*`) so they don't show up as noise in the dead-letter queue.
3. `worker/batch_ingest.py` ingests that archive exactly like any other, tagging every row it writes with `--ingestion-run-id` set to the GitHub Actions run ID -- so `SELECT ingestion_run_id, COUNT(*) FROM patients GROUP BY 1` shows exactly which rows came from which scheduled run, real lineage instead of one undifferentiated blob of data.

The cap exists because this runs against Supabase's free tier, which caps storage and compute -- not because of any limit on how much synthetic data Synthea itself can generate. Once the count check reports no headroom left, the workflow still fires on its 4-hour schedule but exits right after the cap check -- the intended steady state once the campaign completes, not a failure.

Verified with a real `workflow_dispatch` run against production, not just a syntax check -- Synthea generated a fresh batch, it landed in R2, and it upserted into Supabase with a real `ingestion_run_id` attached, all without anyone at a keyboard.

## Verifying extraction completeness, independently

Every upsert in this pipeline is defensive -- idempotent, transactional, dead-letter-queued on failure. But none of that protects against a different failure mode: `extract_clinical_data()` could return successfully while silently producing nothing for a resource it should have captured -- an unexpected LOINC code, a missing field, a bad date -- and every test in `tests/` would still pass, because they're written against the same assumptions the function itself makes. Idempotent writes protect the write stage; they say nothing about whether the right data reached that stage in the first place.

`scripts/validate_extraction_coverage.py` closes that gap with an independent second opinion. It re-reads a real archive's raw FHIR JSON on its own -- with its own duplicated copy of the classification logic rather than importing from `shared/extraction.py`, so a bug in the real extraction code can't "agree with itself" here -- and checks every in-scope Observation and Condition against what `extract_clinical_data()` actually returned for the same bundle. That includes recomputing the exact `uuid.uuid5` formula used to derive blood-pressure component IDs, so a dropped systolic or diastolic reading shows up as a real mismatch rather than an assumption that it worked. It also flags misattribution -- a row landing under the wrong patient's ID -- which the write-side idempotency logic would never catch on its own, since a misattributed row still upserts cleanly.

Run against the real, current 2,000-patient production archive:

| Check | Result |
|---|---|
| In-scope Observations (height, weight, blood pressure) | 79,666 / 79,666 captured (100%) |
| Conditions | 70,817 / 70,817 captured (100%) |
| Misattributed rows | 0 |
| Distinct out-of-scope LOINC codes seen | 234, across 912,307 resources -- real clinical data (medications, immunizations, procedures, and similar) this pipeline was never built to parse |

A clean result doesn't make this check unnecessary -- it means the assumption every other test already relies on (that `extract_clinical_data()` handles the archive's actual real-world shape correctly, not just its fixtures) is now independently confirmed against real data instead of taken on faith. Full report and per-code breakdown: [`extraction-coverage-results/`](extraction-coverage-results/).

## Known limitations / what I'd do next

Running multiple API instances behind a load balancer (`uvicorn --workers N` as the simplest local approximation) would multiply the number of database connections by however many workers are running, since each worker builds its own independent pool in its own `lifespan`. That's worth accounting for explicitly before scaling horizontally against a connection-limited managed Postgres instance — it's exactly the kind of problem a shared external pooler (like the one tested above) is meant to solve once there's more than one application process competing for the same database.

The API key is a single shared secret, not per-user auth — fine for a demo, not how this would work with more than one real user.

The rate limiter keys on `request.client.host`, which is the direct TCP peer. Behind a reverse proxy or load balancer in a real deployment, that would be the proxy's IP for every request unless `X-Forwarded-For` is parsed and trusted correctly — a genuinely tricky thing to get right securely, and out of scope here since this project isn't deployed behind one.

The dashboard's patient search is exact-match on name and birth date, by design — that precision is what makes the identity-collision safeguard trustworthy (a fuzzy match would need its own confidence-scoring and confirmation UX, which is a bigger feature than this project needs to make its point). A typo in either field currently just looks like "no match found" rather than a near-miss worth surfacing, which would be the next thing to improve if this went further.
