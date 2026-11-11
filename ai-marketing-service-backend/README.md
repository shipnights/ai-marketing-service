# AI Marketing Service — Backend API

**Stack:** Python 3.12 · FastAPI · Uvicorn · DynamoDB (Local → AWS) · boto3 · PyJWT · Argon2 · Anthropic Claude API · structlog · slowapi

---

## Overview

REST API server for the AI Marketing Service platform. Handles all business logic, AI orchestration via Claude API, background job processing (media generation, auto-publishing via `FastAPI.BackgroundTasks`), and integrations with Meta Graph API, TikTok for Developers, Higgsfield AI, and DALL-E 3.

---

## Project Structure

```
app/
├── main.py                      # FastAPI app, middleware, lifespan, router mount
├── config.py                    # All env vars (loaded via python-dotenv)
├── constants.py                 # Platform limits, credit costs, status enums
├── errors.py                    # Standardized error responses { status, message, code }
├── middleware.py                # SecurityHeaders, ApiKey, RequestId middlewares
├── logging_config.py            # structlog setup
├── responses.py                 # Success envelope wrapper
├── idempotency.py               # Idempotency key handling for AI jobs
│
├── auth/
│   ├── jwt.py                   # JWT creation/validation (access, refresh, temp tokens)
│   └── password.py              # CryptContext with Argon2
│
├── db/
│   ├── main.py                  # DynamoDB generic ops (get, put, update, delete, query)
│   ├── tables.py                # Table name constants from env vars
│   ├── users.py                 # User CRUD, credit balance ops
│   ├── teams.py                 # Team CRUD, team membership
│   ├── projects.py              # Project CRUD
│   ├── analysis.py              # AudienceAnalysis storage
│   ├── ideas.py                 # Idea CRUD, status updates
│   ├── posts.py                 # Post CRUD, status updates, bulk ops
│   ├── media.py                 # Media record storage
│   ├── schedules.py             # Schedule CRUD
│   ├── publish_logs.py          # PublishLog writes
│   ├── metrics.py               # PostMetrics upsert
│   ├── connected_accounts.py   # ConnectedAccount storage, token refresh
│   ├── ad_accounts.py           # [ADS] ad-platform OAuth connections + ad accounts (encrypted tokens)
│   ├── ad_stats.py              # [ADS] normalized ad insights storage + dashboard aggregations
│   └── billing.py               # [BILLING] Stripe customer map, subscription, AI-credit ledger
│
├── services/
│   ├── anthropic_service.py     # Claude API wrapper (audience analysis, ideas, posts, insights)
│   ├── media_service.py         # Higgsfield + DALL-E provider abstraction
│   ├── higgsfield_service.py    # Higgsfield image + video generation
│   ├── dalle_service.py         # DALL-E 3 image generation (fallback)
│   ├── scraper_service.py       # Playwright website scraper
│   ├── storage_service.py       # Local → S3/R2 file storage abstraction
│   ├── meta_publisher.py        # Meta Graph API publish logic (IG, FB, Reels, Stories)
│   ├── tiktok_publisher.py      # TikTok chunked upload + publish flow
│   ├── email_service.py         # AWS SES (failure notifications)
│   ├── schedule_service.py      # Conflict detection, bulk scheduling helpers
│   ├── credits_service.py       # Credit balance check + deduction
│   ├── ads/                     # [ADS] connector abstraction (base) + google/meta/tiktok/reddit
│   │   ├── base.py · schema.py · registry.py · sync.py
│   │   └── google.py · meta.py · tiktok.py · reddit.py
│   └── billing/                 # [BILLING] plans catalog · stripe_service · webhooks
│
├── api/v1/
│   ├── routes.py                # All router includes
│   ├── deps.py                  # Shared FastAPI Depends() (auth, team guard, credit check)
│   ├── auth.py                  # Register, login, refresh, logout, email verify, password reset
│   ├── projects.py              # Project CRUD endpoints
│   ├── analysis.py              # Audience analysis endpoints
│   ├── ideas.py                 # Idea generation + management
│   ├── posts.py                 # Post generation + review
│   ├── media.py                 # Media generation + upload + library
│   ├── schedule.py              # Scheduling + calendar + CSV export
│   ├── publishing.py            # Platform OAuth + account management
│   ├── analytics.py             # Metrics + AI insights
│   ├── quick_post.py            # Quick post mode (single or batch)
│   ├── integrations.py          # [ADS] ad-platform OAuth connect/callback, account mgmt
│   ├── ads.py                   # [ADS] unified reporting (overview/campaigns/insights/sync)
│   ├── billing.py               # [BILLING] plans, checkout, portal, credits, Stripe webhook
│   └── webhooks.py              # (Future) Zapier / Make.com outbound webhooks
│
└── prompts/
    ├── audience_analysis.py     # Claude prompt: audience analysis
    ├── idea_generation.py       # Claude prompt: idea generation
    ├── post_generation.py       # Claude prompt: post generation
    ├── schedule_recommendation.py
    ├── marketing_plan.py
    └── performance_insights.py
```

