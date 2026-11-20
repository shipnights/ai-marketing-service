# AI Marketing Service

A FastAPI + DynamoDB backend, a Next.js web app and a pytest suite for a personal AI
social-media and paid-ads marketing tool. You connect your own ad accounts and see spend and
performance for Google, Meta, TikTok and Reddit in one place. The longer plan is to use the
Claude API to analyse an audience, generate post ideas and copy, and publish them on a schedule.

It is a side project, built by one person and meant to be run by one person on their own machine.
There is no billing, no plans and no AI-credit metering: you bring your own ad-platform and
Anthropic API keys and pay those providers directly. It has no live deployment.

Registration is still the original four-step wizard and creates a team, which here is simply
your one personal workspace. The code underneath is multi-user, but nothing in this repository
sets up more than one owner.

## What works today

- **Backend** (`ai-marketing-service-backend/`): four-step registration, login, token refresh,
  email verification and password reset (Argon2, JWT), project create/activate/archive,
  ad-platform OAuth connections with encrypted token storage, and a unified ad-stats engine
  with four read-only connectors (Google, Meta, TikTok, Reddit).
- **Frontend** (`ai-marketing-service-frontend/`): sign-in and a four-step registration wizard, a
  project dashboard, and the ad-accounts and integrations pages.
- **Tests** (`ai-marketing-service-tests/`): a pytest suite that runs against a mocked DynamoDB
  (moto), so it needs no AWS account and no API keys.

## What is not built yet

The organic-content side is still a plan: audience analysis, idea and post generation, media
generation, scheduling and auto-publishing. The frontend pages for Ideas, Posts, Schedule and
Analytics do not exist yet. [ROADMAP.md](ROADMAP.md) lists the remaining work in order, and the
checklists in each sub-folder's README show what is done and what is open.

## Built with

Python 3.12, FastAPI, Uvicorn, boto3 and DynamoDB, PyJWT, Argon2, structlog, slowapi, the
Anthropic SDK. Next.js 16, React 19 and TypeScript on the front end. pytest, moto and
Playwright for tests.

## Run it

You need Python 3.12, Node.js 20 or newer, and Docker (for DynamoDB Local).

```bash
# 1. Backend, on http://localhost:8000
cd ai-marketing-service-backend
docker run -d -p 8001:8000 amazon/dynamodb-local
pip install -r requirements.txt
cp .env.example .env
python scripts/create_tables.py
uvicorn app.main:app --reload --port 8000

# 2. Frontend, on http://localhost:3000
cd ../ai-marketing-service-frontend
cp .env.example .env.local
npm install
npm run dev
```

The ad platforms need your own developer credentials, set in `.env`. Without them the connectors
cannot be exercised end to end. See `ai-marketing-service-backend/.env.example` for every
setting. No real keys are in this repository.

## Run the tests

```bash
cd ai-marketing-service-tests
pip install -r requirements.txt
pip install -e ../ai-marketing-service-backend   # or set PYTHONPATH to the backend folder
pytest backend/
```

On Python 3.12 the backend suite takes under two minutes: 480 pass, 34 are marked as expected
failures (`xfail`) and none fail (run on 19 November 2026, after the billing code was removed).

## Repository layout

| Path | What it is |
|---|---|
| `ai-marketing-service-backend/` | FastAPI API, DynamoDB single-table access layer, ad connectors |
| `ai-marketing-service-frontend/` | Next.js web app |
| `ai-marketing-service-tests/` | pytest, Jest and Playwright test project, plus its TODO list |
| `MARKETING_PLATFORM_LOGIC.md` | the original build plan for the organic-content platform |
| `ADS_STATS_LOGIC.md` | design and phase plan for ads and unified stats |

## History of this repository

The three folders began as three separate repositories, each uploaded as a single commit on
19 March 2026. They were combined into this repository in November 2026, so the commit history
here is short and starts on 10 November 2026.

## License

[MIT](LICENSE).
