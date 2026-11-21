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
| Preview-only port for a tunnel (static previews only) | `http://127.0.0.1:8090/p/<preview_id>/` |

All ports are bound to 127.0.0.1.

## Running

| Command | Description |
|---|---|
| `make up` / `make down` / `make logs` | start (rebuilds images), stop, follow logs |
| `make run-cycle` | run every enabled source once, then every waiting lead |
| `docker compose exec api python -m app.cli run-source <key>` | run one discovery source |
| `docker compose exec api python -m app.cli run-lead <lead_id>` | run or resume one lead |
| `docker compose exec api python -m app.cli run-pending` | resume every lead waiting mid-pipeline |
| `make clean-leads` (`python -m app.cli clean-leads [--dry-run]`) | move social posts that were older than 8 h when found (or undated) and freelance-platform leads to "Not useful"; idempotent; leads already drafted, sent or answered are kept |
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
| `EXCLUDED_COUNTRIES` | `PK,IN,BD` | markets never searched, stored or contacted |
| `ALL_COUNTRIES_OUTREACH_ENABLED` | `true` | every other country is a market; `false` limits it to the target countries in Settings |
| `SOCIAL_MAX_POST_AGE_HOURS` | `8` | every social post must carry its time and be at most this old when stored; undated posts are dropped |
| `LLM_PROVIDER` / `LLM_FALLBACK_PROVIDER` | `gemini` / `groq` | Groq takes over text work when Gemini is capped or down (`GROQ_MODEL`, default `openai/gpt-oss-120b`) |
| `SEARCH_PROVIDER_ORDER` | `serper,serpapi,scrapingdog,searlo,tavily,exa` | search router order; each provider has its own `*_CAP` |
| `SOCIAL_SOURCES_ENABLED` / `SOCIAL_PLATFORMS` | `true` / all | social providers and platforms (LinkedIn only through the providers' LinkedIn endpoints) |
| `SOCIAL_PROVIDER_DAILY_CAP` | `50` | credits per social provider per day (1 per search; SocialCrawl LinkedIn 5) |
| `OUTREACH_EMAIL_MODE` | `gmail` | `gmail` (drafts or compose links from `GMAIL_SENDER`) or `provider` (`EMAIL_PROVIDER`: Mailpit / Resend) |
| `GMAIL_APP_PASSWORD` / `GMAIL_DELIVERY` | empty / `draft` | with an App Password: `draft` = IMAP draft with attachments, `send` = SMTP (587 STARTTLS, falls back to 465 SSL); without: compose links |
| `PUBLIC_PREVIEW_BASE_URL` | empty | tunnel hostname of the preview-only port; empty = screenshots only, no preview link |
| `PUBLIC_API_BASE_URL` | empty | public API base for one-click unsubscribe; empty = mailto unsubscribe only |
| `EMAIL_VERIFICATION_PROVIDER` | `zerobounce` | ZeroBounce within `ZEROBOUNCE_MONTHLY_CAP`, local syntax + MX check as fallback |
| `MONTHLY_BUDGET_USD` / `DAILY_BUDGET_USD` | `0` | paid spend caps; `0` means no paid call can run |

Free-tier keys: `GEMINI_API_KEY`, `GROQ_API_KEY`, `SERPER_API_KEY` (+ `_FALLBACK`),
`SERPAPI_API_KEY`, `SCRAPINGDOG_API_KEY`, `SEARLO_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`,
`APIFY_API_TOKEN` (Facebook and Instagram only), `ENSEMBLEDATA_API_TOKEN`, `SCRAPECREATORS_API_KEY`,
`SOCIALCRAWL_API_KEY`, `HIKERAPI_ACCESS_KEY`, `COMPANIES_HOUSE_API_KEY`,
`GOOGLE_PAGESPEED_API_KEY`, `EMAIL_VERIFICATION_API_KEY`. Open data (Overpass, FR
recherche-entreprises, NO brreg) needs no key. A missing key turns that integration
off and the Sources page shows "Needs API key"; the system keeps running without it.

Free-tier usage is tracked against each provider's own limit (by default Gemini 500
requests/day, Serper 2,500 credits, Exa $10 credit, Apify $5/month split between Facebook and Instagram, PageSpeed
400/day). When a limit is reached, that work pauses until it resets.

Outreach rules per country live in `backend/app/policy/country_rules.yaml`. Every listed
country and, through the file's `default` entry, every unlisted one may be drafted and sent to,
except `EXCLUDED_COUNTRIES`. `sending_enabled: false` switches sending off for one country and
`requires_prior_consent: true` blocks cold email; `legal_reviewed` only records whether a lawyer
has confirmed the entry. Free-tier caps and live usage per provider are on the Sources page, and
`docs/API_AND_ENDPOINTS.md` lists every external service. Pricing packages are seeded by `make seed`
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

