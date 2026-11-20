# Runbook

How to set up, run, configure, test, back up and deploy the Leads System. The overview of
what the system does is in the [README](../README.md).

## Repository layout

```
backend/            FastAPI app, agents, orchestrator, policy layer, Alembic migrations, tests
dashboard/          Next.js dashboard
site-templates/     website templates (compiled once, rendered per lead to static HTML)
email-templates/    React Email templates and the render script
caddy/              Caddyfile for private previews
docker-compose.yml  the full local stack
```

## Setup

Requirements: Docker Desktop (or Docker Engine with Compose), Git, and for running tests on
the host, Python 3.11+ and Node 22. `make` is optional; on Windows `.\make.ps1 <target>`
provides the same targets.

```bash
cp .env.example .env      # then fill in the API keys (see Configuration)
make up                   # build and start the stack
make seed                 # runtime settings, pricing packages and source rows
make check-providers      # one live call per configured provider
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API and OpenAPI docs | http://localhost:8000/api/docs |
| Mailpit (all outgoing email) | http://localhost:8025 |
| Adminer (server `postgres`, user/password/db `leads`) | http://localhost:8081 |
| Website previews | `http://<preview_id>.preview.localhost:8080` |

All ports are bound to 127.0.0.1.

## Running

| Command | Description |
|---|---|
| `make up` / `make down` / `make logs` | start (rebuilds images), stop, follow logs |
| `make run-cycle` | run every enabled source once, then every waiting lead |
| `docker compose exec api python -m app.cli run-source <key>` | run one discovery source |
| `docker compose exec api python -m app.cli run-lead <lead_id>` | run or resume one lead |
| `docker compose exec api python -m app.cli run-pending` | resume every lead waiting mid-pipeline |
| `make check-providers` | live provider check (secrets are never printed) |
| `make backup` | database backup now (the scheduler also runs one daily) |
| `make shell` | shell in the API container |

The scheduler runs discovery, sending, reply ingestion, delivery tracking, follow-ups, the
weekly report and backups on its own.

### Lanes

Every lead goes through the **fast lane**: a rule-based prefilter (no LLM), batch scoring
(up to 20 leads per Gemini call), contact finding, a light audit (static checks and cached
PageSpeed, no browser) and a text-only draft: an email for a verified address, otherwise
**Track B** manual drafts (a public reply, a DM and a contact-form message that a person
posts by hand; nothing is ever posted automatically). Leads that hit a free-tier cap or a
transient error wait for the sweep (every 5 minutes) instead of retrying in place.

The **heavy lane** adds logo sets (wordmark, mark, reversed, mono, animated), a website preview
in three designs and the full audit (screenshots, vision review, competitors, PDF), then
redrafts the proposal with those attachments. High-intent leads (75+) go there as soon as their
fast lane ends, and their fast draft waits for a person instead of auto-sending; the scheduler
fills the rest of the day's slots every 15 minutes (`heavy_lane_daily_limit`, default 10;
verified email first, then intent). **Generate assets** on a lead sends it there directly. No
picks are made while Gemini's own daily limit is used up (it resets at midnight Pacific).

The `worker` container runs the fast queue (`arq:queue`, several jobs side by side) and the
discovery queue (`arq:discovery`, source runs) in one process; the `worker-heavy` container runs
the heavy queue (`arq:heavy`, one job at a time), so a heavy job that runs out of memory never
stops the fast lane. Gemini's daily cap is split per lane (scoring, fast, heavy, other) so logos
can never starve scoring. The Overview's **Speed** panel shows median and p90 time per lane and
per step against the targets (fast < 3 min, heavy < 10 min).

Manual **Run now** on a source that spends a limited free tier (Apify, Serper) asks for
confirmation, is limited to one run per source per hour, and is refused when the free cap
cannot cover the run. Serper credits are one-time, so a daily ceiling
(`SERPER_DAILY_CAP`, default 60) spreads them out.

Day to day, the dashboard is the main interface:

1. **Approvals**: read each email next to its claims and evidence, then approve, edit or
   reject it (keyboard: j/k/a/e/r).
2. **Inbox**: positive replies, and quote, contract, payment and delivery status per deal.
3. **Sources**: free-tier usage and source health. **Overview** shows worker and scheduler
   heartbeats and agent errors.
4. **Reports**: weekly results by source, service, country, trigger and template.

To test replies locally, reply to a message from the Mailpit UI or send an email to
`replies@test-agency.local` through Mailpit's SMTP port (`localhost:1025`) with `In-Reply-To`
set to the original Message-ID. The scheduler reads the reply inbox every five minutes.

## Configuration

All configuration lives in `.env`. Every variable maps 1:1 to a field in
`backend/app/config.py`, and `.env.example` lists all of them (a test checks that they stay in
sync). Runtime settings such as limits, budgets, languages and the autonomy stage are seeded
from the environment into the `settings` table and edited in the dashboard afterwards.

Main switches:

| Variable | Default | Meaning |
|---|---|---|
| `APP_TIER` | `free` | `free` uses free-tier providers only and forces paid integrations off |
| `LLM_PROVIDER` | `gemini` | `gemini` or `openai` |
| `SEARCH_PROVIDER` | `serper` | `serper` (falls back to Exa), `exa` or `brave` |
| `EMAIL_PROVIDER` | `smtp` | `smtp` (Mailpit locally) or `resend` |
| `EMAIL_VERIFICATION_PROVIDER` | `local` | `local` (syntax + MX) or `zerobounce` |
| `MONTHLY_BUDGET_USD` / `DAILY_BUDGET_USD` | `0` | paid spend caps; `0` means no paid call can run |

Free-tier keys: `GEMINI_API_KEY`, `SERPER_API_KEY`, `EXA_API_KEY`, `APIFY_API_TOKEN`,
`COMPANIES_HOUSE_API_KEY`, `GOOGLE_PAGESPEED_API_KEY`. A missing key turns that integration
off and the Sources page shows "Needs API key"; the system keeps running without it.

Free-tier usage is tracked against each provider's own limit (by default Gemini 500
requests/day, Serper 2,500 credits, Exa $10 credit, Apify $5/month split per source, PageSpeed
400/day). When a limit is reached, that work pauses until it resets.

Outreach rules per country live in `backend/app/policy/country_rules.yaml`. Real sending stays
blocked for a country until `legal_reviewed: true` is set after a legal review; the Mailpit
sandbox skips only that check. Pricing packages are seeded by `make seed`
(`backend/app/services/seed.py`) and shown in Settings.

## Testing

```bash
make test                 # backend and dashboard tests inside the containers
make lint                 # ruff, mypy and eslint/tsc
```

On the host:

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg://leads:leads@127.0.0.1:5432/leads_test .venv/bin/pytest -q
cd ../dashboard && npm run lint && npm test
```

Tests are hermetic: they never read `.env`, never call live APIs, and use recorded fixtures
and mock adapters. Database tests run against the `leads_test` database and are skipped when
no database is reachable. One end-to-end test runs a lead through every agent.

## Deployment

### Backups and restore

Backups are written by `pg_dump` to `./backups/leads-YYYYMMDD-HHMMSS.sql.gz`; the newest 14
are kept. To restore, use the API container's `psql` (it matches the `pg_dump` version):

```bash
docker compose stop api worker scheduler
docker compose exec postgres psql -U leads -d postgres \
  -c "DROP DATABASE leads WITH (FORCE);" -c "CREATE DATABASE leads;"
gunzip -c backups/<file>.sql.gz | docker compose run --rm -T api \
  psql "postgresql://leads:leads@postgres:5432/leads" -v ON_ERROR_STOP=1
docker compose up -d
```

To check a backup without touching live data, restore it into a scratch database instead.

### Going live

1. Complete the legal review and set `legal_reviewed: true` for each country in
   `country_rules.yaml`.
2. Set up a separate sending domain with SPF, DKIM and DMARC. A warm-up schedule is
   configured in Settings.
3. Set `APP_TIER=paid`, `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, `SENDING_DOMAIN` and the
   reply mailbox (`REPLY_IMAP_*`).
4. Set the real sender identity (`SENDER_*`) and a long random `APP_SECRET`.
5. Optionally enable paid email verification (`EMAIL_VERIFICATION_PROVIDER=zerobounce`).

No code changes are needed. Autonomy starts at stage 1, where every email needs approval.

### Single Linux server

Use the same `docker-compose.yml` with a different `.env`: set `APP_MODE=server`, `API_TOKEN`
(and the same value as the dashboard's `NEXT_PUBLIC_API_TOKEN` build argument), `APP_SECRET`,
`PUBLIC_API_BASE_URL` for unsubscribe links and `PUBLIC_PREVIEW_BASE_URL` for preview links.
Put Caddy in front with TLS, keep Postgres, Redis and Adminer bound to 127.0.0.1 (or remove
Adminer), restore the latest backup, then run `make migrate` and `make up`. The stack needs
about 4.5 GB of memory; 4 vCPU / 8 GB is enough.

## Development

- Backend: Python 3.11+, type hints everywhere, `ruff` and `mypy --strict` clean. Agents read
  their input from the database and write structured results back; every agent must be
  idempotent.
- Every external fetch goes through the safe fetcher (`app/adapters/fetcher.py`), every LLM
  call through the LLM adapter, and every paid call through `orchestrator.budget.charge()`.
- New environment variables go into `config.py` and `.env.example` together.
- Schema changes need an Alembic migration (`alembic revision --autogenerate -m "..."` in the
  API container).
- Dashboard: TypeScript strict, eslint with zero warnings. After API changes, refresh the
  schema with `curl -s http://localhost:8000/api/openapi.json > dashboard/openapi.json` and
  regenerate the typed client with `npm run gen:api`.
- Install the pre-commit hooks with `pip install pre-commit && pre-commit install`.
- Before opening a pull request, run `make lint` and `make test`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard shows "API unreachable" | `docker compose ps` and `docker compose logs api --tail 50` |
| Ports stop answering after Docker restarts | `docker compose up -d --force-recreate` |
| A source shows "Degraded" | the provider failed or returned nothing; details are on the source card |
| Leads paused on a free-tier or budget cap | the cap resets daily or monthly (see Sources) |
| The first preview after a template change is slow | the template is compiled once per version (npm install + tsc + CSS); later previews render in about a second |
| Docker disk filling up | `docker builder prune --keep-storage 3GB` |
