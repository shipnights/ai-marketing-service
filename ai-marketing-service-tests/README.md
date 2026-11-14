# AI Marketing Service — Tests

**Stack:** pytest · moto (DynamoDB mocking) · httpx · Playwright · Jest · React Testing Library

> **In this copy:** only the backend tests (`backend/`) are included. The frontend, Playwright and Jest
> parts described below are not part of this bundle.

---

## Overview

Dedicated test repository for the AI Marketing Service platform. Backend tests use pytest + moto (AWS mock library) to test against a fully mocked DynamoDB — no real AWS required. Frontend tests use Jest + React Testing Library for component tests and Playwright for E2E. AI service behavior tests (real Claude API) are gated behind an env flag and run on a weekly schedule.

---

## Test Architecture

```
ai-marketing-service-tests/
├── backend/
│   ├── conftest.py                  # Pytest fixtures: mocked DynamoDB, test app client, factories
│   ├── unit/
│   │   ├── test_audience_analysis.py
│   │   ├── test_idea_generation.py
│   │   ├── test_post_generation.py
│   │   ├── test_media_service.py
│   │   ├── test_scraper_service.py
│   │   ├── test_schedule_service.py
│   │   ├── test_credits_service.py
│   │   ├── test_meta_publisher.py
│   │   ├── test_tiktok_publisher.py
│   │   ├── test_jwt.py
│   │   ├── test_password.py
│   │   └── test_db_helpers.py
│   ├── integration/
│   │   ├── test_auth_routes.py
│   │   ├── test_project_routes.py
│   │   ├── test_analysis_routes.py
│   │   ├── test_idea_routes.py
│   │   ├── test_post_routes.py
│   │   ├── test_media_routes.py
│   │   ├── test_schedule_routes.py
│   │   ├── test_integration_routes.py
│   │   └── test_analytics_routes.py
│   └── fixtures/
│       ├── claude_responses/         # Captured real Claude API responses (JSON)
│       │   ├── audience_analysis.json
│       │   ├── idea_generation.json
│       │   ├── post_generation.json
│       │   ├── schedule_recommendation.json
│       │   ├── marketing_plan_30.json
│       │   └── performance_insights.json
│       └── factories.py              # Entity factories (user, team, project, idea, post, etc.)
├── frontend/
│   ├── unit/
│   │   ├── components/
│   │   │   ├── auth.test.tsx
│   │   │   ├── project-wizard.test.tsx
│   │   │   ├── ideas-board.test.tsx
│   │   │   ├── post-review.test.tsx
│   │   │   ├── schedule-calendar.test.tsx
│   │   │   └── analytics.test.tsx
│   │   └── hooks/
│   │       ├── usePoll.test.ts
│   │       └── api-client.test.ts
│   └── e2e/
│       ├── auth.spec.ts
│       ├── project-wizard.spec.ts
│       ├── ai-workflow.spec.ts
│       ├── scheduling.spec.ts
│       ├── integrations.spec.ts
│       └── analytics.spec.ts
├── pytest.ini
├── playwright.config.ts
├── package.json                     # For Playwright + frontend tests
├── tsconfig.json
└── requirements.txt                 # pytest, moto, httpx, etc.
```

---

## Environment Variables

```env
# Test environment flag
ENV=test

# DynamoDB (moto mocks these — no real AWS needed for unit/integration tests)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
DYNAMODB_ENDPOINT_URL=              # empty = use moto mock (no endpoint)
DYNAMODB_MAIN_TABLE=ai-marketing-test-main
DYNAMODB_CONTENT_TABLE=ai-marketing-test-content
DYNAMODB_SCHEDULE_TABLE=ai-marketing-test-schedule
DYNAMODB_ANALYTICS_TABLE=ai-marketing-test-analytics
DYNAMODB_ACCOUNTS_TABLE=ai-marketing-test-accounts

# Auth
JWT_SECRET=test-secret-that-is-at-least-32-characters-long
JWT_ALGORITHM=HS256

# Set to "true" to use real Claude API in AI behavior tests (costs credits — CI: always false)
USE_REAL_CLAUDE=false
USE_REAL_HIGGSFIELD=false

# Backend + frontend URLs for E2E
BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000
```

---

## Quick Start

```bash
# Backend tests (no external services needed — moto mocks DynamoDB)
pip install -r requirements.txt
pytest backend/ -v

# Frontend unit tests
npm install
npm run test:unit

# E2E tests (requires backend + frontend running locally)
npx playwright install
npm run test:e2e
```

---

## Key Testing Conventions