### Public preview links (Cloudflare Tunnel)

Recipients cannot open `localhost` links, so by default every proposal attaches the preview
screenshots and PDF and contains no preview link. To add live links without a server, expose
the preview-only Caddy port through a Cloudflare Tunnel.

Port `8090` (bound to 127.0.0.1 like every other port) serves only the static preview files at
`/p/<preview_id>/` from `./data/previews/<preview_id>/site`, read-only. It has no
`reverse_proxy`, so the API, the dashboard, Mailpit and Adminer cannot be reached through it.
There are no directory listings, dotfiles are hidden, only GET and HEAD are allowed, every
response is `noindex` with security headers, and anything else returns 404.

1. Start a tunnel to that port only:

   ```bash
   # quick tunnel, prints a random https://<name>.trycloudflare.com hostname
   cloudflared tunnel --url http://127.0.0.1:8090
   # or a named tunnel on your own hostname
   cloudflared tunnel create leads-previews
   cloudflared tunnel route dns leads-previews previews.example.com
   cloudflared tunnel run --url http://127.0.0.1:8090 leads-previews
   ```

2. Set `PUBLIC_PREVIEW_BASE_URL=https://<tunnel hostname>` in `.env` and restart `api`,
   `worker` and `scheduler`. New proposals then link to `<PUBLIC_PREVIEW_BASE_URL>/p/<id>/`;
   the screenshot is still attached. A `localhost` value is ignored.
3. Check that only previews are reachable (locally and again through the tunnel hostname):

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/health        # 404
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/gmail/outbox  # 404
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/                  # 404
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/p/                # 404
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/p/<preview_id>/   # 200
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/p/<preview_id>/design/classic/  # 200
   curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8090/p/<preview_id>/        # 405
   curl -sI http://127.0.0.1:8090/p/<preview_id>/ | grep -i x-robots-tag  # noindex, nofollow, noarchive
   ```

   `<preview_id>` is a folder name under `./data/previews/`. Preview pages use root-relative
   links (`/styles.css`, `/design/<name>/`); a request carrying a preview page as its Referer is
   redirected back under that preview's `/p/<id>/`, so it never leaves the previews folder.

`PUBLIC_API_BASE_URL` works the same way for unsubscribe links: while it is empty (or local),
emails offer only the reply-based unsubscribe (`mailto:<reply address>?subject=unsubscribe`,
handled by the reply poller) and no one-click `List-Unsubscribe-Post` header. Never point the
preview tunnel at port 8000 or 3000; publishing one-click unsubscribe needs a setup that
exposes only `/u/<token>` of the API.

### Email attachments and links

Gmail accepts at most 25 MB per message, counted after base64 encoding (about a third larger
than the files). Each proposal's attachments are fitted when it is drafted, by one policy in
`backend/app/services/attachments.py`, against a budget of 23 MB encoded (about 16.8 MB of
files), leaving room for the text, HTML and headers. When they do not fit, the same steps run
in order and stop as soon as the email fits:

1. images over 300 KB are scaled to fit 1600 x 6000 px and saved as high-quality JPEG
   (quality 85) or optimised PNG, whichever is smaller (PNG only for transparent images);
2. the secondary images (logos, mobile screenshot) are merged into one `drafts.pdf`;
3. items are left out in this order: preview PDF, audit PDF, merged PDF, mobile screenshot,
   logos;
4. if the main screenshot alone is still too large it is shrunk further.

The main preview screenshot is never left out. Changed files are stored under
`data/outbox/<lead_id>/`, every attachment record carries its size, and the proposal's
`draft.attachment_report` lists what was done. The email text only mentions what is really
attached. The Gmail outbox shows the attachment count and size for each email.

Every outgoing email (proposals and follow-ups, any provider) is checked before it leaves: if
the subject, text, HTML, headers or attachment names contain `localhost`, `127.x.x.x`,
`*.localhost`, `0.0.0.0` or `[::1]`, it is not sent. A proposal goes to `needs_review`; a
follow-up is skipped and logged as `email_blocked`. A `localhost` `PORTFOLIO_URL` is left out
of the footer.

With `GMAIL_DELIVERY=send`, mail goes through `smtp.gmail.com:587` with STARTTLS. If that
connection is refused, times out, disconnects or fails the TLS upgrade, the same message is
sent over implicit TLS on port 465 (`GMAIL_SMTP_SSL_PORT`). A rejected login is not retried.
Drafts always use IMAP over SSL on port 993.

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
