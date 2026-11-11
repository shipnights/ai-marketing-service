# Marketing AI Platform — Logic & Build Plan
**Created:** 2026-03-18
**Priority:** Internal team use first → then public-facing app
**Stack Recommendation:** Next.js (frontend) · Node.js/Python (backend) · PostgreSQL (DB) · Anthropic Claude API · Higgsfield AI (media gen) · Meta/TikTok APIs (publishing)

> **⚠️ As-built note (2026-06-22):** the implemented backend is **FastAPI + DynamoDB**
> (single-table design), **not** Postgres/Prisma. Treat the Postgres references below as
> the original proposal; the live code uses DynamoDB. See `app/scripts/create_tables.py`.
>
> **➕ Paid Ads + Unified Stats + Billing** are now a first-class layer on top of the
> organic workflow below — connect Google / Meta / TikTok / Reddit ad accounts, pull
> cross-platform spend & performance, and bill via Stripe (subscriptions + AI credits +
> ROAS). Full design & phased plan: **[ADS_STATS_BILLING_LOGIC.md](ADS_STATS_BILLING_LOGIC.md)**.

---

## QUICK OVERVIEW

```
User Creates Project
       ↓
AI Analyzes Audience + Market
       ↓
AI Generates 5–15 Promotion Ideas
       ↓
For Each Idea → AI Generates 5–10 Posts (text + visual brief)
       ↓
User Reviews / Approves / Edits Posts
       ↓
For Each Approved Post → Generate Image or Video (Higgsfield / DALL-E / Runway)
       ↓
User Final Approval → AI Writes Captions, Hashtags, CTAs
       ↓
User Schedules (or AI Recommends Schedule)
       ↓
Platform Auto-Posts on Schedule (no user interaction needed)
```

---

## PHASE 0 — FOUNDATION & SETUP
**Goal:** Dev environment, architecture, auth, DB
**Estimated time: 1–2 weeks**

### 0.1 Project Scaffolding
- [ ] Initialize monorepo (e.g. Turborepo or nx) with `/apps/web`, `/apps/api`, `/packages/shared`
- [ ] Set up Next.js 14+ (App Router) for web frontend
- [ ] Set up Node.js + Express (or Fastify) OR Python FastAPI for backend API
- [ ] Configure TypeScript across all packages
- [ ] Set up ESLint, Prettier, Husky pre-commit hooks
- [ ] Create `.env.example` with all required keys documented

### 0.2 Database Setup
- [ ] Set up PostgreSQL (local dev via Docker Compose)
- [ ] Set up Prisma ORM (or Drizzle) with initial schema
- [ ] Create initial tables: `users`, `teams`, `projects`, `ideas`, `posts`, `media`, `schedules`, `publish_logs`
- [ ] Set up migrations workflow
- [ ] Set up Redis (for job queues and caching)

### 0.3 Authentication
- [ ] Implement auth via NextAuth.js or Clerk (team SSO support)
- [ ] Roles: `admin`, `editor`, `viewer`
- [ ] Team/workspace concept — users belong to a team
- [ ] Invite team members by email

### 0.4 Infrastructure
- [ ] Docker Compose for local: Postgres + Redis + API + Web
- [ ] Set up background job queue (BullMQ with Redis) for AI tasks and scheduled posts
- [ ] File storage: local for dev → S3/Cloudflare R2 for production
- [ ] Set up logging (Winston or Pino)

---

## PHASE 1 — PROJECT CREATION & DATA COLLECTION
**Goal:** Users can create a project and feed the AI with context
**Estimated time: 1 week**

### 1.1 Project Model (DB Schema)
```
Project {
  id, name, team_id, created_by
  product_name, product_description
  industry, niche
  target_age_range, target_gender, target_location
  target_interests, target_pain_points
  budget_range, campaign_goal (brand_awareness | leads | sales | engagement)
  tone_of_voice (professional | casual | humorous | inspirational)
  platforms: [instagram, facebook, tiktok]
  website_url
  existing_assets (uploaded images/videos)
  competitors (optional list)
  created_at, updated_at
  status (draft | active | archived)
}
```

### 1.2 Project Creation UI
- [ ] Multi-step wizard form (5–6 steps, not one long form)
  - Step 1: What are you promoting? (name, description, URL)
  - Step 2: Who is your audience? (demographics, interests, pain points)
  - Step 3: Campaign goals & budget range
  - Step 4: Tone, style preferences, platforms
  - Step 5: Upload existing photos/videos (optional)
  - Step 6: Competitor URLs (optional)
