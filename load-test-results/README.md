# Load-test raw results

Raw, unedited output from the connection-pooling investigation described in
the main [README](../README.md#the-connection-pooling-investigation). Each
run is a separate Locust CSV export (`_stats.csv`, `_stats_history.csv`,
`_failures.csv`, `_exceptions.csv`) -- the `Aggregated` row in each `_stats.csv`
is the one actually cited in the main README's table.

All four runs used the same load profile: 100 simulated users, ramped at
10/second, sustained for 60 seconds, against a local `uvicorn` instance
(never against a deployed endpoint).

| Prefix | Configuration |
|---|---|
| `01-no-db-baseline` | No database at all -- `GET /openapi.json` only. The ceiling: what the server/network can do with zero database involvement. |
| `02-direct-pool1` | Direct Supabase connection, `POOL_MAX_SIZE=1`. |
| `03-direct-pool10` | Direct Supabase connection, `POOL_MAX_SIZE=10`. |
| `04-pooler-pool10` | Supabase transaction pooler (Supavisor/PgBouncer), `POOL_MAX_SIZE=10` -- matched to config 3's pool size deliberately, so this comparison isolates direct-vs-pooler as the only variable. |

To reproduce: see the exact commands in the main README's connection-pooling
section, or ask for the runbook again -- each run restarts `uvicorn` with a
different `DATABASE_URL`/`POOL_MAX_SIZE` combination, then points Locust at
it in headless mode with `--csv=load-test-results/<prefix>`.
