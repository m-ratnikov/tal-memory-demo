# Deploying the demo (free, on the Wisery VPS)

This app is a **persistent** web service (the migration wizard runs background
jobs and the page polls for progress), so it runs as a long-lived container.
The free, always-on home is the existing Caddy + Docker VPS
(`wisery@103.6.168.161`): two containers behind Caddy at
**https://demo.michaelratnikov.com**, gated by a dedicated `DEMO_PASSWORD`.

## Prerequisites (yours)
1. **DNS**: add an A record `demo.michaelratnikov.com -> 103.6.168.161` where michaelratnikov.com's DNS is managed. The apex runs on Vercel, but this subdomain points straight at the VPS (DNS-only, not proxied) - that is what Caddy needs to issue the TLS cert.
2. **A dedicated, spend-capped `OPENAI_API_KEY`** - not your local key.
3. **A `DEMO_PASSWORD`** to share with the client (username is ignored).

## 1. Get the code onto the VPS
Private repo, so rsync from your machine (no GitHub auth needed on the VPS):
```bash
# from the local repo (D:\__softwisery\wisery\tal-memory-demo):
rsync -az --delete \
  --exclude .venv --exclude __pycache__ --exclude .env \
  ./ wisery@103.6.168.161:~/tal-memory-demo/
```

## 2. Set the deploy secrets on the VPS
```bash
ssh wisery@103.6.168.161
cd ~/tal-memory-demo
cat > .env <<'EOF'
OPENAI_API_KEY=sk-your-dedicated-capped-key
DEMO_PASSWORD=choose-a-demo-password
OPENAI_MODEL=gpt-4o-mini
RECONCILE_JUDGE_MODEL=gpt-4o
EOF
```

## 3. Bring it up
```bash
docker compose -f docker-compose.prod.yml up -d --build
```
`schema.sql` auto-applies on the DB's first init: tables, RLS policies, the
`tal_app` role, and the Alice/Bob seed. The app comes up on host port 8010.

## 4. Seed the memory (one-off, a few cents)
```bash
docker compose -f docker-compose.prod.yml exec tal-app \
  uv run python -c "from app.migration import run_migration; from app.ingestion import ingest_conversations; print(run_migration()); print(ingest_conversations())"
```
Leaves the store at the clean 2-student baseline (Alice + Bob). The wizard's
wave-2 import and its Reset button work from there.

## 5. Route Caddy to it
Append `deploy/caddy-tal.conf` to the VPS Caddyfile (wherever the vps compose
mounts it, e.g. `~/vps/Caddyfile`), then reload Caddy gracefully:
```bash
cat ~/tal-memory-demo/deploy/caddy-tal.conf >> ~/vps/Caddyfile
docker compose -f ~/vps/docker-compose.yml exec caddy caddy reload --config /etc/caddy/Caddyfile
```
(A bad edit fails the reload and leaves the running config untouched - existing
sites keep serving.)

## 6. Verify
- `https://demo.michaelratnikov.com/health` -> `{"status":"ok"}` (no auth).
- The site prompts for the password, then `/`, `/meet`, `/vision`, `/architecture`,
  `/wizard` render; `/meet` shows Bob's then/now; an "ask" returns an answer.

## Updating later
Re-run the `rsync` (step 1), then:
```bash
docker compose -f docker-compose.prod.yml up -d --build
```
The DB volume persists, so students/facts survive a redeploy. To wipe and
re-seed: `docker compose -f docker-compose.prod.yml down -v` then repeat steps 3-4.

## Alternative (paid): Render
`render.yaml` describes a Render Blueprint (web service + managed Postgres).
Render now requires a card, so the VPS is the free path; the Render config is
kept for a future paid option.
