# API and endpoints

Reference for the HTTP API this system exposes and the external services it calls. It contains
endpoint names, environment variable **names** and free-tier caps only, never key values.

- Local API: `http://localhost:8000` (interactive docs at `/docs`). Every `/api/*` route accepts
  an optional `Authorization: Bearer <API_TOKEN>` header; it is required when `API_TOKEN` is set.
- Public routes (no token): `GET /api/health`, the unsubscribe page `GET|POST /u/{token}`.
- The dashboard's typed client is generated from `dashboard/openapi.json`
  (`npm run gen:api` in `dashboard/`).

## Our API


### activity

| Method | Path | Summary |
|---|---|---|
| GET | `/api/activity` | Activity |

### approvals

| Method | Path | Summary |
|---|---|---|
| GET | `/api/approvals` | Approvals |

### audits

| Method | Path | Summary |
|---|---|---|
| GET | `/api/audits` | Audits |
| GET | `/api/audits/{lead_id}` | Audit Detail |

### autonomy

| Method | Path | Summary |
|---|---|---|
| GET | `/api/autonomy` | Get Autonomy |
| POST | `/api/autonomy` | Set Autonomy |

### costs

| Method | Path | Summary |
|---|---|---|
| GET | `/api/costs` | Costs |

### deals

| Method | Path | Summary |
|---|---|---|
| PATCH | `/api/deals/{lead_id}` | Patch Deal |

### discovery

| Method | Path | Summary |
|---|---|---|
| GET | `/api/discovery/jobs` | Jobs |
| GET | `/api/discovery/jobs/{job_id}` | Job |
| GET | `/api/discovery/options` | Options |
| POST | `/api/discovery/search` | Search |
| GET | `/api/discovery/sweep` | Weekly OpenStreetMap sweep: today's countries and cities (done / failed / pending), businesses per country this week (`null` = unknown), GeoNames and OSM attribution |

### events

| Method | Path | Summary |
|---|---|---|
| GET | `/api/events` | Events |

### files

| Method | Path | Summary |
|---|---|---|
| GET | `/api/files/{path}` | Files |

### free-tier

| Method | Path | Summary |
|---|---|---|
| GET | `/api/free-tier` | Free Tier |

### gallery

| Method | Path | Summary |
|---|---|---|
| GET | `/api/gallery` | Gallery |

### gmail

| Method | Path | Summary |
|---|---|---|
| GET | `/api/gmail/outbox` | Gmail Outbox |

### health

| Method | Path | Summary |
|---|---|---|
| GET | `/api/health` | Health |

### inbox

| Method | Path | Summary |
|---|---|---|
| GET | `/api/inbox` | Inbox |

### integrations

| Method | Path | Summary |
|---|---|---|
| GET | `/api/integrations` | Integrations |

### lead-counts

| Method | Path | Summary |
|---|---|---|
| GET | `/api/lead-counts` | Lead Family Tier Counts |

### leads

| Method | Path | Summary |
|---|---|---|
| GET | `/api/leads` | List Leads |
| GET | `/api/leads/{lead_id}` | Lead Detail |
| POST | `/api/leads/{lead_id}/assets` | Generate Assets |
| POST | `/api/leads/{lead_id}/generate-preview` | Generate Assets |
| POST | `/api/leads/{lead_id}/rerun` | Rerun Lead |

### manual-outreach

| Method | Path | Summary |
|---|---|---|
| GET | `/api/manual-outreach` | Manual Outreach |
| POST | `/api/manual-outreach/{item_id}/done` | Manual Outreach Done |
| POST | `/api/manual-outreach/{item_id}/skip` | Manual Outreach Skip |

### messages

| Method | Path | Summary |
|---|---|---|
| GET | `/api/messages` | Messages |
| POST | `/api/messages/{message_id}/mark-sent` | Message Mark Sent |

### monitoring

| Method | Path | Summary |
|---|---|---|
| GET | `/api/monitoring` | Monitoring |

### overview

| Method | Path | Summary |
|---|---|---|
| GET | `/api/overview` | Overview |

### proposals

| Method | Path | Summary |
|---|---|---|
| PATCH | `/api/proposals/{proposal_id}` | Edit |
| POST | `/api/proposals/{proposal_id}/approve` | Approve |
| POST | `/api/proposals/{proposal_id}/reject` | Reject |

### reports

| Method | Path | Summary |
|---|---|---|
| GET | `/api/reports` | Reports |
| GET | `/api/reports/live` | Live Report |
| GET | `/api/reports/platforms` | Platform Report |
| POST | `/api/reports/run` | Run Report |

### search

| Method | Path | Summary |
|---|---|---|
| GET | `/api/search` | Search |

