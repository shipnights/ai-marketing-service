# Ads, Unified Stats & Billing — Platform Logic & Build Plan
**Created:** 2026-06-22
**Status:** Phase A (read-only ads + billing foundation) implemented in this codebase
**Extends:** [MARKETING_PLATFORM_LOGIC.md](MARKETING_PLATFORM_LOGIC.md)

This document covers the **paid advertising** layer (Google, Reddit, Meta, TikTok),
the **unified cross-platform stats** engine, and the **Stripe billing** system —
how they work, the data model, the API, and the phased rollout.

> The original platform plan was *organic* social (create content → publish → engagement).
> This layer adds *paid* ads: connect ad accounts, pull spend/performance, attribute
> revenue (ROAS), and bill customers for the SaaS.

---

## 1. SCOPE & DECISIONS

| Decision | Choice |
|----------|--------|
| Deliverable | Full working implementation (not stubs) |
| Ads depth | **Read-first, then management** — Phase A = connect + pull stats; management is a later phase |
| Stripe role | **Both** — SaaS subscription billing + AI credits, *and* revenue/ROAS tracking |
| Platforms | Google Ads, Meta Ads, TikTok Ads, Reddit Ads (unified behind one interface) |

### Actual stack (corrects the older plan)
The implemented backend is **FastAPI + DynamoDB single-table design**, not Postgres/Prisma.
The frontend is **Next.js (App Router) + TypeScript + CSS Modules**. All new work follows
those real conventions.

---

## 2. ARCHITECTURE OVERVIEW

```
                         ┌──────────────────────────────────────────┐
                         │              Frontend (Next.js)           │
                         │  /dashboard/ads     unified stats dash    │
                         │  /dashboard/integrations  connections     │
                         │  /dashboard/billing  plans · credits      │
                         └───────────────────┬──────────────────────┘
                                             │ apiCall<T>  (Bearer JWT)
                         ┌───────────────────▼──────────────────────┐
                         │            FastAPI  /api/v1               │
                         │  integrations · ads · billing            │
                         └───┬───────────────┬──────────────────┬───┘
            ┌────────────────▼──┐   ┌─────────▼─────────┐  ┌─────▼────────┐
            │  Ad Connectors    │   │  Ads Sync Worker  │  │ Stripe       │
            │  (provider absn.) │   │  pull → normalize │  │ service +    │
            │  google·meta·     │   │  → store          │  │ webhooks     │
            │  tiktok·reddit    │   └─────────┬─────────┘  └─────┬────────┘
            └─────────┬─────────┘             │                  │
                      │   OAuth + Insights    │ normalized       │ subscription
                      ▼                       ▼ AdInsight        ▼ + credits
            ┌──────────────────┐   ┌────────────────────┐  ┌──────────────┐
            │ Google/Meta/     │   │ DynamoDB ANALYTICS  │  │ DynamoDB     │
            │ TikTok/Reddit    │   │ ADSTATS#{team}      │  │ MAIN TEAM#   │
            │ Ads APIs         │   │                     │  │ BILLING/CREDIT│
            └──────────────────┘   └────────────────────┘  └──────────────┘
            tokens encrypted at rest in ACCOUNTS table (ADCONN#{platform})
```

---

## 3. DATA MODEL (no new tables — reuses the existing 5)

Everything fits the existing single-table design. New **item types** only:

### ACCOUNTS table — connections & ad accounts
| pk | sk | meaning |
|----|----|---------|
| `TEAM#{team_id}` | `ADCONN#{platform}` | OAuth connection — **encrypted** access/refresh tokens, status, scope |
| `TEAM#{team_id}` | `ADACCT#{platform}#{account_id}` | a discovered ad account (name, currency, `enabled`, `last_synced_at`) |

`platform` ∈ `google_ads · meta_ads · tiktok_ads · reddit_ads`.

### ANALYTICS table — ad insights
| pk | sk | meaning |
|----|----|---------|
| `ADSTATS#{team_id}` | `{date}#{platform}#{account_id}#{level}#{entity_id}` | one normalized insight row |