---

## DynamoDB Schema (Local → AWS)

Single-table design with composite keys.

### Tables

| Table | Env Var | Purpose |
|-------|---------|---------|
| `ai-marketing-{ENV}-main` | `DYNAMODB_MAIN_TABLE` | Users, teams, projects |
| `ai-marketing-{ENV}-content` | `DYNAMODB_CONTENT_TABLE` | Ideas, posts, media |
| `ai-marketing-{ENV}-schedule` | `DYNAMODB_SCHEDULE_TABLE` | Schedules, publish logs |
| `ai-marketing-{ENV}-analytics` | `DYNAMODB_ANALYTICS_TABLE` | Post metrics, AI insights |
| `ai-marketing-{ENV}-accounts` | `DYNAMODB_ACCOUNTS_TABLE` | Connected social accounts |

### Key Patterns

```
# Users
pk: USER#{user_id}           sk: PROFILE
pk: EMAIL#{email}            sk: USER           (GSI for email lookup)

# Teams
pk: TEAM#{team_id}           sk: PROFILE
pk: TEAM#{team_id}           sk: MEMBER#{user_id}

# Projects
pk: TEAM#{team_id}           sk: PROJECT#{project_id}
pk: PROJECT#{project_id}     sk: PROFILE        (GSI for direct project lookup)

# Ideas
pk: PROJECT#{project_id}     sk: IDEA#{idea_id}

# Posts
pk: PROJECT#{project_id}     sk: POST#{post_id}
pk: POST#{post_id}           sk: PROFILE        (GSI for direct post lookup)

# Media
pk: PROJECT#{project_id}     sk: MEDIA#{media_id}

# Schedules
pk: TEAM#{team_id}           sk: SCHEDULE#{scheduled_at}#{post_id}
pk: POST#{post_id}           sk: SCHEDULE       (GSI)

# Publish Logs
pk: POST#{post_id}           sk: LOG#{timestamp}

# Post Metrics
pk: POST#{post_id}           sk: METRICS

# Connected Accounts (social publishing)
pk: TEAM#{team_id}           sk: ACCOUNT#{platform}#{account_id}

# Ad-platform connections & accounts  [ADS]  (accounts table)
pk: TEAM#{team_id}           sk: ADCONN#{platform}              (encrypted OAuth tokens)
pk: TEAM#{team_id}           sk: ADACCT#{platform}#{account_id} (discovered ad account)

# Ad insights  [ADS]  (analytics table)
pk: ADSTATS#{team_id}        sk: {date}#{platform}#{account_id}#{level}#{entity_id}

# Billing & credit ledger  [BILLING]  (main table)
pk: TEAM#{team_id}           sk: BILLING                        (subscription + stripe customer id)
pk: TEAM#{team_id}           sk: CREDIT#{timestamp}#{seq}       (AI-credit ledger entry)
pk: STRIPECUST#{customer_id} sk: TEAM                           (customer → team reverse lookup)
pk: STRIPEEVENT#{event_id}   sk: SEEN                           (webhook idempotency)

# platform ∈ google_ads · meta_ads · tiktok_ads · reddit_ads
```

---

## Authentication

How auth works:
- **Access token:** 7 days, HS256, `JWT_SECRET`
- **Refresh token:** 30 days, tracked JTI for revocation in DynamoDB
- **Temp token:** 1 hour, used during registration wizard
- **Password hashing:** Argon2 via passlib
- **Dev bypass:** `ENABLE_DEV_AUTH_HEADER=true` + `X-User-Id` header (dev only)

---

## Environment Variables

```env
# Server
ENV=development                    # development | production
PORT=8000
CORS_ORIGINS=http://localhost:3000

# Auth
JWT_SECRET=your-secret-min-32-chars
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080           # 7 days

# API Key guard
REQUIRE_API_KEY=false
API_KEY=

# DynamoDB (local dev)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=local            # any value for local DynamoDB
AWS_SECRET_ACCESS_KEY=local        # any value for local DynamoDB
DYNAMODB_ENDPOINT_URL=http://localhost:8001   # DynamoDB Local port
DYNAMODB_MAIN_TABLE=ai-marketing-dev-main
DYNAMODB_CONTENT_TABLE=ai-marketing-dev-content
DYNAMODB_SCHEDULE_TABLE=ai-marketing-dev-schedule
DYNAMODB_ANALYTICS_TABLE=ai-marketing-dev-analytics
DYNAMODB_ACCOUNTS_TABLE=ai-marketing-dev-accounts

# AI
ANTHROPIC_API_KEY=sk-ant-...

# Media Generation
HIGGSFIELD_API_KEY=...
OPENAI_API_KEY=sk-...              # DALL-E 3 fallback

# File Storage
STORAGE_DRIVER=local               # local | s3 | r2
MEDIA_LOCAL_PATH=./uploads
MEDIA_BASE_URL=http://localhost:8000/uploads
AWS_S3_BUCKET=ai-marketing-media

# Meta (Instagram/Facebook)
META_APP_ID=...
META_APP_SECRET=...
META_REDIRECT_URI=http://localhost:8000/api/v1/integrations/meta/callback

# TikTok
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=http://localhost:8000/api/v1/integrations/tiktok/callback

# Email (publish failure notifications)
AWS_SES_REGION=us-east-1
EMAIL_FROM=noreply@yourdomain.com
SUPPORT_EMAIL=support@yourdomain.com

# Rate limiting (slowapi)
RATE_LIMIT=100/minute

# Dev
ENABLE_DEV_AUTH_HEADER=true
```