- **Unit tests** mock all external services: Claude API (fixture JSON), Higgsfield, Meta, TikTok, storage. `moto` auto-mocks all `boto3` DynamoDB calls.
- **Integration tests** use `moto` for DynamoDB (no real AWS), but test the full HTTP request → route handler → DB path via `httpx.AsyncClient(app=app)`.
- **AI fixture responses** are real Claude API responses captured once and stored as JSON files. Never hit the real API in CI.
- **Factories** create consistent test data without duplication. Each test creates its own data and cleans up via `moto`'s context manager (tables are recreated per test).
- **E2E tests** run against locally served backend + frontend, with `httpretty` or `respx` intercepting external API calls (Meta, TikTok, Higgsfield).

---

## TODO LIST

---

### PHASE 0 — Test Infrastructure Setup

#### 0.1 Backend Test Setup
- [x] `requirements.txt` — pytest, pytest-asyncio, pytest-cov, httpx, moto[dynamodb], respx, freezegun
- [x] `pytest.ini` — `asyncio_mode = auto`, `testpaths = backend`, `filterwarnings = ignore::DeprecationWarning`
- [x] Configure `pytest-cov` with 80% coverage target for `app/services/` and `app/api/` (see `.coveragerc`)

#### 0.2 Backend Fixtures (`backend/conftest.py`)
- [x] `dynamodb` fixture (function-scoped): `@mock_aws` context + all 5 tables created via `_create_tables()`
- [x] `app_client` fixture: `fastapi.testclient.TestClient(app)` with moto DynamoDB patched in (patches `db_module.dynamodb`)
- [x] `auth_headers` fixture: returns `{ "X-User-Id": ..., "X-Team-Id": ... }` (dev bypass)
- [x] `test_user_id` / `test_team_id` fixtures — unique UUIDs per test
- [x] `mock_anthropic` fixture: monkeypatches `anthropic.Anthropic` with a stub returning canned text
- [x] `mock_meta_api` fixture: `respx.mock` intercepting `https://graph.facebook.com/`
- [x] `mock_tiktok_api` fixture: `respx.mock` intercepting the TikTok **Business** API (`https://business-api.tiktok.com/`, used by the ads connector); the organic-publishing host `open.tiktokapis.com` has no backend code yet
- [x] `mock_google_ads_api` / `mock_reddit_api` fixtures (extra: the ads connectors for these platforms exist)
- [ ] `mock_higgsfield` fixture: `respx.mock` intercepting Higgsfield API *(blocked: no Higgsfield client exists in the backend yet, so there is no API surface/URL to mock)*