Date-first sort key → a single `BETWEEN(since, until)` query returns the whole window,
all platforms, in date order. Re-syncing a day overwrites the same key (idempotent).
Only **raw summable metrics** are stored; ratios are recomputed on read.

### MAIN table — billing
| pk | sk | meaning |
|----|----|---------|
| `TEAM#{team_id}` | `BILLING` | plan, status, interval, period end, `stripe_customer_id` |
| `TEAM#{team_id}` | `CREDIT#{ts}#{seq}` | append-only AI-credit ledger entry |
| `STRIPECUST#{customer_id}` | `TEAM` | reverse lookup (customer → team) for webhooks |
| `STRIPEEVENT#{event_id}` | `SEEN` | webhook idempotency marker |

The spendable balance stays on `TEAM#/PROFILE.ai_credits` (the field the app already reads);
`adjust_credits()` mutates it atomically (DynamoDB `ADD`) and writes a ledger row.

---

## 4. AD CONNECTORS (provider abstraction)

One interface, four implementations. Routes and the sync worker never touch a
platform's quirks.

`app/services/ads/base.py` → `AdConnector` (ABC):
```
authorize_url(state, redirect_uri)   → consent URL
exchange_code(code, redirect_uri)    → TokenBundle (access/refresh/expiry)
refresh(refresh_token)               → TokenBundle
list_ad_accounts(token)              → [AdAccountInfo]
fetch_campaigns(token, account_id)   → [Campaign]
fetch_insights(token, account_id, since, until, level) → [AdInsight]  (per campaign per day)
```

| Platform | Connector | API | Auth model | Notes |
|----------|-----------|-----|------------|-------|
| Google Ads | `google.py` | Ads API v17 REST (`searchStream` + GAQL) | OAuth2 + **developer token**; optional `login-customer-id` for MCC | cost in micros ÷ 1e6 |
| Meta Ads | `meta.py` | Graph Marketing API v21 | OAuth2, **long-lived** token (60d), no refresh — re-exchanged via `fb_exchange_token` | conversions parsed from `actions`/`action_values` |
| TikTok Ads | `tiktok.py` | Business API v1.3 | OAuth2, long-lived, **no refresh** (reconnect) | `{code,data}` envelope; `/report/integrated/get/` |
| Reddit Ads | `reddit.py` | Ads API v3 | OAuth2 + refresh | report endpoint; spend micros heuristic |

**Normalized metrics** (`schema.py`) — every connector returns these raw fields; the
rest is derived:

```
raw (summable):  spend · impressions · clicks · conversions ·
                 conversion_value · reach · video_views
derived (recomputed on read, never averaged):
   CTR = clicks/impressions      CPC = spend/clicks       CPM = spend/impr×1000
   CPA = spend/conversions       CVR = conversions/clicks  ROAS = revenue/spend
```

Because ratios are recomputed from summed raw totals, they aggregate correctly across
any mix of platforms and dates.

### OAuth flow
1. User clicks **Connect** → `GET /integrations/{platform}/connect` returns the consent URL.
   `state` is a **signed JWT** (15-min TTL) carrying `team_id`+`user_id` (the callback has no auth header).
2. Platform redirects to `GET /integrations/{platform}/callback?code=&state=`.
3. Server verifies `state`, exchanges `code`→tokens, **encrypts** and stores them, and
   immediately discovers ad accounts. Redirects back to `/dashboard/integrations?status=…`.
4. Tokens are refreshed automatically before each sync when expired; auth failures flip
   the connection to `error` so the UI prompts a reconnect.

---

## 5. UNIFIED STATS ENGINE

`app/services/ads/sync.py` pulls insights for every **enabled** ad account over a
look-back window (default 30 days), normalizes, and stores them. `db/ad_stats.py`
serves three dashboard shapes:

- **`summary(team, since, until)`** → grand totals, per-platform breakdown, daily time series.
- **`top_campaigns(...)`** → campaigns aggregated across the window, ranked.
- **`query_insights(...)`** → raw filterable rows (platform / account / level) for tables & charts.

Sync is **idempotent** and safe to re-run; a scheduled worker calls the same `sync_team()`.

---

## 6. STRIPE BILLING (both roles)

### Role 1 — SaaS subscriptions + AI credits
Plans (`services/billing/plans.py`):

| Plan | Monthly | Annual/mo | Credits/mo | Ad accounts |
|------|---------|-----------|-----------|-------------|
| Solo | $49 | $39 | 50 | 3 |
| Team | $99 | $79 | 200 | 10 |
| Agency | $249 | $199 | 600 | unlimited |

Plus one-time credit packs (Starter/Growth/Pro/Agency = 25/100/300/1000 credits).

- **Checkout** is always hosted (Stripe Checkout) and initiated **server-side by plan key**
  — the client never passes a price id.
- **Webhooks are the source of truth** (`services/billing/webhooks.py`):
  `invoice.paid` grants the plan's monthly credits; `subscription.*` syncs plan/status;
  `checkout.session.completed` (credit packs) grants pack credits. All grants are
  **idempotent** via `claim_event(event_id)`.
- **Billing Portal** lets customers upgrade/cancel without us handling cards.

### Role 2 — Revenue / ROAS tracking
ROAS = `conversion_value / spend` is already computed from the ad platforms' own
attributed conversion value. **Phase C** adds *true* ROAS by ingesting the customer's
actual Stripe sales (via Stripe Connect / read access) and attributing revenue to
campaigns — closing the loop between ad spend and real money in.

---

## 7. API SURFACE (new endpoints, all under `/api/v1`)

### Integrations
```
GET    /integrations                              list platforms + connection state
GET    /integrations/{platform}/connect           → { authorize_url }
GET    /integrations/{platform}/callback           OAuth redirect target (no auth)
GET    /integrations/{platform}/accounts           discovered ad accounts
POST   /integrations/{platform}/accounts/refresh   re-discover accounts
PATCH  /integrations/{platform}/accounts/{id}?enabled=  toggle account in syncs
DELETE /integrations/{platform}                    disconnect (+ purge accounts)
```

### Ads (reporting)
```
GET  /ads/accounts                                 all connected ad accounts
GET  /ads/overview?since=&until=                   totals + per-platform + timeseries
GET  /ads/campaigns?since=&until=&sort_by=&limit=  top campaigns
GET  /ads/insights?since=&until=&platform=&account_id=&level=   raw rows
GET  /ads/{platform}/{account_id}/campaigns/live   live campaign list from platform
POST /ads/sync?platform=&lookback_days=            pull fresh insights
```

### Billing
```
GET  /billing/plans              catalog + stripe_configured + publishable_key
GET  /billing/subscription       current plan/status/credits
GET  /billing/credits            balance + ledger
POST /billing/checkout           { plan_key, interval } → { url }
POST /billing/credits/checkout   { pack_key } → { url }
POST /billing/portal             → { url }
POST /billing/webhook            Stripe events (signature-verified, no auth)
```

---

## 8. SECURITY

- **OAuth tokens encrypted at rest** (Fernet, `TOKEN_ENCRYPTION_KEY`). Decrypted only in
  memory at sync time. Dev falls back to a flagged passthrough; **production must set the key**.
- **OAuth `state` is a signed, expiring JWT** — prevents CSRF / connection hijack on callback.
- **Stripe webhook** verified by signature; **exempt `/billing/webhook` from any API-key guard**.
- **Credit grants are idempotent** (event-id dedupe) — webhook retries never double-grant.
- **Stripe keys come from the environment only** (`STRIPE_SECRET_KEY`); `.gitignore` blocks all `.env` files.

---

## 9. PHASED IMPLEMENTATION PLAN