---

## Quick Start

```bash
# 1. Start DynamoDB Local
docker run -d -p 8001:8000 amazon/dynamodb-local

# 2. Create tables (run setup script)
python scripts/create_tables.py

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy env and start server
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

---

## API Response Format

Every response uses this envelope:

```json
// Success
{ "status": "success", "data": { ... }, "message": "Optional message" }

// Error
{ "status": "error", "message": "Human readable", "code": "ERROR_CODE", "detail": "..." }
```

---

## TODO LIST

---

### PHASE 0 — Foundation & Infrastructure

#### 0.1 Project Setup
- [x] Initialize Python 3.12 project with `pyproject.toml`
- [x] Create `requirements.txt` (fastapi, uvicorn, boto3, passlib[argon2], PyJWT, anthropic, httpx, slowapi, structlog, python-multipart, python-dotenv, jinja2, email-validator, tzdata)
- [x] Set up `.env.example` with all required keys documented
- [x] Create `Dockerfile` (python:3.12-slim, multi-stage)
- [x] Create `docker-compose.yml` with `amazon/dynamodb-local` on port 8001 and FastAPI service

#### 0.2 App Entry Point (`app/main.py`)
- [x] Create `FastAPI` app with `lifespan` context manager (startup/shutdown logging)
- [x] Apply middleware stack in order:
  1. `ApiKeyMiddleware` — validates `X-API-Key` when `REQUIRE_API_KEY=true`
  2. `SecurityHeadersMiddleware` — OWASP security headers
  3. `RequestIdMiddleware` — generate/propagate `X-Request-Id`
  4. `CORSMiddleware` — from `CORS_ORIGINS` env var
  5. `SlowAPIMiddleware` — rate limiting from `RATE_LIMIT` env var
- [x] Mount `api/v1` router at `/api/v1`
- [x] Mount static files at `/uploads` for local media serving
- [x] Add `/health` and `/ready` routes
- [x] Global exception handlers for `AppError`, `RequestValidationError`, unhandled exceptions

#### 0.3 Config (`app/config.py`)
- [x] Create config module loading all env vars via `python-dotenv` with `_parse_bool` / `_parse_int` helpers
- [ ] Validate required keys on startup (raise on missing `JWT_SECRET`, `ANTHROPIC_API_KEY`)
- [x] Expose module-level constants imported across codebase

#### 0.4 Auth (`app/auth/`)
- [x] `app/auth/password.py` — `CryptContext(schemes=["argon2"])`, `hash_password()`, `verify_password()`
- [x] `app/auth/jwt.py` — `create_access_token(user_id, team_id, email)`, `create_refresh_token`, `create_temp_token`, `decode_token` — all tokens embed `team_id` claim
- [x] `app/db/tokens.py` — `revoke_refresh_token(jti)`, `is_refresh_token_revoked(jti)` using `REVOKED_TOKEN#{jti}` pk pattern
- [x] `verify_refresh_token(token)` — implemented as `decode_refresh_token()` in `jwt.py`: decode + check `is_refresh_token_revoked(jti)`

#### 0.5 Logging (`app/logging_config.py`)
- [x] Configure structlog with JSON renderer for production (`STRUCTURED_LOGGING=1`), console renderer for dev
- [x] `request_id` bound per request via `RequestIdMiddleware`
- [x] `get_logger()` module-level helper for consistent logger access across modules

#### 0.6 Error Handling (`app/errors.py`, `app/responses.py`)
- [x] `AppError(status_code, message, code)` — custom exception class
- [x] `error_response(message, code, detail=None)` — standard error JSON `{ status: "error", message, code }`
- [x] `success_response(data, message=None)` — standard success JSON `{ status: "success", data }`
- [x] `accepted_response(job_id)` — 202 response for background jobs

