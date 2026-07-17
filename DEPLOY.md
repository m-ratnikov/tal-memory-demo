# Deploying the demo (Render + Postgres/pgvector)

This app is a **persistent** web service (the migration wizard runs background
jobs and the page polls for progress), so it runs as a long-lived container -
not serverless. Render (Docker) + a managed Postgres with pgvector is the fit.

## 1. Provision from the blueprint
Push this repo to GitHub, then in Render: **New > Blueprint** and point it at the
repo. `render.yaml` provisions:
- a Docker **web service** (`tal-memory-demo`) with a health check on `/health`, and
- a managed **Postgres** (`tal-db`).

`DSN_OWNER` is wired automatically from the database's connection string.

## 2. Enable pgvector + create the schema
Open the database's PSQL console in Render (or `psql "$DSN_OWNER"`) and run:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
Then apply the schema (creates tables, RLS policies, the `tal_app` role, and the
Alice/Bob seed):
```bash
psql "$DSN_OWNER" -f schema.sql
```
If the DB user lacks `CREATEROLE`, `schema.sql`'s `CREATE ROLE tal_app` will fail -
create the role first as the owner, then re-run:
```sql
CREATE ROLE tal_app LOGIN PASSWORD 'choose-a-strong-password';
```

## 3. Wire the app (RLS) role
Set `DSN_APP` on the web service to the `tal_app` connection string - same host,
db, and port as `DSN_OWNER`, but `tal_app` and its password:
```
DSN_APP=postgresql://tal_app:<password>@<host>:<port>/<db>
```
(Direct connection is fine at demo scale; `SET LOCAL app.student_id` is correct on
it. Add a transaction-mode pooler only if concurrency grows.)

## 4. Set the secrets (dashboard, never in git)
- `OPENAI_API_KEY` - a **dedicated, spend-capped** key. Not your local key.
- `DEMO_PASSWORD` - the shared password that gates the public URL (username is
  ignored). Leave unset to serve open.
- Optional `LANGSMITH_*` - leave off in production unless self-hosted (traces
  carry student psychological facts).

## 5. Seed the memory (one-off, a few cents of OpenAI)
The schema seeds Alice and Bob's raw reports/conversations; distill them into
facts once. From a Render **Shell** on the web service (or locally against the
prod DSNs):
```bash
uv run python -c "from app.migration import run_migration; from app.ingestion import ingest_conversations; print(run_migration()); print(ingest_conversations())"
```
This leaves the store at the clean 2-student baseline (Alice + Bob). The wizard's
wave-2 import and its **Reset** button work from there; nothing else to seed.

## 6. Verify
- `/health` returns `{"status":"ok"}` (no auth).
- The URL prompts for the password, then `/`, `/meet`, `/vision`, `/architecture`,
  `/wizard` all render; `/meet` shows Bob's then/now; an "ask" returns an answer.

## Notes
- **Static**: `mermaid.min.js` (~3.3 MB) ships in the image and is served by the
  app - fine on a persistent server.
- **Cost control**: the gate keeps randoms off the key; the spend cap is the hard
  backstop. For a fully free public link, pre-compute the `/meet` + `/chat`
  answers instead of calling OpenAI live.
- **Free tiers**: Render's free web service spins down when idle (slow first
  click) and free Postgres expires after ~30 days. Use `starter` for a demo that
  must stay warm across weeks.
