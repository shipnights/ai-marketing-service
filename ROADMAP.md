# Roadmap

What is left to build, in the order I plan to do it. Every item here comes from the plan
documents in this repository ([MARKETING_PLATFORM_LOGIC.md](MARKETING_PLATFORM_LOGIC.md) and
[ADS_STATS_LOGIC.md](ADS_STATS_LOGIC.md)) and from the open checklists in the
sub-folder READMEs, where each item has more detail. Time estimates are the ones in those plans,
not measurements.

This is a personal-use tool for one owner. On 19 November 2026 I removed Stripe billing, plans and
AI-credit metering, so nothing below is gated on credits or payments.

## 1. Finish the organic-content loop (backend + frontend)

The simplest end-to-end loop first: create a project, generate ideas, generate posts, show the
results. Media generation and auto-publishing come after that works.

- [ ] Website scraping and audience analysis with the Claude API (`POST /analysis/{project_id}/audience`)
- [ ] Idea generation, approve/reject/regenerate (`/ideas/{project_id}/...`)
- [ ] Post text generation and a review UI
- [ ] Missing frontend pages: Ideas, Posts, Schedule, Analytics
- [ ] Validate required settings on startup (fail fast on a missing `JWT_SECRET` or `ANTHROPIC_API_KEY`)
- [ ] `scripts/seed_dev.py` to seed a demo team, user and project

## 2. Ads: scheduled sync, alerts and attribution

From the ads plan, phases B and C.

- [ ] Scheduled sync worker for all teams, plus token-health checks and reconnect prompts (about 1 week)
- [ ] Spend and anomaly alerts, per-account sync cursors, 90/180-day backfill
- [ ] Revenue attribution: import your own sales data (CSV or manual entry), click-id capture,
      blended ROAS dashboard (about 1.5 weeks). This replaces the earlier Stripe Connect idea.
- [ ] Verify the four connector endpoints against live API docs (Meta conversion action types,
      Google REST field names, TikTok status enums, Reddit report rows)

## 3. Media generation, scheduling and publishing

- [ ] Image and video generation pipeline
- [ ] Final approval and scheduling
- [ ] Auto-publishing engine for Meta and TikTok, with a publish log

## 4. Tests

The tests project keeps its own checklist (`ai-marketing-service-tests/README.md`), with
96 items done and 233 open at the time of writing.

- [ ] Backend unit and integration tests for the phases above
- [ ] Frontend tests with Jest and React Testing Library (there are none yet)
- [ ] Playwright end-to-end tests
- [ ] Fix the 8 `react-hooks` lint errors in the frontend

## 5. Later

- [ ] Ad campaign management (create, pause, budget edit) with spend caps and an audit log
- [ ] AI ads intelligence: weekly Claude report, ad-copy variants, budget recommendations
- [ ] Analytics and insights, extended analysis, user-uploaded content workflow
- [ ] Mobile app