#### 0.7 DynamoDB Client (`app/db/main.py`)
- [x] Create DynamoDB resource via `boto3.resource("dynamodb", endpoint_url=DYNAMODB_ENDPOINT_URL)`; expose `dynamodb` module var for test patching
- [x] Generic helpers: `get_item`, `put_item`, `update_item`, `delete_item`, `query_by_pk`, `query_by_pk_sk_begins`, `query_gsi` — all accept optional `table_name`
- [x] `app/db/tables.py` — named constants: `MAIN_TABLE`, `CONTENT_TABLE`, `SCHEDULE_TABLE`, `ANALYTICS_TABLE`, `ACCOUNTS_TABLE`
- [x] Handle `ClientError` from boto3 in all helpers (logged, returns None/False/[])

#### 0.8 DynamoDB Table Setup (`scripts/create_tables.py`)
- [x] Script to create all 5 DynamoDB tables with correct pk/sk and GSIs
- [x] Main table GSIs: `email-index`, `user_id-index`, `project_id-index`
- [x] Content table GSIs: `post_id-index`
- [x] Schedule table GSIs: `post_id-index`
- [x] Accounts table GSIs: `platform-index`
- [x] Script is idempotent (skip if table already exists)
- [ ] `scripts/seed_dev.py` — seed demo team, user, and project for local dev

#### 0.9 FastAPI Shared Dependencies (`app/api/v1/deps.py`)
- [x] `CurrentUser` dataclass `(user_id, team_id, email)`
- [x] `get_current_user()` — decode JWT or use `X-User-Id`/`X-Team-Id` dev bypass when `ENABLE_DEV_AUTH_HEADER=true`
- [x] `get_team_guard` — verify user belongs to team from route param (403 if mismatch)
- [x] `require_credits(cost: int)` — factory returning `Depends` that checks credit balance → 402 if insufficient

---

### PHASE 1 — Auth & User Management

#### 1.1 Auth Routes (`app/api/v1/auth.py`)
- [x] `POST /auth/register/step-1` — email + password → validate unique email, hash password, store user, return temp token
- [x] `POST /auth/register/step-2` — profile info (full_name, role) → update user, return temp token
- [x] `POST /auth/register/step-3` — create team, add user as owner, return new temp token
- [x] `POST /auth/register/step-4` — finalize registration, generate access + refresh tokens
- [x] `POST /auth/login` — email + password → verify → return access + refresh tokens
- [x] `POST /auth/refresh` — refresh token → validate JTI not revoked → revoke old JTI → return new token pair
- [x] `POST /auth/logout` — revoke refresh token JTI in DynamoDB (idempotent)
- [x] `POST /auth/verify-email` — validate temp token → mark email verified
- [x] `POST /auth/forgot-password` — always 200 (prevents email enumeration); issues temp token for reset
- [x] `POST /auth/reset-password` — validate temp token → hash + store new password, bump token_version
- [x] `GET /auth/me` — return current user profile (strips password_hash)

#### 1.2 User DB (`app/db/users.py`)
- [x] `create_user(user: dict)` → put USER#{id}/PROFILE + EMAIL#{email}/USER items
- [x] `get_user_by_id(user_id)` → get item
- [x] `get_user_by_email(email)` → query email-index GSI, then fetch full profile
- [x] `update_user(user_id, updates: dict)` → update item + stamps `updated_at`

#### 1.3 Team DB (`app/db/teams.py`)
- [x] `create_team(team_id, team_name, owner_user_id, ai_credits=500)` → put item
- [x] `get_team(team_id)` → get item
- [x] `add_team_member(team_id, user_id, role)` → put MEMBER# item
- [x] `get_team_members(team_id)` → query all MEMBER# sk items
- [x] `update_member_role(team_id, user_id, new_role)` → update item
- [x] `remove_team_member(team_id, user_id)` → delete item
- [x] `deduct_team_credits(team_id, amount)` → subtract from ai_credits field

---

### PHASE 1 — Project Management API

#### 1.4 Project Routes (`app/api/v1/projects.py`)
- [x] `POST /projects` — create project, status=draft, return project
- [x] `GET /projects` — list team projects with optional `?status=` filter
- [x] `GET /projects/{project_id}` — get project, assert team ownership
- [x] `PATCH /projects/{project_id}` — update fields (partial update)
- [x] `POST /projects/{project_id}/activate` — transition to active status
- [x] `POST /projects/{project_id}/archive` — transition to archived status

#### 1.5 Project DB (`app/db/projects.py`)
- [x] `create_project(team_id, project_id, data)` → put item with status=draft
- [x] `get_project(project_id)` → get via project_id-index GSI
- [x] `get_project_for_team(project_id, team_id)` → direct pk/sk get (ownership check)
- [x] `list_projects(team_id, status_filter=None)` → query PROJECT# sk prefix
- [x] `update_project(project_id, team_id, updates)` → update item + stamps `updated_at`