### settings

| Method | Path | Summary |
|---|---|---|
| GET | `/api/settings` | Get Settings View |
| PATCH | `/api/settings` | Patch Settings |

### sources

| Method | Path | Summary |
|---|---|---|
| GET | `/api/sources` | List Sources |
| PATCH | `/api/sources/{key}` | Patch Source |
| POST | `/api/sources/{key}/run` | Run Source Now |

### suppressions

| Method | Path | Summary |
|---|---|---|
| GET | `/api/suppressions` | Suppressions |
| POST | `/api/suppressions` | Add Suppression |

### timings

| Method | Path | Summary |
|---|---|---|
| GET | `/api/timings` | Lead Timings |


## External services

Every call is counted in the `costs` table before it is made and stops at the cap shown
(`orchestrator/quota.py`). When a provider is out of quota or fails, the next one in its chain
takes over and the failure is logged as a `provider_error` policy decision (shown on the
Sources page under "Provider fallback").

### Web search (router order: `SEARCH_PROVIDER_ORDER`)

| Provider | Endpoint | Auth | Cap variable | Verified |
|---|---|---|---|---|
| Serper | `POST https://google.serper.dev/search` (and `/places`) | `X-API-KEY` header; second key `SERPER_API_KEY_FALLBACK` | `SERPER_DAILY_CAP` | yes, live 2026-11-21 |
| SerpAPI | `GET https://serpapi.com/search?engine=google` | `api_key` query | `SERPAPI_MONTHLY_CAP` | yes, live |
| ScrapingDog | `GET https://api.scrapingdog.com/google/` | `api_key` query | `SCRAPINGDOG_MONTHLY_CAP` | live call OK; parameter names from the product page (docs page 404) |
| Searlo | `GET https://api.searlo.tech/api/v1/search/web` | `x-api-key` header | `SEARLO_MONTHLY_CAP` | live call OK; `limit` vs `num` unclear in its docs |
| Tavily | `POST https://api.tavily.com/search` | Bearer | `TAVILY_MONTHLY_CAP` | yes, live |
| Exa | `POST https://api.exa.ai/search` | `x-api-key` header | `EXA_MONTHLY_CAP` + free credit | yes, live |

### LLM

| Provider | Endpoint | Use | Cap variable |
|---|---|---|---|
| Gemini | `generativelanguage.googleapis.com` `generateContent` | everything; logos and vision only here | `GEMINI_DAILY_REQUEST_CAP` |
| Groq | `POST https://api.groq.com/openai/v1/chat/completions` | text fallback when Gemini is capped or down (`LLM_FALLBACK_PROVIDER=groq`); model `GROQ_MODEL`, default `openai/gpt-oss-120b` | `GROQ_DAILY_REQUEST_CAP` |

### Social posts (public, read-only)

| Provider | Endpoints used | Auth | Platforms | Cap |
|---|---|---|---|---|
| EnsembleData | `GET https://ensembledata.com/apis/tt/keyword/search` | `token` query | TikTok | `SOCIAL_PROVIDER_DAILY_CAP` (free trial: 50 units/day) |
| ScrapeCreators | `GET https://api.scrapecreators.com/v1/{reddit/search, twitter/search/posts, threads/search, youtube/search, tiktok/search/keyword, instagram/search/hashtag}` | `x-api-key` | Reddit, X, Threads, YouTube, TikTok, Instagram | `SOCIAL_PROVIDER_DAILY_CAP` |
| SocialCrawl | `GET https://www.socialcrawl.dev/v1/{tiktok/search, facebook/search/posts}` | `x-api-key` | TikTok, Facebook | `SOCIAL_PROVIDER_DAILY_CAP` (one-time 100 free credits) |
| HikerAPI | `GET {HIKERAPI_BASE_URL}/g2/hashtag/medias` | `x-access-key` | Instagram | `SOCIAL_PROVIDER_DAILY_CAP`; off until `HIKERAPI_ACCESS_KEY` is set; response shape unverified |
| Apify | actor runs (`api.apify.com/v2/acts/...`) | `APIFY_API_TOKEN`, then `APIFY_API_TOKEN_FALLBACK` | X, Instagram, Facebook, Reddit | `APIFY_MONTHLY_CAP_USD` (both tokens together) |

LinkedIn is never searched (project rule). Only posts younger than `SOCIAL_MAX_POST_AGE_HOURS`
are kept.

### Business search (no keys)