#### 0.3 Entity Factories (`backend/fixtures/factories.py`)
- [x] `make_user(**overrides)` → returns user profile dict (pk=USER#{id}, sk=PROFILE)
- [x] `make_email_index_item(user_id, email)` → lookup item (pk=EMAIL#{email}, sk=USER)
- [x] `make_team(**overrides)` → returns team profile dict (pk=TEAM#{id}, sk=PROFILE)
- [x] `make_team_member(team_id, user_id)` → team member item
- [x] `make_project(**overrides)` → project item (pk=TEAM#{team_id}, sk=PROJECT#{id})
- [x] `make_post(**overrides)` → content table post item
- [x] `make_social_account(**overrides)` → accounts table item
- [x] `make_audience_analysis(project_id)` → analysis result dict
- [x] `make_idea(project_id, status="approved")` → idea dict
- [x] `make_schedule(post_id, team_id, scheduled_at=None)` → schedule item
- [x] `make_media(project_id)` → media record dict

> **Note:** Factories return plain dicts matching the DynamoDB schema. Use `dynamodb.Table(TABLE).put_item(Item=make_user(...))` in tests to insert.

#### 0.4 Claude API Fixtures
- [ ] `scripts/capture_fixtures.py` — run real Claude API once, save responses to `backend/fixtures/claude_responses/`
- [ ] `backend/fixtures/claude_responses/audience_analysis.json`
- [ ] `backend/fixtures/claude_responses/idea_generation.json`
- [ ] `backend/fixtures/claude_responses/post_generation.json`
- [ ] `backend/fixtures/claude_responses/schedule_recommendation.json`
- [ ] `backend/fixtures/claude_responses/marketing_plan_30.json`
- [ ] `backend/fixtures/claude_responses/performance_insights.json`

#### 0.5 Frontend / E2E Test Setup
- [x] `package.json` — `@playwright/test` 1.44, TypeScript
- [x] `playwright.config.ts` — baseURL env var, 4 browser projects (chromium/firefox/webkit/mobile-chrome), 2 retries in CI, auto-starts Next.js dev server locally
- [x] `tsconfig.json` — strict, commonjs, covers `frontend/**/*.ts`
- [x] `frontend/e2e/home.spec.ts` — smoke test: heading + page title
- [ ] `__mocks__/api-client.ts` — Jest mock of `lib/api-client.ts` for frontend unit tests
- [ ] `frontend/unit/` Jest config (separate `jest.config.js` in tests repo, or rely on frontend repo's Jest)

#### 0.6 Smoke Tests
- [x] `backend/unit/test_health.py` — `GET /health`, `GET /ready`, `GET /` all pass

---

### PHASE 1 — Backend Unit Tests

#### 1.1 JWT & Auth (`backend/unit/test_jwt.py`)
- [x] `create_access_token` — returns string JWT with correct `user_id`, `team_id`, `email`, `token_type=access` claims
- [x] `create_refresh_token` — includes `jti` claim; decodes correctly when not revoked
- [x] `create_temp_token` — token_type=temp; decoded by `decode_temp_token`
- [x] `decode_access_token` — returns None for refresh/temp tokens (wrong type)
- [x] `decode_access_token` — returns None for garbage/empty input
- [x] `decode_refresh_token` — returns None when JTI is revoked in DynamoDB
- [x] `decode_temp_token` — returns None for access tokens (wrong type)

#### 1.2 Password Hashing (`backend/unit/test_password.py`)
- [x] `hash_password` — returns Argon2 hash (not plain text)
- [x] `hash_password` — different salts each call (two hashes of same password differ)
- [x] `verify_password` — returns True for correct password against its hash
- [x] `verify_password` — returns False for wrong password
- [x] `verify_password` — returns False for empty password
- [x] `verify_password` — returns False for garbage hash (no exception)

#### 1.3 DynamoDB Helpers (`backend/unit/test_db_helpers.py`)
- [x] `put_item` + `get_item` — round-trip: put item, retrieve by pk/sk
- [x] `get_item` — returns None for non-existent key (no exception)
- [x] `put_item` — overwrites existing item
- [x] `update_item` — updates only specified fields, preserves others
- [x] `update_item` — returns current item when updates dict is empty
- [x] `delete_item` — removes item; subsequent `get_item` returns None
- [x] `delete_item` — idempotent (succeeds on nonexistent key)
- [x] `query_by_pk_sk_begins` — returns items matching pk + sk prefix, excludes others
- [x] `query_by_pk_sk_begins` — returns empty list + None cursor for missing pk
- [x] `query_gsi` — queries email-index, returns matching items
- [x] `query_gsi` — returns empty list for no matches

#### 1.4 AudienceAnalysisService (`backend/unit/test_audience_analysis.py`)
- [ ] Prompt includes `product_name`, `target_interests`, `platforms`, `campaign_goal`
- [ ] Prompt includes scraped website data when provided
- [ ] Parses Claude fixture response into correct `AudienceAnalysis` shape (all required keys present)
- [ ] Saves result to DynamoDB (`save_audience_analysis` called with project_id)
- [ ] Deducts exactly 5 credits via `deduct_credits`
- [ ] Raises `AppError(402)` when credit balance < 5
- [ ] Handles `anthropic.APIError` — raises `AppError(503)` with `CLAUDE_ERROR` code
- [ ] Handles malformed JSON from Claude — raises `AppError(500)` with `PARSE_ERROR` code

#### 1.5 IdeaGenerationService (`backend/unit/test_idea_generation.py`)
- [ ] Raises `AppError(400)` when audience analysis does not exist
- [ ] Generates between 5 and 15 ideas from fixture response
- [ ] Each idea has all required keys: `title`, `concept`, `hook`, `platforms`, `content_type`, `estimated_impact`, `tone`, `reasoning`
- [ ] `estimated_impact` is one of `low`, `medium`, `high` (no other values)
- [ ] All ideas saved with `status = "pending"`
- [ ] Deducts 5 credits on success
- [ ] Re-prompt for regeneration includes rejected idea titles in the prompt

#### 1.6 PostGenerationService (`backend/unit/test_post_generation.py`)
- [ ] Only processes ideas with `status = "approved"`
- [ ] Generates posts for each idea × each selected platform
- [ ] Instagram posts have 15–25 hashtags
- [ ] Facebook posts have 3–5 hashtags
- [ ] TikTok posts have 5–8 hashtags
- [ ] All captions are under platform character limits (IG ≤ 2200, FB ≤ 63000, TT ≤ 2200)
- [ ] Each post has non-empty `visual_brief`
- [ ] All posts saved with `status = "draft"`
- [ ] Deducts 3 credits per idea batch
- [ ] Caption regeneration saves to `caption_draft` only (not `caption_final`)
- [ ] Caption regeneration deducts 1 credit

#### 1.7 MediaService (`backend/unit/test_media_service.py`)
- [ ] Image generation: calls HiggsField provider by default
- [ ] Image generation: calls DALL-E provider when HiggsField raises
- [ ] Video generation: calls HiggsField video provider
- [ ] Video generation: falls back to Runway when HiggsField video raises
- [ ] Stores generated file in storage, creates `Media` record in DynamoDB
- [ ] Updates `Post.media_id` after successful generation
- [ ] Credit cost: 1 for image, 5 for 15s video, 15 for 60s video
- [ ] Raises `AppError(402)` when credit balance insufficient before calling provider

#### 1.8 ScheduleService (`backend/unit/test_schedule_service.py`)
- [ ] `create_schedule` rejects `scheduled_at` in the past — raises `AppError(400)`
- [ ] `create_schedule` — no conflict: schedules successfully
- [ ] `conflict_check` — returns warning when same platform scheduled within 3h
- [ ] `conflict_check` — no warning when different platform within 3h
- [ ] `bulk_schedule` — distributes N posts evenly across date range
- [ ] `bulk_schedule` — raises `AppError(400)` if date range too narrow for post count
- [ ] `export_csv` — returns CSV bytes with correct headers: post_id, platform, caption_snippet, scheduled_at

#### 1.9 MetaPublisher (`backend/unit/test_meta_publisher.py`)
- [ ] `publish_feed_post` — calls `POST /{ig-user-id}/media` then `POST /media_publish`, returns post ID
- [ ] `publish_feed_post` — raises `PublishError` on 4xx response from Meta API
- [ ] `publish_reel` — uses resumable upload path for video files
- [ ] `publish_story` — sends `media_type=STORIES` in request
- [ ] Logs attempt to `PublishLog` on success
- [ ] Logs attempt to `PublishLog` on failure

#### 1.10 TikTokPublisher (`backend/unit/test_tiktok_publisher.py`)
- [ ] `publish_video` — calls init → chunk upload → finalize in correct order
- [ ] `publish_video` — polls status until `PUBLISH_COMPLETE`
- [ ] `publish_video` — raises `PublishError` after timeout (mock status never complete)
- [ ] Logs attempt to `PublishLog` on success and failure

#### 1.11 CreditsService (`backend/unit/test_credits_service.py`)
- [ ] `get_balance(team_id)` — returns credit balance from DynamoDB
- [ ] `deduct_credits(team_id, 5, "audience_analysis", "proj_123")` — reduces balance by 5, logs usage
- [ ] `deduct_credits` — raises `AppError(402)` if balance < cost
- [ ] `log_credit_usage` — writes to DynamoDB with all required fields

#### 1.12 ScraperService (`backend/unit/test_scraper_service.py`)
- [ ] Extracts title, meta description, h1, body text from valid HTML (use Playwright with local test HTML)
- [ ] Returns empty fields (not exception) for unreachable URL
- [ ] Raises timeout `AppError` after 10 seconds
- [ ] Strips `<script>` and `<style>` tags from extracted body text
- [ ] Does not follow redirects more than 3 hops

---

### PHASE 1 — Backend Integration Tests (Full HTTP)

All integration tests use `fastapi.testclient.TestClient(app)` + `@mock_aws` + factory fixtures.

#### 1.4 Auth Routes (`backend/integration/test_auth_routes.py`)
- [x] `POST /api/v1/auth/register/step-1` — unique email → 200, temp token in response
- [x] `POST /api/v1/auth/register/step-1` — duplicate email → 409
- [x] `POST /api/v1/auth/register/step-1` — invalid email format → 422
- [x] `POST /api/v1/auth/register/step-2` — valid temp token + full_name → 200
- [x] `POST /api/v1/auth/register/step-2` — invalid token → 401
- [x] `POST /api/v1/auth/register/step-3` — valid team name → 200, returns new tempToken + teamId
- [x] `POST /api/v1/auth/register/step-3` — invalid token → 401
- [x] `POST /api/v1/auth/register/step-4` — completes wizard → 200, access + refresh tokens
- [x] `POST /api/v1/auth/register/step-4` — invalid token → 401
- [x] `POST /api/v1/auth/login` — correct credentials → 200, access + refresh tokens
- [x] `POST /api/v1/auth/login` — wrong password → 401
- [x] `POST /api/v1/auth/login` — unknown email → 401
- [x] `POST /api/v1/auth/login` — unverified user → 403
- [x] `POST /api/v1/auth/refresh` — valid refresh token → 200, new token pair
- [x] `POST /api/v1/auth/refresh` — invalid/expired token → 401
- [x] `POST /api/v1/auth/refresh` — used token is revoked; using it again → 401
- [x] `POST /api/v1/auth/logout` — revokes JTI → 200 (idempotent on repeated calls)
- [x] `POST /api/v1/auth/forgot-password` — always 200 (prevents email enumeration)
- [x] `POST /api/v1/auth/reset-password` — valid token + new password → 200; new password works for login
- [x] `POST /api/v1/auth/reset-password` — invalid token → 401
- [x] `GET /api/v1/auth/me` — authenticated → 200, user data without password_hash
- [x] `GET /api/v1/auth/me` — unauthenticated → 401

#### 1.5 Project Routes (`backend/integration/test_project_routes.py`)
- [x] `POST /api/v1/projects` — authenticated → 200, project with status=draft
- [x] `POST /api/v1/projects` — invalid platform → 422
- [x] `POST /api/v1/projects` — no auth → 401
- [x] `POST /api/v1/projects` — with description → stored correctly
- [x] `GET /api/v1/projects` — returns only own team's projects
- [x] `GET /api/v1/projects` — empty list for new team
- [x] `GET /api/v1/projects?status=active` — returns only active projects
- [x] `GET /api/v1/projects` — unauthenticated → 401
- [x] `GET /api/v1/projects/{id}` — authenticated + owns project → 200
- [x] `GET /api/v1/projects/{id}` — non-existent → 404
- [x] `GET /api/v1/projects/{id}` — different team's project → 404
- [x] `PATCH /api/v1/projects/{id}` — updates name → 200
- [x] `PATCH /api/v1/projects/{id}` — empty body → 422
- [x] `PATCH /api/v1/projects/{id}` — invalid platform → 422
- [x] `PATCH /api/v1/projects/{id}` — not found → 404
- [x] `POST /api/v1/projects/{id}/activate` — draft → active
- [x] `POST /api/v1/projects/{id}/activate` — already active → 200 (idempotent)
- [x] `POST /api/v1/projects/{id}/activate` — not found → 404
- [x] `POST /api/v1/projects/{id}/archive` — active → archived
- [x] `POST /api/v1/projects/{id}/archive` — already archived → 200 (idempotent)
- [x] `POST /api/v1/projects/{id}/archive` — not found → 404

### PHASE 2 — Backend Integration Tests (Full HTTP)

#### 2.1 Analysis Routes (`backend/integration/test_analysis_routes.py`)
- [ ] `POST /api/v1/analysis/{id}/audience` — queues job → 202
- [ ] `POST /api/v1/analysis/{id}/audience` — no audience analysis prereq → no error (runs directly)
- [ ] `POST /api/v1/analysis/{id}/audience` — insufficient credits → 402
- [ ] `GET /api/v1/analysis/{id}/audience` — before analysis → 404
- [ ] `GET /api/v1/analysis/{id}/audience` — after analysis → 200, correct shape
- [ ] `PUT /api/v1/analysis/{id}/audience` — saves user overrides without losing other fields

#### 2.2 Idea Routes (`backend/integration/test_idea_routes.py`)

#### 2.3 Analysis Routes (`backend/integration/test_analysis_routes.py`)
- [ ] `POST /api/v1/analysis/{id}/audience` — queues job → 202
- [ ] `POST /api/v1/analysis/{id}/audience` — no audience analysis prereq → no error (runs directly)
- [ ] `POST /api/v1/analysis/{id}/audience` — insufficient credits → 402
- [ ] `GET /api/v1/analysis/{id}/audience` — before analysis → 404
- [ ] `GET /api/v1/analysis/{id}/audience` — after analysis → 200, correct shape
- [ ] `PUT /api/v1/analysis/{id}/audience` — saves user overrides without losing other fields

#### 2.3 Idea Routes (`backend/integration/test_idea_routes.py`)
- [ ] `POST /api/v1/ideas/{id}/generate` — no audience analysis → 400
- [ ] `POST /api/v1/ideas/{id}/generate` — audience analysis exists → 202
- [ ] `POST /api/v1/ideas/{id}/generate` — insufficient credits → 402
- [ ] `GET /api/v1/ideas/{id}` — returns empty list before generation
- [ ] `GET /api/v1/ideas/{id}?status=approved` — returns only approved ideas
- [ ] `PUT /api/v1/ideas/{id}/{idea_id}` — approve → status=approved
- [ ] `PUT /api/v1/ideas/{id}/{idea_id}` — reject → status=rejected
- [ ] `PUT /api/v1/ideas/{id}/{idea_id}` — update title+concept → saved correctly
- [ ] `PUT /api/v1/ideas/{id}/{idea_id}` — different team's idea → 403
- [ ] `POST /api/v1/ideas/{id}/regenerate` — queues new generation → 202

#### 2.5 Post Routes (`backend/integration/test_post_routes.py`)
- [ ] `POST /api/v1/posts/{id}/generate` — no approved ideas → 400
- [ ] `POST /api/v1/posts/{id}/generate` — approved ideas exist → 202
- [ ] `POST /api/v1/posts/{id}/generate` — body missing `idea_ids` → 422
- [ ] `GET /api/v1/posts/{id}` — empty before generation
- [ ] `GET /api/v1/posts/{id}?platform=instagram` — returns only IG posts
- [ ] `GET /api/v1/posts/{id}?status=approved` — returns only approved posts
- [ ] `PUT /api/v1/posts/{id}/{post_id}` — updates caption, hashtags, cta
- [ ] `PUT /api/v1/posts/{id}/{post_id}` — caption over platform limit → 200 with `over_limit_warning: true`
- [ ] `POST /api/v1/posts/{id}/{post_id}/approve` → status=approved
- [ ] `POST /api/v1/posts/{id}/{post_id}/reject` → status=rejected
- [ ] `POST /api/v1/posts/{id}/{post_id}/regenerate-caption` → 202 (costs 1 credit)
- [ ] `POST /api/v1/posts/{id}/bulk-approve` — updates all specified post statuses

#### 2.6 Media Routes (`backend/integration/test_media_routes.py`)
- [ ] `POST /api/v1/media/{post_id}/generate-image` — queues job → 202
- [ ] `POST /api/v1/media/{post_id}/generate-image` — insufficient credits → 402
- [ ] `POST /api/v1/media/{post_id}/generate-video` — queues job → 202
- [ ] `GET /api/v1/media/{post_id}/status` — returns `queued` immediately after request
- [ ] `POST /api/v1/media/{post_id}/upload` — valid JPG → 201, Media record created
- [ ] `POST /api/v1/media/{post_id}/upload` — unsupported MIME → 415
- [ ] `POST /api/v1/media/{post_id}/upload` — file over 20MB limit → 413
- [ ] `GET /api/v1/media/library/{project_id}` — returns all media for project
- [ ] `DELETE /api/v1/media/{media_id}` — removes DynamoDB record
- [ ] `POST /api/v1/media/{post_id}/assign/{media_id}` — links existing media to post

#### 2.7 Schedule Routes (`backend/integration/test_schedule_routes.py`)
- [ ] `POST /api/v1/schedule/{post_id}` — future date → 201, post status=scheduled
- [ ] `POST /api/v1/schedule/{post_id}` — past date → 400
- [ ] `POST /api/v1/schedule/{post_id}` — same platform within 3h → 200 + `conflict_warning`
- [ ] `GET /api/v1/schedule/calendar` — returns posts in date range
- [ ] `GET /api/v1/schedule/calendar` — does not return other team's posts
- [ ] `PUT /api/v1/schedule/{post_id}` — updates `scheduled_at`
- [ ] `DELETE /api/v1/schedule/{post_id}` — removes schedule, post reverts to approved
- [ ] `POST /api/v1/schedule/{project_id}/bulk` — creates N schedules across date range
- [ ] `GET /api/v1/schedule/{project_id}/export` — returns CSV with correct `Content-Type: text/csv`

#### 2.8 Integration Routes (`backend/integration/test_integration_routes.py`)
- [ ] `GET /api/v1/integrations/meta/connect` — returns 302 redirect to Meta OAuth URL
- [ ] `GET /api/v1/integrations/meta/callback` — valid code → stores ConnectedAccount → redirect to settings
- [ ] `GET /api/v1/integrations/meta/callback` — error param → 400 `OAUTH_ERROR`
- [ ] `GET /api/v1/integrations/tiktok/connect` — returns 302 redirect to TikTok OAuth URL
- [ ] `GET /api/v1/integrations/tiktok/callback` — valid code → stores ConnectedAccount
- [ ] `GET /api/v1/integrations/accounts` — lists team's connected accounts only
- [ ] `DELETE /api/v1/integrations/accounts/{id}` — removes account
- [ ] `GET /api/v1/integrations/accounts/{id}/health` — not expired → `{ status: "valid" }`
- [ ] `GET /api/v1/integrations/accounts/{id}/health` — expired token → `{ status: "expired" }`

#### 2.9 Analytics Routes (`backend/integration/test_analytics_routes.py`)
- [ ] `GET /api/v1/analytics/{post_id}/metrics` — no metrics yet → 404
- [ ] `GET /api/v1/analytics/{post_id}/metrics` — after upsert → 200, correct shape
- [ ] `GET /api/v1/analytics/{project_id}/summary` — returns aggregate reach, best post
- [ ] `GET /api/v1/analytics/{project_id}/insights` — no insights yet → 404
- [ ] `POST /api/v1/analytics/{project_id}/insights/generate` — queues job → 202
- [ ] `POST /api/v1/analytics/{project_id}/insights/generate` — insufficient credits (1) → 402

---

### PHASE 3 — Background Worker / Publishing Tests

#### 3.1 Publishing Worker (`backend/unit/test_publishing_worker.py`)
- [ ] Finds posts with `scheduled_at <= now + 5min` and `status=scheduled`
- [ ] Skips posts with no `ConnectedAccount` for the platform
- [ ] Calls `MetaPublisher.publish_feed_post` for Instagram feed posts
- [ ] Calls `MetaPublisher.publish_reel` for Reel post type
- [ ] Calls `TikTokPublisher.publish_video` for TikTok posts
- [ ] On success: sets `Post.status=published`, stores `platform_post_id`, `published_at`
- [ ] On failure (1st attempt): logs to `PublishLog`, retries
- [ ] On 3 consecutive failures: sets `Post.status=failed`, calls `send_publish_failure_email`
- [ ] Uses `freezegun` to freeze time: posts scheduled 2 min in the future are not picked up, 4 min are
- [ ] Token refresh: calls `update_token` when `token_expires_at < now + 7 days`

#### 3.2 Metrics Worker (`backend/unit/test_metrics_worker.py`)
- [ ] Fetches metrics only for posts with `status=published`
- [ ] Skips posts with no `platform_post_id`
- [ ] Calls Meta Insights API with correct post ID and access token
- [ ] Calls TikTok Analytics API with correct publish ID
- [ ] Upserts `PostMetrics` record (creates if new, updates if exists)
- [ ] Handles Meta API error (expired token) — logs error, skips post, continues

---

### PHASE 4 — Frontend Unit Tests (Jest + React Testing Library)

#### 4.1 Auth Page (`frontend/unit/components/auth.test.tsx`)
- [ ] Renders login form by default when `?mode=login` in URL
- [ ] Renders registration wizard step 1 when `?mode=register`
- [ ] "Continue" in step 1 disabled while email/password fields are empty
- [ ] "Continue" in step 1 calls `registerStep1()` mock with correct args
- [ ] Inline error shown when `registerStep1` returns error response
- [ ] Step progress indicator shows correct step number
- [ ] "Already have an account?" link switches to login view
- [ ] Login: calls `login()` mock, stores tokens in localStorage on success
- [ ] Login: shows inline error on 401 response

#### 4.2 API Client (`frontend/unit/hooks/api-client.test.ts`)
- [ ] `apiCall` attaches `Authorization: Bearer {token}` header from localStorage
- [ ] On 401 → calls `/api/v1/auth/refresh` → stores new access token → retries original request
- [ ] On refresh 401 → clears localStorage → does NOT retry indefinitely
- [ ] Concurrent 401s from multiple calls share one refresh request (not multiple)
- [ ] On 402 → returns `{ creditError: true }` (does not throw)
- [ ] Unwraps `{ status: "success", data: {...} }` → returns `data` only
- [ ] Non-JSON response → throws `Error("Invalid response from server")`

#### 4.3 Project Wizard (`frontend/unit/components/project-wizard.test.tsx`)
- [ ] Step 1 renders product name, description, website URL inputs
- [ ] "Analyze Website" button calls `analyzeWebsite()` mock, shows loading, populates fields
- [ ] Tag input for interests: type + Enter adds tag, click × removes tag
- [ ] Platform checkboxes: selecting Instagram sets platforms state correctly
- [ ] "Continue" calls `createProject()` on step 1 completion
- [ ] "Back" from step 2 returns to step 1 with step 1 data preserved
- [ ] Step 6 "Finish" calls `activateProject()` then navigates to project page

#### 4.4 Ideas Board (`frontend/unit/components/ideas-board.test.tsx`)
- [ ] Renders idea cards from mocked `listIdeas()` response
- [ ] Click "Approve ✓" → calls `approveIdea()` → card border turns green
- [ ] Click "Reject ✗" → calls `rejectIdea()` → card becomes muted
- [ ] "Proceed to Post Generation" is disabled when < 3 ideas approved
- [ ] "Proceed" enables after 3rd approval
- [ ] "See reasoning" expand shows full reasoning text
- [ ] Inline title edit: click title → input renders → blur saves via `updateIdeaContent()`

#### 4.5 Post Review Board (`frontend/unit/components/post-review.test.tsx`)
- [ ] Posts grouped under idea section headers
- [ ] Caption shows first 120 chars, "Expand" shows full caption
- [ ] Character counter shows correct count and turns red when over limit
- [ ] "Approve" button calls `approvePost()` mock
- [ ] "Bulk Approve" calls `bulkApprove()` with all post IDs in section
- [ ] Platform filter tab "Instagram" hides non-Instagram post cards
- [ ] "Regenerate caption" shows loading state, replaces caption text on success

#### 4.6 Schedule Calendar (`frontend/unit/components/schedule-calendar.test.tsx`)
- [ ] Renders current week by default
- [ ] Scheduled posts appear on correct day as chips
- [ ] Platform chips have correct color (purple for Instagram)
- [ ] Conflict warning icon shown for same-platform posts within 3h
- [ ] Click on chip opens side panel with post details
- [ ] "Export CSV" button calls `exportScheduleCSV()` and triggers browser download

#### 4.7 usePoll Hook (`frontend/unit/hooks/usePoll.test.ts`)
- [ ] Calls `fetchFn` on mount
- [ ] Re-calls `fetchFn` every `intervalMs` milliseconds
- [ ] Stops polling when `stopCondition(result)` returns true
- [ ] Cleans up interval on unmount (no memory leaks)
- [ ] Does not call `fetchFn` after unmount

---

### PHASE 5 — End-to-End Tests (Playwright)

All E2E tests use MSW (Mock Service Worker) in the browser to intercept API calls, except auth which hits the real test backend.

#### 5.1 Auth Flow (`frontend/e2e/auth.spec.ts`)
- [ ] New user completes 4-step registration and lands on `/dashboard`
- [ ] Existing user logs in and sees projects list
- [ ] Unauthenticated user accessing `/dashboard` is redirected to `/auth?mode=login`
- [ ] User signs out → localStorage cleared → redirect to sign-in

#### 5.2 Project Creation Wizard (`frontend/e2e/project-wizard.spec.ts`)
- [ ] Complete all 6 steps with valid data → project appears on dashboard
- [ ] Submit step 1 with empty product name → inline validation error shown
- [ ] Navigate back from step 3 → step 2 data preserved
- [ ] Website URL analysis: enter URL → click Analyze → fields auto-populated
- [ ] Step 5 file upload: drop a JPG → thumbnail appears

#### 5.3 AI Workflow — Happy Path (`frontend/e2e/ai-workflow.spec.ts`)
- [ ] "Analyze Audience" → spinner → audience report rendered with all sections
- [ ] Edit messaging angle inline → "Save" → data persists on page reload
- [ ] "Generate Ideas" → 10 idea cards appear → approve 3 → "Proceed" button enabled
- [ ] "Generate Posts" for 2 ideas → posts stream in live (one by one) → all visible
- [ ] Approve all posts → "Proceed to Media" CTA active
- [ ] "Generate Image" for one post → loading → image thumbnail appears
- [ ] Schedule one post → post chip appears on calendar on correct date

#### 5.4 Scheduling & Calendar (`frontend/e2e/scheduling.spec.ts`)
- [ ] Drag post from Approved column to Scheduled column → date picker opens → schedule created
- [ ] Conflict warning shown when scheduling Instagram within 3h of existing IG post
- [ ] "Bulk Schedule" modal: select 3 posts, set 2-week range, confirm → 3 chips on calendar
- [ ] Drag chip to new date → post rescheduled (API call made, chip moves)
- [ ] "Export CSV" → file download triggered

#### 5.5 Integrations (`frontend/e2e/integrations.spec.ts`)
- [ ] Settings → Integrations page shows "Connect" for Meta and TikTok
- [ ] "Connect Instagram" → OAuth redirect initiated (verify URL contains correct params)
- [ ] Mock OAuth callback redirect → account card shows "Connected" badge + account name
- [ ] "Disconnect" → confirm dialog → account removed from list

#### 5.6 Analytics (`frontend/e2e/analytics.spec.ts`)
- [ ] Analytics page shows empty state when no published posts
- [ ] With mocked metrics: summary cards show non-zero values, charts render
- [ ] Date range "Last 7 days" → "Last 30 days" → chart data updates
- [ ] "Generate New Insight" → loading → insight text block appears

---

### PHASE 6 — AI Behavior Tests (Real Claude API — Weekly / Manual)

Gated by `USE_REAL_CLAUDE=true` env var. Run manually or in a scheduled weekly CI job. Each test calls the real Claude API — costs credits.

- [ ] Audience analysis always returns all required top-level keys
- [ ] `estimated_impact` in ideas is always exactly `"low"`, `"medium"`, or `"high"`
- [ ] At least 5 ideas generated from a complete project brief
- [ ] Post captions for Instagram never exceed 2,200 characters
- [ ] Post hashtags for Instagram always between 15 and 25
- [ ] Post hashtags for Facebook always between 3 and 5
- [ ] Post hashtags for TikTok always between 5 and 8
- [ ] Visual briefs always describe an image/video scene (contain location/subject words, not meta instructions)
- [ ] Schedule recommendation always returns times in `HH:MM` format
- [ ] Marketing plan always contains all 4 sections (content calendar, platform strategy, KPI targets, budget)
- [ ] Insight report contains at least one actionable sentence (verb + recommendation pattern)
- [ ] TikTok posts never include `post_type = "carousel"` (TikTok doesn't support it)
- [ ] Regenerated captions differ from original by at least 20% (use Levenshtein distance check)
- [ ] All generated captions include a non-empty `cta` field

---

### Non-Feature / Infrastructure Tasks

- [x] `requirements.txt`, `pytest.ini`, `package.json`, `playwright.config.ts`, `tsconfig.json` — all bootstrapped
- [ ] Set up GitHub Actions: on PR → run `pytest backend/unit` + `pytest backend/integration`
- [ ] Set up nightly GitHub Actions: run E2E tests + AI behavior tests (`USE_REAL_CLAUDE=true`)
- [x] Configure pytest-cov: fail CI if coverage drops below 80% for `app/services/` and `app/api/` (`.coveragerc`; run `pytest --cov --cov-config=.coveragerc`)
- [ ] Configure Playwright HTML reporter: publish test report as CI artifact
- [ ] `scripts/capture_fixtures.py` — capture real Claude responses and save to fixture files
- [ ] `CONTRIBUTING.md` — document how to run each test suite locally