#### 1.6 Website Scraper (`app/services/scraper_service.py`)
- [ ] Install Playwright: `playwright install chromium`
- [ ] `scrape_website(url: str) -> dict` — async, 10s timeout:
  - Extract: title, meta description, h1/h2 headings, cleaned body text, OG image, social links
  - Handle: timeout, bot-detection, non-HTML (return empty fields, no raise)
- [ ] Route: `POST /projects/{project_id}/analyze-website` — run as `BackgroundTasks` task, update project on completion

---

### PHASE 2 — AI Audience Analysis & Idea Generation

#### 2.1 Claude Service (`app/services/anthropic_service.py`)
- [ ] Initialize `anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)`
- [ ] `call_claude(prompt: str, model="claude-sonnet-4-6", max_tokens=4096) -> dict` — calls messages API, parses JSON from response
- [ ] Handle `anthropic.APIError`, `anthropic.RateLimitError` → map to `AppError`
- [ ] All prompts use tool_use for structured JSON output

#### 2.2 Audience Analysis (`app/services/anthropic_service.py` + `app/prompts/audience_analysis.py`)
- [ ] `analyze_audience(project: dict, scraped_website: dict | None) -> dict` — builds prompt, calls Claude, returns:
  ```json
  {
    "primary_audience": { "description": "", "psychographics": [], "behaviors": [], "platforms_used": [] },
    "secondary_audience": { ... },
    "messaging_angles": [],
    "best_platforms": [],
    "best_content_types": [],
    "recommended_posting_times": { "instagram": [], "facebook": [], "tiktok": [] }
  }
  ```
- [ ] Cost: 5 credits
- [ ] Route `POST /analysis/{project_id}/audience` — check credits → run as BackgroundTask → return 202
- [ ] Route `GET /analysis/{project_id}/audience` — return stored result
- [ ] Route `PUT /analysis/{project_id}/audience` — save user overrides

#### 2.3 Analysis DB (`app/db/analysis.py`)
- [ ] `save_audience_analysis(project_id, data)` → put item
- [ ] `get_audience_analysis(project_id)` → get item
- [ ] `update_audience_analysis(project_id, updates)` → update item (for user overrides)

#### 2.4 Idea Generation (`app/services/anthropic_service.py` + `app/prompts/idea_generation.py`)
- [ ] `generate_ideas(project: dict, audience_analysis: dict) -> list[dict]` — builds prompt, calls Claude, returns 5–15 ideas each with: title, concept, hook, platforms, content_type, estimated_impact, tone, reasoning
- [ ] Cost: 5 credits
- [ ] Require audience analysis exists before running
- [ ] Route `POST /ideas/{project_id}/generate` — check credits → BackgroundTask → return 202
- [ ] Route `GET /ideas/{project_id}` — list ideas with optional status filter
- [ ] Route `PUT /ideas/{project_id}/{idea_id}` — approve / reject / edit concept+title
- [ ] Route `POST /ideas/{project_id}/regenerate` — re-prompt including rejected idea titles as context

#### 2.5 Ideas DB (`app/db/ideas.py`)
- [ ] `save_ideas(project_id, ideas: list[dict])` → batch put
- [ ] `get_idea(project_id, idea_id)` → get item
- [ ] `list_ideas(project_id, status=None)` → query with optional filter
- [ ] `update_idea_status(project_id, idea_id, status)` → update item
- [ ] `update_idea_content(project_id, idea_id, title, concept)` → update item

---

### PHASE 3 — Post Text Generation

#### 3.1 Post Generation (`app/services/anthropic_service.py` + `app/prompts/post_generation.py`)
- [ ] `generate_posts(idea: dict, audience: dict, platforms: list[str], tone: str) -> list[dict]` — for each platform generates: caption_draft, hashtags (platform count rules), cta, visual_brief, post_type
- [ ] Platform hashtag rules enforced in prompt: IG 15–25, FB 3–5, TT 5–8
- [ ] Caption length validated after generation (warn if over platform limit)
- [ ] Cost: 3 credits per idea batch
- [ ] Route `POST /posts/{project_id}/generate` — body: `{ idea_ids: list[str] }` → BackgroundTask → return 202
- [ ] Route `GET /posts/{project_id}` — list posts, filter by idea_id / platform / status
- [ ] Route `GET /posts/{project_id}/{post_id}`
- [ ] Route `PUT /posts/{project_id}/{post_id}` — edit caption, hashtags, cta
- [ ] Route `POST /posts/{project_id}/{post_id}/approve` — status → approved
- [ ] Route `POST /posts/{project_id}/{post_id}/reject` — status → rejected
- [ ] Route `POST /posts/{project_id}/{post_id}/regenerate-caption` — 1 credit, BackgroundTask
- [ ] Route `POST /posts/{project_id}/bulk-approve` — body: `{ post_ids: list[str] }`