- [ ] Save as draft at any step
- [ ] Project dashboard (list all projects, status badges, quick actions)

### 1.3 Website Analysis (optional at project creation)
- [ ] If website URL provided → scrape via Puppeteer/Playwright
- [ ] Extract: headline, value props, product info, social links
- [ ] Feed extracted data into project context automatically
- [ ] Show user what was extracted, let them confirm/edit

---

## PHASE 2 — AI AUDIENCE ANALYSIS & IDEA GENERATION
**Goal:** Claude analyzes the project and generates promotion ideas
**Estimated time: 1–1.5 weeks**

### 2.1 Audience Analysis Engine (Claude API)
- [ ] Build `AudienceAnalysisService`
- [ ] Prompt: feed all project data → Claude returns structured JSON:
  ```json
  {
    "primary_audience": { "description", "psychographics", "behaviors", "platforms_used" },
    "secondary_audience": { ... },
    "messaging_angles": ["pain point angle", "aspiration angle", ...],
    "best_platforms": ["instagram", "tiktok"],
    "best_content_types": ["short video", "carousel", "story"],
    "recommended_posting_times": { "instagram": ["18:00", "20:00"], ... }
  }
  ```
- [ ] Display audience analysis as a readable report card in UI
- [ ] User can edit/override any segment before proceeding

### 2.2 Idea Generation (Claude API)
- [ ] Build `IdeaGenerationService`
- [ ] Input: project data + audience analysis output
- [ ] Claude generates 5–15 promotion ideas, each as structured JSON:
  ```json
  {
    "id": "idea_001",
    "title": "Behind the Scenes Series",
    "concept": "...",
    "hook": "...",
    "platforms": ["instagram", "tiktok"],
    "content_type": "video_series",
    "estimated_impact": "high",
    "tone": "casual",
    "reasoning": "Why this works for this audience"
  }
  ```
- [ ] Show ideas as interactive cards in UI
- [ ] User can: approve, reject, edit title/concept, add custom notes
- [ ] Min 3 ideas must be approved to proceed

### 2.3 Ideas UI
- [ ] Ideas board (card grid layout)
- [ ] Each card: title, concept summary, platform badges, impact indicator
- [ ] Expand card to see full reasoning
- [ ] Inline editing of idea concept/title
- [ ] "Generate more ideas" button (re-prompts with feedback on rejected ones)
- [ ] Proceed button → goes to post generation

---

## PHASE 3 — POST GENERATION (TEXT)
**Goal:** For each approved idea, generate 5–10 social posts
**Estimated time: 1.5 weeks**

### 3.1 Post Model (DB Schema)
```
Post {
  id, project_id, idea_id
  platform (instagram | facebook | tiktok)
  post_type (feed | story | reel | carousel)
  caption_draft, caption_final
  hashtags, cta
  visual_brief (text description for image/video gen)
  media_id (FK → generated or uploaded media)
  status (draft | approved | scheduled | published | failed)
  scheduled_at, published_at
  created_at, updated_at
}
```

### 3.2 Post Generation Service (Claude API)
- [ ] Build `PostGenerationService`
- [ ] Input: idea + audience analysis + platform + tone
- [ ] Claude generates for each idea × each platform:
  - Caption (draft)
  - Hashtags (platform-optimized count: IG=15–25, FB=3–5, TT=5–8)
  - CTA (call-to-action)
  - Visual brief (detailed text description for image/video generation)
  - Suggested post format (carousel, reel, story, etc.)
- [ ] Posts generated async (background job), shown in real-time via SSE/WebSocket
- [ ] Platform-specific formatting applied automatically

### 3.3 Posts Review UI
- [ ] Posts grouped by idea
- [ ] Each post card: platform icon, post type, caption preview, visual brief
- [ ] Inline editing of caption, hashtags, CTA
- [ ] Character counter per platform (IG: 2200, FB: 63000, TT: 2200)
- [ ] "Regenerate caption" button per post
- [ ] Approve / Reject / Edit states
- [ ] Bulk approve all posts in an idea

---

## PHASE 4 — MEDIA GENERATION (IMAGE & VIDEO)
**Goal:** Generate visuals for each approved post
**Estimated time: 2 weeks**

### 4.1 Media Generation Providers
- [ ] Build unified `MediaGenerationService` with provider abstraction:
  - **Images:** Higgsfield AI (Soul), DALL-E 3, Stable Diffusion (fallback)
  - **Videos:** Higgsfield AI (Image-to-Video, Speech-to-Video), Runway ML
