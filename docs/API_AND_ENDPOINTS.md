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
| ScrapeCreators | `GET https://api.scrapecreators.com/v1/{reddit/search, threads/search, youtube/search, tiktok/search/keyword, instagram/search/hashtag, linkedin/search/posts}` | `x-api-key` | Reddit, Threads, YouTube, TikTok, Instagram, LinkedIn | `SOCIAL_PROVIDER_DAILY_CAP` (1 credit per search) |
| SocialCrawl | `GET https://www.socialcrawl.dev/v1/{twitter/search/tweets?sort=latest, reddit/search?since=, facebook/search/posts, tiktok/search, linkedin/search/posts?sort_by=date_posted}` | `x-api-key` | X, Facebook, Reddit and LinkedIn fallback, TikTok fallback | `SOCIAL_PROVIDER_DAILY_CAP` (one-time 100 free credits; 1 per page, LinkedIn 5) |
| HikerAPI | `GET {HIKERAPI_BASE_URL}/g2/hashtag/medias` | `x-access-key` | Instagram | `SOCIAL_PROVIDER_DAILY_CAP`; off until `HIKERAPI_ACCESS_KEY` is set; response shape unverified |
| Apify | actor runs (`api.apify.com/v2/acts/...`) | `APIFY_API_TOKEN` (one token) | Facebook, Instagram only | `APIFY_MONTHLY_CAP_USD`, half per source |

Provider order per platform (the next one takes over on an error or a full cap): TikTok
EnsembleData > SocialCrawl > ScrapeCreators; Reddit ScrapeCreators > SocialCrawl; X SocialCrawl
only (ScrapeCreators' X search is Google-indexed with no "latest" order); Threads, YouTube
ScrapeCreators; Instagram HikerAPI > ScrapeCreators (plus Apify); Facebook SocialCrawl (plus
Apify); LinkedIn ScrapeCreators > SocialCrawl. LinkedIn is read only through these LinkedIn
endpoints: linkedin.com itself is never fetched, no cookies or logins.

The 8-hour rule: every social post, from any source or a search result whose URL is a post,
must carry its own timestamp and be at most `SOCIAL_MAX_POST_AGE_HOURS` old when it is stored;
undated posts are dropped (`app/agents/discovery/freshness.py`, enforced in the discovery
runner for every source). Scout profile leads are profiles, not posts, and are exempt.

Cap math with the defaults (`social_providers` every 60 min, 3 platforms per run, 50 credits per
provider per day): 72 searches a day over 8 platforms = 9 per platform, one every 160 minutes.
EnsembleData 9/day, ScrapeCreators 45/day, SocialCrawl 18/day (its one-time balance lasts about
5 days at that rate). Pinterest, Twitch, GitHub and Linktree are covered by Scout (8 searches a
day, each platform at least daily).

### Business search (no keys)

| Source | Endpoint | Countries |
|---|---|---|
| OpenStreetMap (Overpass) | `POST {OVERPASS_API_URL}`, then `OVERPASS_FALLBACK_URLS`; at most `OVERPASS_MAX_REQUESTS_PER_MINUTE` | all; results carry `OSM_ATTRIBUTION` |
| recherche-entreprises | `GET {FR_RECHERCHE_ENTREPRISES_URL}/search` (≤ `FR_RECHERCHE_MAX_RPS`/s) | FR |
| Brønnøysund (brreg) | `GET {NO_BRREG_API_URL}/enheter` | NO |
| Companies House | `GET https://api.company-information.service.gov.uk/advanced-search/companies` (Basic auth, `COMPANIES_HOUSE_API_KEY`) | GB |
| Geofabrik | size check only (`HEAD {GEOFABRIK_BASE_URL}/...-latest.osm.pbf`); nothing over 300 MB is downloaded without approval; `GEOFABRIK_ENABLED=false` | — |

Website status values: `OSM_WEBSITE_MISSING`, `WEBSITE_RECORDED`, `WEBSITE_DISCOVERED`,
`WEBSITE_VERIFIED`, `WEBSITE_UNCONFIRMED`. Searches for `EXCLUDED_COUNTRIES` are refused, and
results located there are dropped before they are stored.

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