#### 3.2 Posts DB (`app/db/posts.py`)
- [ ] `save_posts(project_id, posts: list[dict])` → batch put
- [ ] `get_post(post_id)` → get via GSI
- [ ] `list_posts(project_id, idea_id=None, platform=None, status=None)` → query + filter
- [ ] `update_post(post_id, updates: dict)` → update item
- [ ] `bulk_update_status(post_ids: list, status: str)` → batch update

---

### PHASE 4 — Media Generation

#### 4.1 Media Service (`app/services/media_service.py`)
- [ ] Provider abstraction: `generate_image(prompt, quality) -> str (url)` and `generate_video(prompt, duration) -> str (url)`
- [ ] `HiggsFieldImageProvider` — call Higgsfield API, poll for result, return CDN URL
- [ ] `DalleProvider` — call OpenAI images API, return URL (fallback on Higgsfield error)
- [ ] `HiggsFieldVideoProvider` — call Higgsfield video API, async poll for completion
- [ ] Provider selection: try Higgsfield first, catch exception → fall back

#### 4.2 Media Generation Routes (`app/api/v1/media.py`)
- [ ] `POST /media/{post_id}/generate-image` — check 1 credit → BackgroundTask (generate, store, update post.media_id) → return 202
- [ ] `POST /media/{post_id}/generate-video` — check credits (5 or 15) → BackgroundTask → return 202
- [ ] `GET /media/{post_id}/status` — return `{ status: queued|processing|complete|failed, media_url? }`
- [ ] `POST /media/{post_id}/upload` — multipart upload, validate MIME + size, store, link to post
- [ ] `GET /media/library/{project_id}` — list all media for project (paginated)
- [ ] `DELETE /media/{media_id}` — remove record + storage file
- [ ] `POST /media/{post_id}/assign/{media_id}` — link existing media to post

#### 4.3 Storage Service (`app/services/storage_service.py`)
- [ ] `save_file(content: bytes, filename: str, content_type: str) -> str (url)` — local saves to `MEDIA_LOCAL_PATH`, returns `MEDIA_BASE_URL/{filename}`; S3 uploads to bucket, returns CDN URL
- [ ] `delete_file(url: str)` — removes from local disk or S3
- [ ] Auto-convert uploaded images to WebP (Pillow)
- [ ] Validate MIME types on upload (jpg, png, webp, mp4, mov, gif)

#### 4.4 Media DB (`app/db/media.py`)
- [ ] `save_media(project_id, media_id, data: dict)` → put item
- [ ] `get_media(media_id)` → get item
- [ ] `list_media(project_id)` → query
- [ ] `delete_media(media_id)` → delete item

---

### PHASE 5 — Final Approval & Scheduling

#### 5.1 Schedule Recommendation (`app/services/anthropic_service.py` + `app/prompts/schedule_recommendation.py`)
- [ ] `recommend_schedule(project, audience_analysis) -> dict` — Claude returns best days, times per platform, frequency, content mix ratio
- [ ] Cost: 2 credits
- [ ] Route `POST /schedule/{project_id}/recommend` — check credits → call Claude → return recommendation

#### 5.2 Schedule Routes (`app/api/v1/schedule.py`)
- [ ] `POST /schedule/{post_id}` — body: `{ scheduled_at: ISO8601, platform: str }` → create Schedule, set post status=scheduled
- [ ] Validate: `scheduled_at` must be in the future
- [ ] Warn (not block) if same platform scheduled within 3h
- [ ] `GET /schedule/calendar` — query params: `{ team_id, start_date, end_date }` → list scheduled posts
- [ ] `PUT /schedule/{post_id}` — update `scheduled_at`
- [ ] `DELETE /schedule/{post_id}` — delete Schedule, revert post status=approved
- [ ] `POST /schedule/{project_id}/bulk` — body: `{ post_ids, start_date, end_date }` → distribute evenly
- [ ] `GET /schedule/{project_id}/export` — return CSV of scheduled posts

#### 5.3 Schedule DB (`app/db/schedules.py`)
- [ ] `create_schedule(post_id, team_id, platform, scheduled_at)` → put item
- [ ] `get_schedule(post_id)` → get via GSI
- [ ] `list_schedules(team_id, start_date, end_date)` → query by TEAM#{team_id} pk, SCHEDULE# sk range
- [ ] `update_schedule(post_id, scheduled_at)` → update item
- [ ] `delete_schedule(post_id)` → delete item

---

### PHASE 6 — Auto-Publishing Engine

#### 6.1 Meta OAuth (`app/api/v1/integrations.py`)
- [ ] `GET /integrations/meta/connect` — build Meta OAuth URL with `instagram_content_publish`, `pages_manage_posts`, `pages_read_engagement` scopes → redirect
- [ ] `GET /integrations/meta/callback` — exchange code for short-lived token → extend to long-lived (60 days) → store `ConnectedAccount`
- [ ] Schedule token refresh job 7 days before expiry (FastAPI BackgroundTasks triggered on each auth check)
- [ ] `GET /integrations/tiktok/connect` — build TikTok OAuth URL with `video.upload`, `video.publish` → redirect
- [ ] `GET /integrations/tiktok/callback` — exchange code → store `ConnectedAccount`
- [ ] `GET /integrations/accounts` — list team's connected accounts
- [ ] `DELETE /integrations/accounts/{account_id}` — remove account
- [ ] `GET /integrations/accounts/{account_id}/health` — validate token not expired

