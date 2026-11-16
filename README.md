# AI Agency Automation System — local prototype

Finds US/UK businesses that need a logo, website, redesign or SEO, qualifies them, finds a
verified contact route, audits their site, drafts logo concepts and a website preview, writes a
personalised email and (after your approval) "sends" it.

> **Testing mode — free tier.** `APP_TIER=free` runs only on live free-tier APIs (Gemini,
> Serper → Exa, Apify, Companies House, PageSpeed). Paid integrations are off, the paid budget
> is $0, and **every email goes to Mailpit — nothing is really sent.** There is no mock data in
> the running system; mocks exist only inside the automated test suite.

## Quick start (Windows)

Prerequisites: Docker Desktop (WSL2), Git, Node 22, Python 3.12+ (only for running tests on
the host). `make` is optional: `.\make.ps1 <target>` mirrors every Makefile target.

```powershell
copy .env.example .env        # then fill in the keys (see "Keys" below)
.\make.ps1 up                 # build + start the stack
.\make.ps1 seed               # settings, pricing packages, sources (no fake data)
.\make.ps1 check-providers    # one real call per provider → table of results
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API (OpenAPI docs) | http://localhost:8000/docs |
| Mailpit (every outgoing email lands here) | http://localhost:8025 |
| Adminer (database browser; server `postgres`, user/password/db `leads`) | http://localhost:8081 |
| Website previews | http://&lt;lead_id&gt;.preview.localhost:8080 |

All ports are bound to 127.0.0.1 only.

## Everyday commands

| Command | What it does |
|---|---|
| `.\make.ps1 up` / `down` / `logs` | start (rebuilds images) / stop / follow logs |
| `.\make.ps1 run-cycle` | one full cycle now: every enabled source, then every waiting lead |
| `docker compose exec api python -m app.cli run-source <key>` | run one discovery source once |
| `docker compose exec api python -m app.cli run-lead <lead_id>` | run / resume one lead's pipeline |
| `docker compose exec api python -m app.cli run-pending` | resume every lead waiting mid-pipeline |
| `.\make.ps1 check-providers` | live provider check (no secrets printed) |
| `.\make.ps1 test` / `lint` | backend + dashboard tests / ruff + mypy + eslint |
| `.\make.ps1 backup` | `pg_dump` now (also runs daily from the scheduler) |
| `.\make.ps1 purge-demo` | delete anything a test fixture ever produced (should report 0) |

## Keys

Every variable in `.env` maps 1:1 to a field in `backend/app/config.py` (`.env.example` is
the reference, verified by a test). Free-tier keys used in testing mode: `GEMINI_API_KEY`,
`SERPER_API_KEY`, `EXA_API_KEY`, `APIFY_API_TOKEN`, `COMPANIES_HOUSE_API_KEY`,
`GOOGLE_PAGESPEED_API_KEY`. A missing key turns that integration "Off · needs API key" on the
Sources page; the system never falls back to fake data. Never commit `.env` (it is gitignored).

## Backups and restore

The scheduler runs `pg_dump` daily (and `.\make.ps1 backup` runs it on demand) into
`./backups/leads-YYYYMMDD-HHMMSS.sql.gz`; the newest 14 are kept.

Restore (verified on 2026-11-16 into a scratch database). Use the api container's `psql`,
which matches the `pg_dump` version that wrote the file:

```powershell
# 1. stop the writers
docker compose stop api worker scheduler
# 2. recreate the database
docker compose exec postgres psql -U leads -d postgres -c "DROP DATABASE leads WITH (FORCE);" -c "CREATE DATABASE leads;"
# 3. load the dump (Git Bash / WSL syntax shown; pick the file you want)
gunzip -c backups/leads-20261116-172922.sql.gz | docker compose run --rm -T api psql "postgresql://leads:leads@postgres:5432/leads" -v ON_ERROR_STOP=1
# 4. start again (migrations run on api start; a restored dump is already at its revision)
docker compose up -d
```

To test a backup without touching live data, restore it into a scratch database instead
(`CREATE DATABASE leads_restore_check;` … then drop it).

## Tests

```powershell
.\make.ps1 test              # inside the containers (uses the leads_test database)
# or on the host:
cd backend; $env:TEST_DATABASE_URL="postgresql+psycopg://leads:leads@127.0.0.1:5432/leads_test"; .venv\Scripts\pytest -q
```

Tests are hermetic: they never read `.env`, never make live calls, and use recorded fixtures
and mock adapters (`APP_MODE=test` only).