- [ ] Provider selected per post based on type (image vs video) and quality settings
- [ ] Queue-based generation (BullMQ) — don't block UI

### 4.2 Image Generation Flow
- [ ] Take visual brief from post → send to image gen API
- [ ] Store result in file storage (S3/R2)
- [ ] Show generated image in post card
- [ ] Options: Regenerate, Upscale, Crop for platform (square/portrait/landscape)
- [ ] User can also upload their own image to replace generated one

### 4.3 Video Generation Flow
- [ ] Take visual brief + optional base image → send to video gen API (Higgsfield)
- [ ] Queue video generation (takes 30s–3min)
- [ ] Notify user via in-app notification when video is ready
- [ ] Show video player in post card
- [ ] Options: Regenerate, trim (basic), add captions overlay

### 4.4 User-Uploaded Assets
- [ ] Drag-and-drop upload zone on post card
- [ ] Accept: JPG, PNG, MP4, MOV, GIF
- [ ] Auto-resize/compress to platform specs
- [ ] Apply to post → skips generation step

### 4.5 Media Library
- [ ] All generated + uploaded assets stored in a project media library
- [ ] Reuse assets across posts/projects
- [ ] Basic metadata: dimensions, duration, file size, creation date

---

## PHASE 5 — FINAL APPROVAL & SCHEDULING
**Goal:** User approves final posts and sets schedule
**Estimated time: 1 week**

### 5.1 Final Review Board
- [ ] Kanban-style board: Draft → Approved → Scheduled → Published
- [ ] Each post shows: final media thumbnail, caption, platform, scheduled time
- [ ] Final approve/reject per post
- [ ] "Approve all" button

### 5.2 AI-Recommended Schedule (Claude API)
- [ ] Based on audience analysis + platform best practices → Claude recommends:
  - Best days of week
  - Best times per platform
  - Frequency recommendation (posts/week)
  - Content mix ratio (70% value, 20% promotional, 10% engagement)
- [ ] Show recommended schedule as a calendar view
- [ ] User can accept recommendations or set custom times