#### 6.2 Connected Accounts DB (`app/db/connected_accounts.py`)
- [ ] `save_account(team_id, platform, account_id, account_name, access_token, refresh_token, token_expires_at, scopes)` → put item
- [ ] `get_account(team_id, platform, account_id)` → get item
- [ ] `list_accounts(team_id)` → query
- [ ] `update_token(team_id, platform, account_id, new_access_token, new_expires_at)` → update item
- [ ] `delete_account(team_id, platform, account_id)` → delete item

#### 6.3 Meta Publisher (`app/services/meta_publisher.py`)
- [ ] `publish_feed_post(account, post, media_url) -> str (platform_post_id)` — `POST /{ig-user-id}/media` → `POST /media_publish`
- [ ] `publish_reel(account, post, video_url) -> str` — resumable upload for files >4MB
- [ ] `publish_story(account, post, media_url) -> str` — `media_type=STORIES`
- [ ] `publish_facebook_post(page, post, media_url) -> str`
- [ ] All methods raise `PublishError` on failure (caught by publishing worker)

#### 6.4 TikTok Publisher (`app/services/tiktok_publisher.py`)
- [ ] `publish_video(account, post, video_url) -> str` — init upload → chunked PUT → finalize → return publish_id
- [ ] Poll status until `PUBLISH_COMPLETE` or error
- [ ] Raise `PublishError` on failure

#### 6.5 Publishing Background Worker (`app/api/v1/schedule.py` or standalone worker)
- [ ] Use FastAPI `BackgroundTasks` or a standalone Python script run via cron / APScheduler
- [ ] Every minute: query `DYNAMODB_SCHEDULE_TABLE` for posts with `scheduled_at <= now + 5min` and `status=scheduled`
- [ ] For each due post:
  1. Fetch `ConnectedAccount` for platform — skip + notify if missing
  2. Validate token not expired — refresh if needed
  3. Fetch media bytes from storage
  4. Call correct publisher service
  5. On success: update `Post.status=published`, store `platform_post_id`, `published_at`
  6. On failure: log to `PublishLog`, retry up to 3x with exponential backoff
  7. After 3 failures: `Post.status=failed`, send email notification via SES
- [ ] Log every attempt to `PublishLog` (success and failure)

#### 6.6 Publish Logs DB (`app/db/publish_logs.py`)
- [ ] `log_attempt(post_id, platform, attempt, status, api_response, error_message)` → put item
- [ ] `get_logs(post_id)` → query all LOG# sk items

#### 6.7 Email Notifications (`app/services/email_service.py`)
- [ ] `send_publish_failure_email(user_email, post_title, platform, error_message)` → AWS SES
- [ ] Use Jinja2 HTML template

---

### PHASE 7 — Analytics

#### 7.1 Metrics Fetching
- [ ] Background task (run daily): fetch metrics for all published posts
- [ ] `fetch_meta_metrics(platform_post_id, access_token) -> dict` — call `/{post-id}/insights`
- [ ] `fetch_tiktok_metrics(publish_id, access_token) -> dict` — call TikTok analytics endpoint
- [ ] Upsert to `PostMetrics` via `app/db/metrics.py`
- [ ] Route `GET /analytics/{post_id}/metrics` — return stored metrics
- [ ] Route `GET /analytics/{project_id}/summary` — aggregate: total reach, best post, best idea

#### 7.2 AI Insight Generation
- [ ] `generate_performance_insights(project_id, metrics: list[dict]) -> str` — Claude prompt with last 30 days metrics → returns insight report text
- [ ] Cost: 1 credit
- [ ] Route `GET /analytics/{project_id}/insights` — return latest stored insight
- [ ] Route `POST /analytics/{project_id}/insights/generate` — check credits → BackgroundTask

#### 7.3 Metrics DB (`app/db/metrics.py`)
- [ ] `upsert_metrics(post_id, platform, metrics: dict)` → update item (create if not exists)
- [ ] `get_metrics(post_id)` → get item
- [ ] `list_project_metrics(project_id, post_ids: list)` → batch get

---

### PHASE 8 — Extended Analysis

#### 8.1 Social Profile Analysis
- [ ] Route `POST /analysis/instagram` — body: `{ ig_user_id }` → fetch profile via Meta API → Claude analyzes → return report (3 credits)
- [ ] Route `POST /analysis/facebook-page` — body: `{ page_id }` → page insights → Claude analyzes (3 credits)
- [ ] Route `POST /analysis/tiktok` — body: `{ user_id }` → TikTok data → Claude analyzes (3 credits)

