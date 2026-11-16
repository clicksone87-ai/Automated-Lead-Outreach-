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

## How it works (one lead)

```
Discovery (sources + proactive triggers, scheduler)        → leads table (+ evidence)
  → Requirements   intent score, brief, language/country/time zone, duplicate & cooldown gates
  → Contact        email on their own site / social bio / platform reply  (no route = stop, $0 spent)
  → Audit          PageSpeed lab data, Playwright screenshots, axe, technical SEO, Gemini vision
  → Branding       brand extraction + 3 SVG logo concepts (sanitised, PNG renders, similarity check)
  → Website        Next.js template filled from facts → static preview (Caddy, private) + tests
  → Quality check  logos / preview / evidence links; failures go back to the producer once
  → Proposal       evidence-mapped claims, React Email (language, identity footer, unsubscribe)
  → [you approve in the Approval queue — Stage 1]
  → Outreach       policy gate → SMTP → Mailpit (testing) · follow-ups · replies → Inbox
  → Learning loop  weekly report, guarded weight adjustments (logged)
```

Every step is recorded in `agent_runs`; a failed step resumes without redoing completed steps.
Every policy decision (suppression, country rules, limits, QA gate, budget/free caps…) is in
`policy_decisions` with its reason.

## Testing mode — what to expect

* **Sources**: Serper (Google results via API), Exa (fallback), Apify social sources (X,
  Instagram, Facebook public pages/groups, Reddit), Companies House, freelance boards/RSS,
  proactive triggers. Paid ones show "Off in testing mode".
* **Free caps** (Sources page → Free-tier usage): Gemini 500 requests/day, Serper 2,500
  credits, Exa $10 credit, Apify $5/month split per source, PageSpeed 400/day. When a cap is
  reached that work pauses until the cap resets — nothing paid ever runs (budget $0).
* **Email**: every email (first message and follow-ups) goes to **Mailpit**
  (http://localhost:8025). To test a reply, send an email to `replies@test-agency.local`
  through Mailpit's SMTP (`localhost:1025`) with `In-Reply-To` set to the message's
  Message-ID, or simply reply from Mailpit's UI ("Release" is not needed). The scheduler reads
  the reply inbox every 5 minutes; positive replies appear in the dashboard Inbox.
* **Sending windows**: real sending waits for the recipient's business hours; the sandbox
  delivers immediately (Settings → `sandbox_respect_send_window` to test the windows).
* **Country rules**: US/GB are enabled for drafting; real sending stays blocked until a legal
  review sets `legal_reviewed: true` in `backend/app/policy/country_rules.yaml`. The sandbox
  skips only that condition — recipient-type and consent rules still apply.

## Daily operation

1. Dashboard → **Approval queue**: read each email next to its claims → evidence; approve,
   edit the wording, or reject (keys j/k/a/e/r).
2. **Inbox**: answer positive replies yourself; track quote → contract → payment → delivery.
3. **Sources**: watch free-tier usage and any "Degraded" source; **Overview → System health**
   shows worker/scheduler heartbeats and agent errors.
4. **Weekly report**: what produced replies; the learning loop's adjustments (needs ≥ 20 real
   sends per source/template before it moves anything).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says "API unreachable" | `docker compose ps`; `docker compose logs api --tail 50` |
| Ports not answering after Docker Desktop restarted | `docker compose up -d --force-recreate` |
| A source shows "Degraded" | provider returned nothing / failed; the pipeline keeps running. Details in the source card |
| Leads stuck "Paused (free cap / budget)" | a free cap was reached; it resets daily/monthly (Sources page) |
| Gemini "high demand" (503) | retried with backoff, then the next model, then the orchestrator retries later |
| Website build slow | builds run one at a time in the worker (2 GB); ~5–7 min on an 8 GB laptop |
| Disk filling up | `docker builder prune --keep-storage 3GB`; Docker's disk image lives in `C:\DockerDesktopWSL` |

## Go-live checklist — status on 2026-11-17

| Item | Status |
|---|---|
| Legal review of US + EU outreach rules, `country_rules.yaml` filled | ⛔ open — sending stays sandboxed until done |
| Separate sending domain, SPF/DKIM/DMARC, warm-up schedule | ⛔ open (warm-up schedule is configured in Settings) |
| API keys | ✅ free tier (Gemini, Serper, Exa, Apify, Companies House, PageSpeed) · paid ones open (Resend, ZeroBounce, OpenAI optional) |
| Budget cap ≤ $120/month | ✅ enforced in code (currently $0 = free tier only) |
| One industry selected; languages enabled | ✅ home services; EN sending (DE/FR/ES templates ready) |
| Sender identity | ⚠️ placeholder values ("Test Agency") — set real ones in `.env` |
| Autonomy at Stage 1 | ✅ |

To go live: set `APP_TIER=paid`, `EMAIL_PROVIDER=resend` + `RESEND_API_KEY` + `SENDING_DOMAIN`,
a reply mailbox (`REPLY_IMAP_*`), real sender identity, `APP_SECRET`, and mark countries
`legal_reviewed: true` after the review. Nothing else changes in code.

## Moving to one Linux server (4 vCPU / 8 GB)

Same `docker-compose.yml`, different `.env`: set `APP_MODE=server`, `API_TOKEN` (and the same
value as `NEXT_PUBLIC_API_TOKEN` build arg for the dashboard), `APP_SECRET`,
`PUBLIC_API_BASE_URL` (unsubscribe links + webhooks) and `PUBLIC_PREVIEW_BASE_URL` (preview
links instead of screenshots). Put Caddy in front with real TLS, keep Postgres/Redis/Adminer
bound to 127.0.0.1 (Adminer can be removed in production), restore the latest backup with the
steps above, run `make migrate`, then `make up`.