### 5.3 Scheduling Model
- [ ] Schedule calendar UI (week/month view)
- [ ] Drag-and-drop posts onto calendar slots
- [ ] Conflict detection (don't post same platform within 3h unless configured)
- [ ] Bulk schedule: "spread these 10 posts over 2 weeks"
- [ ] Export schedule as CSV

---

## PHASE 6 — AUTO-PUBLISHING ENGINE
**Goal:** Platform posts automatically at scheduled time, no user interaction
**Estimated time: 1.5–2 weeks**

### 6.1 Platform Integrations (OAuth)
- [ ] **Instagram/Facebook:** Meta Graph API
  - Connect via Facebook Login OAuth
  - Permissions: `instagram_content_publish`, `pages_manage_posts`, `pages_read_engagement`
  - Support: feed posts, stories, reels (video)
- [ ] **TikTok:** TikTok for Developers API
  - Connect via TikTok OAuth
  - Permissions: `video.upload`, `video.publish`
- [ ] Manage connected accounts per team/project
- [ ] Token refresh logic (long-lived tokens, auto-refresh)
- [ ] Account health check (detect expired tokens before publish time)

### 6.2 Publishing Worker
- [ ] BullMQ scheduled job: check every minute for posts due within next 5 min
- [ ] For each due post:
  1. Fetch media from storage
  2. Resize/reformat for platform specs if needed
  3. Call platform API to publish
  4. Store result: post_id, permalink, published_at
  5. Update post status → `published`
  6. On failure → retry 3x with backoff → status `failed`, notify user
- [ ] Platform-specific publish logic:
  - Instagram: Resumable upload for videos >4MB
  - TikTok: chunked upload for videos
  - Facebook: page post vs personal post handling

### 6.3 Publishing Logs & Notifications
- [ ] Log every publish attempt (success/fail + API response)
- [ ] In-app notification on publish success/failure
- [ ] Email notification option for failures
- [ ] Published posts show engagement metrics (likes, comments, reach) fetched async

---

## PHASE 7 — ANALYTICS & INSIGHTS
**Goal:** Track post performance and feed back into AI
**Estimated time: 1–1.5 weeks**

### 7.1 Post Performance Metrics
- [ ] Fetch metrics via Meta Insights API + TikTok Analytics API
- [ ] Metrics per post: reach, impressions, likes, comments, shares, saves, CTR
- [ ] Store in `post_metrics` table, refresh every 24h
- [ ] Per-project dashboard: total reach, best performing post, best performing idea

### 7.2 AI Insight Generation (Claude API)
- [ ] Weekly/monthly AI summary: "Your best performing content was X style, recommend Y going forward"
- [ ] Identify which ideas/post types performed best
- [ ] Auto-adapt future post generation based on performance data

---

## PHASE 8 — EXTENDED ANALYSIS FEATURES
**Goal:** Website, Instagram, Facebook, TikTok analysis
**Estimated time: 2–3 weeks**

### 8.1 Website Analysis
- [ ] Input: URL → Puppeteer scrape → extract content, structure, meta tags
- [ ] Claude analyzes: messaging clarity, CTA effectiveness, SEO basics, conversion potential
- [ ] Output: report with score + specific improvement suggestions
- [ ] "Generate updated copy" option → Claude rewrites sections

### 8.2 Website Content Update Suggestions
- [ ] Claude generates: new hero text, tagline alternatives, FAQ updates, blog post ideas
- [ ] Export suggestions as Word doc or copy-paste snippets
- [ ] (Advanced) Integration with CMS APIs (WordPress, Webflow) for direct updates

### 8.3 Instagram Profile Analysis
- [ ] Connect via Meta API → fetch: bio, follower count, recent posts, engagement rate
- [ ] Claude analyzes: bio optimization, posting frequency, content consistency, audience alignment
- [ ] Benchmark against industry averages
- [ ] Suggests: new bio, profile picture guidance, highlights strategy

### 8.4 Facebook Group/Page Analysis
- [ ] Connect via Meta API → fetch page insights, recent posts, engagement
- [ ] Claude analyzes: content performance, audience demographics, optimal posting strategy
- [ ] Group analysis: activity patterns, top contributors, content themes

### 8.5 TikTok Analysis
- [ ] Connect via TikTok API → fetch: videos, views, likes, follower growth
- [ ] Claude identifies: best performing formats, sound/trend alignment, hook effectiveness
- [ ] Content calendar recommendations based on TikTok trend cycles

### 8.6 Marketing Plan Generation
- [ ] Full 30/60/90-day marketing plan as structured document
- [ ] Based on: project data + analysis results + budget + goals
- [ ] Includes: content calendar, platform strategy, KPI targets, budget allocation
- [ ] Export as PDF or interactive plan in UI
- [ ] Plan adapts over time based on performance data

---

## PHASE 9 — USER-UPLOADED CONTENT WORKFLOW
**Goal:** Users add own photos/videos and get AI-generated descriptions/captions only
**Estimated time: 3–5 days**

- [ ] "Quick Post" mode: upload asset → Claude generates caption + hashtags + CTA
- [ ] Platform selection → platform-optimized output
- [ ] Choose tone preset or match project's brand voice
- [ ] Schedule immediately or add to queue
- [ ] Batch mode: upload 10 images → bulk generate captions → review → schedule

---

## PHASE 10 — MOBILE APP
**Goal:** iOS + Android app (after web platform is stable)
**Estimated time: 4–6 weeks**

### Stack
- React Native (Expo) — share business logic with web where possible
- Same backend API as web

### Core App Features
- [ ] Project overview + quick stats
- [ ] Post approval workflow (approve/reject on mobile)
- [ ] Push notifications for scheduled publishes, failures, performance alerts
- [ ] Quick Post: capture/upload photo/video → generate caption → schedule
- [ ] Calendar view of scheduled posts
- [ ] Basic analytics dashboard

### App-Specific Features
- [ ] Camera integration (capture directly in app)
- [ ] Stories preview simulator (see how post looks on IG stories)
- [ ] Swipe to approve/reject posts (Tinder-style UX)

---

## TECH STACK SUMMARY

| Layer | Technology (as-built where it differs) |
|-------|------------|
| Frontend | Next.js (App Router), TypeScript, **CSS Modules** (not Tailwind) |
| Backend API | **Python FastAPI** |
| Database | **DynamoDB** (single-table design) — *original plan said Postgres* |
| Cache / Queues | Redis + BullMQ (planned) / scheduled worker for ad sync |
| AI | Anthropic Claude API (claude-opus-4-8 / claude-sonnet-4-6) |
| Image Gen | Higgsfield AI Soul, DALL-E 3 |
| Video Gen | Higgsfield AI (Image-to-Video), Runway ML |
| File Storage | Local dev → AWS S3 / Cloudflare R2 |
| Auth | JWT (PyJWT) — bespoke, with dev header bypass |
| Publishing APIs | Meta Graph API, TikTok for Developers |
| **Ad APIs** | **Google Ads v17 · Meta Marketing v21 · TikTok Business v1.3 · Reddit Ads v3** |
| **Billing** | **Stripe** (Checkout · Billing Portal · webhooks · AI-credit ledger) |
| **Token security** | **Fernet** encryption at rest for OAuth tokens |
| Web Scraping | Puppeteer / Playwright |
| Mobile | React Native (Expo) |
| Deployment | Docker → VPS (phase 1) → Railway/Render or AWS (scale) |

---

## PHASE BUILD ORDER (INTERNAL TEAM USE FIRST)

```
Phase 0  → Foundation (infra, DB, auth)               ~2 weeks
Phase 1  → Project creation + data collection          ~1 week
Phase 2  → AI audience analysis + idea generation      ~1.5 weeks
Phase 3  → Post text generation + review UI            ~1.5 weeks
Phase 4  → Media generation (images + video)           ~2 weeks
Phase 5  → Final approval + scheduling UI              ~1 week
Phase 6  → Auto-publishing engine                      ~2 weeks
─────────────────────────────────────────────────────────────────
INTERNAL MVP READY                                     ~11 weeks
─────────────────────────────────────────────────────────────────
Phase 7  → Analytics & insights                        ~1.5 weeks
Phase 8  → Extended analysis (website, profiles, etc.) ~3 weeks
Phase 9  → User-uploaded content quick workflow        ~1 week
Phase 10 → Mobile app (iOS + Android)                  ~6 weeks
─────────────────────────────────────────────────────────────────
FULL PLATFORM + APP READY                              ~23 weeks
```

---

## ADDITIONAL SUGGESTIONS (NOT IN ORIGINAL SPEC)

### A. Brand Voice Profile (~3 days)
Create a brand voice document per project. Claude learns from it and keeps all
generated content consistent. Users can describe or upload examples of their tone.

### B. A/B Post Variants (~1 week)
For each post, generate 2 variants (different hooks/angles). Track which performs
better and feed learnings back to AI for future projects.

### C. AI Content Calendar Assistant (~1 week)
Chat interface where users can ask: "Reschedule my posts around the holiday",
"Add 3 more posts for next week focused on this product feature", etc.

### D. Competitor Monitoring (~2 weeks)
User adds competitor Instagram/TikTok handles → platform periodically scrapes
public posts → Claude identifies their content strategy → gives user differentiation tips.

### E. Hashtag Intelligence Engine (~1 week)
Track hashtag performance over time per niche. Suggest trending hashtags in real-time.
Avoid shadowbanned hashtags. Build platform-specific hashtag banks per industry.

### F. Post Performance Prediction (~2 weeks)
Before publishing, Claude estimates engagement potential (low/medium/high) based on
historical data from the team's past posts + current trend data.

### G. Team Collaboration & Approval Workflows (~1 week)
Multi-step approval: Editor creates → Manager reviews → Client approves.
Comments and revision requests on each post. Version history of edits.

### H. White-Label / Client Portal (~2–3 weeks)
Let agencies give clients a read-only or approval-only portal under custom domain.
Client sees only their project, approves content without full platform access.

### I. Zapier / Make.com Integration (~1 week)
Webhook triggers for: post published, post failed, project completed, new idea generated.
Connect to CRMs, Slack, email, Notion, Airtable.

### J. Ad Campaign Creation → **now a first-class layer** ✅
Connecting ad accounts and pulling unified stats (Google / Meta / TikTok / Reddit) is
**implemented** — see [ADS_STATS_BILLING_LOGIC.md](ADS_STATS_BILLING_LOGIC.md). Extending
approved posts into *new* paid campaigns (budget, targeting, run duration, "Boost this post")
is **Phase D — Ad campaign management** in that doc.

### K. SEO Blog Post Generator (~1 week)
Based on project context, Claude generates SEO blog posts aligned with the social
campaign. Export to WordPress/Webflow directly.

### L. Onboarding AI Wizard (~3–5 days)
New user onboarding: AI asks questions in conversational chat format instead of forms.
More engaging, higher completion rate, collects richer project context.

---

## IMMEDIATE NEXT STEPS (START HERE)

1. **Create repo + monorepo structure**
2. **Set up Docker Compose** (Postgres, Redis, API, Web)
3. **Design & run DB migrations** (core tables)
4. **Set up auth** (Clerk recommended for fast team SSO setup)
5. **Build project creation wizard** (Phase 1)
6. **Wire up Claude API** for audience analysis (Phase 2)

> Start with the simplest possible loop:
> **Create project → Generate ideas → Generate posts → Display results**
> Get that working end-to-end before adding media generation or auto-publishing.