#### 8.2 Marketing Plan Generation
- [ ] `generate_marketing_plan(project, audience, plan_duration: int) -> dict` — Claude builds full 30/60/90-day plan
- [ ] Output: content calendar, platform strategy, KPI targets, budget allocation
- [ ] Cost: 10 credits (30-day), 20 credits (90-day)
- [ ] Route `POST /plans/{project_id}/generate` — body: `{ plan_duration: 30 | 60 | 90 }`
- [ ] Route `GET /plans/{project_id}` — return latest stored plan

---

### PHASE 9 — Quick Post Mode

- [ ] Route `POST /quick-post` — body: `{ media_id, platform, tone, project_id? }` → Claude generates caption + hashtags + CTA (1 credit)
- [ ] Route `POST /quick-post/batch` — body: `{ media_ids: list, platform, tone }` → batch captioning

---

### PHASE A — Ads, Unified Stats & Billing ✅ (implemented — see [ADS_STATS_BILLING_LOGIC.md](../ADS_STATS_BILLING_LOGIC.md))

#### A.1 Ad connectors (`app/services/ads/`)
- [x] `base.py` — `AdConnector` ABC + `TokenBundle` + connector exceptions
- [x] `schema.py` — normalized `AdAccountInfo` / `Campaign` / `AdInsight` + derived-metric math (CTR/CPC/CPM/CPA/CVR/ROAS)
- [x] `google.py` · `meta.py` · `tiktok.py` · `reddit.py` — OAuth + accounts + campaigns + per-day insights
- [x] `registry.py` — platform → connector resolution; `sync.py` — pull → normalize → store (idempotent)

#### A.2 Integrations API (`app/api/v1/integrations.py`)
- [x] `GET /integrations` — platforms + connection state; `GET /integrations/{platform}/connect` (signed-state OAuth)
- [x] `GET /integrations/{platform}/callback` — exchange, **encrypt tokens**, discover accounts
- [x] account list / refresh / enable-toggle / `DELETE` disconnect

#### A.3 Ads reporting API (`app/api/v1/ads.py`)
- [x] `GET /ads/overview` (totals + per-platform + timeseries) · `/ads/campaigns` · `/ads/insights` · `/ads/accounts`
- [x] `POST /ads/sync` — manual pull (same `sync_team` a scheduled worker will call)

#### A.4 Billing (`app/services/billing/`, `app/api/v1/billing.py`, `app/db/billing.py`)
- [x] Plan catalog (Solo/Team/Agency) + credit packs; `GET /billing/plans` · `/subscription` · `/credits`
- [x] Stripe Checkout (subscription + credit packs) · Billing Portal
- [x] `POST /billing/webhook` — signature-verified; `invoice.paid`/subscription/checkout → idempotent credit grants
- [x] Atomic AI-credit ledger (`adjust_credits`) on the existing `ai_credits` balance

#### A.5 Security
- [x] Fernet token encryption at rest (`app/utils/crypto.py`, `TOKEN_ENCRYPTION_KEY`)
- [x] `.gitignore` blocks `.env` + plaintext key files

> New env vars for this phase are documented in `.env.example` (Paid Ads platforms, Token
> encryption, Billing/Stripe). Scheduled sync, alerts, true-ROAS attribution, and ad
> *management* (write) are Phases B–E in `ADS_STATS_BILLING_LOGIC.md`.

---

### Non-Feature / Infrastructure Tasks

- [x] `scripts/create_tables.py` — create all DynamoDB tables (idempotent)
- [ ] `scripts/seed_dev.py` — seed demo team, user, and project for local dev
- [ ] Set up APScheduler for recurring background tasks (metrics fetch, token refresh checks, publishing worker)
- [x] Request ID middleware (logs `X-Request-Id` on every request)
- [ ] Structured request logging middleware (log method, path, status, latency per request)
- [x] OpenAPI/Swagger auto-generated at `/docs` (FastAPI default, no extra work)
- [x] `Dockerfile` for production (python:3.12-slim multi-stage)
- [ ] Set up Sentry SDK for error tracking (`sentry-sdk[fastapi]`)

---

### Future / Backlog

- [ ] A/B post variants — 2 caption variants per post, track winner via metrics
- [ ] Competitor monitoring — periodic scrape of competitor profiles, Claude analysis
- [ ] Hashtag intelligence engine — track hashtag performance per niche
- [ ] Zapier/Make.com outbound webhooks — fire on post.published, post.failed
- [ ] Public API keys (Agency tier) — API key auth for external integrations
- [ ] White-label client workspace isolation
- [x] Ad platform integration (Google · Meta · TikTok · Reddit) — read/stats done; **management = Phase D**
- [ ] Brand voice profile per project — injected into all Claude prompts
- [ ] Multi-step team approval chain (editor → manager → client)