### ✅ Phase A — Connections + Unified Stats + Billing foundation  *(implemented here)*
- [x] Connector abstraction + Google/Meta/TikTok/Reddit connectors (read paths)
- [x] OAuth connect/callback with signed state; encrypted token storage; account discovery
- [x] Normalized insight schema + aggregation; sync worker; idempotent storage
- [x] Reporting API (overview / campaigns / insights / sync)
- [x] Stripe billing: plans, checkout, portal, webhooks, credit ledger
- [x] Frontend: Ad Manager dashboard, Integrations manager, Billing page
- [x] Config, env, `.gitignore`, table-comment docs

### Phase B — Scheduled sync + alerts *(~1 week)*
- [ ] Scheduled worker (EventBridge/cron) calling `sync_team` for all teams nightly + hourly for active
- [ ] Token-health checker (refresh proactively; email/in-app reconnect prompts)
- [ ] Spend/anomaly alerts ("Google spend +40% vs 7-day avg", "budget 90% consumed")
- [ ] Per-account sync cursors + backfill (90/180-day history import on first connect)

### Phase C — True ROAS / revenue attribution *(~1.5 weeks)*
- [ ] Stripe Connect (read) to ingest customers' actual sales
- [ ] UTM / click-id capture (gclid, fbclid, ttclid) → attribution model (last-click first)
- [ ] Blended ROAS dashboard: ad spend vs real revenue, by campaign/platform
- [ ] Cohort/LTV view; CAC by channel

### Phase D — Ad campaign management (write) *(~2–3 weeks)*
- [ ] Flip `supports_management` on; add create/pause/resume + budget edit to each connector
- [ ] Guardrails: spend caps, approval workflow, dry-run, audit log of every mutation
- [ ] "Boost this post" → create an ad from an approved organic post (ties into the content engine)
- [ ] AI budget recommendations (shift spend toward best ROAS campaigns)

### Phase E — AI ads intelligence *(~2 weeks)*
- [ ] Claude weekly ad report: what's working, what to cut, next actions
- [ ] AI ad copy + creative variants per platform (reuse media-gen pipeline)
- [ ] Predicted performance before launch; auto A/B of ad creatives

---

## 10. IMPLEMENTED vs. NEEDS-CREDENTIALS

**Works now (no external setup):** data model, connector framework, sync/aggregation logic,
billing logic, all routes, frontend. Verified: all modules import; metric math correct;
4 connectors register; 6 ads + 7 billing + 7 integration routes mount.

**Needs real credentials to exercise end-to-end:** each ad platform's app
(client id/secret, Google developer token, TikTok Business app) and Stripe keys + Price IDs
+ webhook secret. Set them in `.env` (see `.env.example`), set `TOKEN_ENCRYPTION_KEY`, then
run `python scripts/create_tables.py` and connect a platform from `/dashboard/integrations`.

**Connector endpoints to verify against live API docs** (flagged during build): Meta
conversion `action_type` set per advertiser; Google REST camelCase vs gRPC; TikTok status
enums + campaign-name join; Reddit account-listing path + report row shape + micros scaling.
These are isolated to the four connector files and don't affect the framework.

---

## 11. SUGGESTED FEATURES (high-value, beyond the ask)

- **Unified "Marketing P&L"** — organic reach + paid spend + revenue in one view per project.
- **Budget pacing** — daily burn vs. monthly target with projected end-of-month spend.
- **Cross-channel dedupe** — flag the same user converting on two platforms (double-counted ROAS).
- **Creative leaderboard** — rank ad creatives by ROAS across platforms; auto-pause losers (Phase D).
- **Client-ready PDF reports** — branded weekly/monthly ad performance export (agency tier).
- **Slack/email digest** — "Yesterday: $X spend, Y conversions, Z ROAS" pushed each morning.
- **Credit auto-top-up** — opt-in: when credits < threshold, auto-buy a pack (Stripe off-session).
- **Anomaly guardrails** — auto-pause a campaign if CPA exceeds a ceiling (Phase D + alerts).
```