| Source | Endpoint | Countries |
|---|---|---|
| OpenStreetMap (Overpass) | `POST {OVERPASS_API_URL}`, then `OVERPASS_FALLBACK_URLS` (empty = overpass.kumi.systems, overpass.private.coffee); at most `OVERPASS_MAX_REQUESTS_PER_MINUTE`; User-Agent carries `OVERPASS_CONTACT_EMAIL` (empty = `GMAIL_SENDER`) | all; results carry `OSM_ATTRIBUTION` |
| GeoNames cities15000 | `GET {GEONAMES_CITIES_URL}` (zip, ~3.4 MB) to `DATA_DIR/geonames`, reloaded every `GEONAMES_REFRESH_DAYS` | city list of the sweep; shown with `GEONAMES_ATTRIBUTION` (CC BY 4.0) |
| recherche-entreprises | `GET {FR_RECHERCHE_ENTREPRISES_URL}/search` (≤ `FR_RECHERCHE_MAX_RPS`/s) | FR |
| Brønnøysund (brreg) | `GET {NO_BRREG_API_URL}/enheter` | NO |
| Companies House | `GET https://api.company-information.service.gov.uk/advanced-search/companies` (Basic auth, `COMPANIES_HOUSE_API_KEY`) | GB |
| Geofabrik | size check only (`HEAD {GEOFABRIK_BASE_URL}/...-latest.osm.pbf`); nothing over 300 MB is downloaded without approval; `GEOFABRIK_ENABLED=false` | — |

Website status values: `OSM_WEBSITE_MISSING`, `WEBSITE_RECORDED`, `WEBSITE_DISCOVERED`,
`WEBSITE_VERIFIED`, `WEBSITE_UNCONFIRMED`. Searches for `EXCLUDED_COUNTRIES` are refused, and
results located there are dropped before they are stored.

**Weekly world sweep** (`OSM_SWEEP_*`). Countries with a GeoNames city, minus
`EXCLUDED_COUNTRIES`, sorted by ISO code; a country is swept on the weekday equal to its
index modulo 7 (Monday = 0), so every country once a week. Per country the
`OSM_SWEEP_CITIES_PER_COUNTRY` most populous cities; per city one Overpass query for named
`shop`, `office`, `craft` and `amenity` in restaurant, cafe, bar, fast_food, dentist, clinic,
doctors, pharmacy, beauty, veterinary, bank within `OSM_SWEEP_RADIUS_M` that list a phone or
an email (chain branches with `brand:wikidata` are skipped). Without `website` /
`contact:website` / `url`: `OSM_WEBSITE_MISSING` and a `no_website` lead (source `osm_sweep`,
at most `OSM_SWEEP_DAILY_LEAD_CAP` a day, businesses with an email first) through the normal
ingest path and the fast lane. With a website: `WEBSITE_RECORDED`, checked later by the
`site_health` trigger. Businesses are upserted on (`osm_type`, `osm_id`) with `first_seen` /
`last_seen`. Every city of every day is a row in `osm_sweep_cities`; a failed city stays
pending and is retried (20 min, doubling, up to 6 h) until `OSM_SWEEP_MAX_ATTEMPTS`.
Scheduler: `osm_sweep_tick` every 15 minutes queues `run_osm_sweep_job` (discovery queue,
one run at a time, up to 20 minutes); `geonames_refresh` daily at 02:41 UTC. By hand:
`python -m app.cli geonames-refresh` and `python -m app.cli osm-sweep [cities]`.

### Email

| Service | How | Variables |
|---|---|---|
| Gmail (outreach) | App Password: IMAP APPEND to Drafts (`GMAIL_DELIVERY=draft`) or SMTP send (`send`); no App Password: compose links | `GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, `GMAIL_DELIVERY`, `GMAIL_IMAP_*`, `GMAIL_SMTP_*`, `GMAIL_DAILY_SEND_CAP`, `REPLY_TO_EMAIL` |
| Mailpit (testing sink) | SMTP `mailpit:1025`, API `:8025` | `SMTP_HOST`, `SMTP_PORT`, `MAILPIT_API_URL` |
| ZeroBounce | `GET https://api.zerobounce.net/v2/validate`, `/v2/getcredits`; local syntax + MX check as fallback | `EMAIL_VERIFICATION_API_KEY`, `ZEROBOUNCE_MONTHLY_CAP` |
| Resend | kept for a later own sending domain | `RESEND_API_KEY`, `SENDING_DOMAIN` |

### Other

| Service | Endpoint | Variable |
|---|---|---|
| PageSpeed Insights | `GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed` | `GOOGLE_PAGESPEED_API_KEY` |

## Environment variables

The full list, with defaults for non-secret settings, is `.env.example`; each name maps to a
field in `backend/app/config.py` (checked by `tests/unit/test_env_mapping.py`). Secrets are
`SecretStr` and never printed. Run `make check-providers` to make one small live call per
configured provider.
