# AI Marketing Service — Frontend

Next.js 16 / React 19 / TypeScript web app for the AI Marketing Service backend
(`../ai-marketing-service-backend`). It is set up to run on your own machine only.

## Run it locally

You need Node.js 20 or newer and the backend running on `http://localhost:8000`
(see the backend README for how to start it).

```bash
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000. The only setting is `NEXT_PUBLIC_API_URL`, which already
points at `http://localhost:8000` in `.env.example`.

`npm run build` makes a production build, which passes type checking. `npm run lint`
currently reports 10 `react-hooks` errors in existing code (mostly state set inside
effects); they do not stop the build or the app.

## What is there

- **Sign in and registration.** `/auth` has the login form and the four-step
  registration wizard. Finishing step 4 verifies the email, so no email service is
  needed locally.
- **Dashboard.** `/dashboard` lists projects. `/dashboard/ads` shows connected ad
  accounts, `/dashboard/integrations` the connected platforms, and
  `/dashboard/billing` plans and AI credits. Billing shows plans as not configured
  until Stripe settings are added to the backend.
- **Not built yet.** The home page is a placeholder. The Ideas, Posts, Schedule and
  Analytics links in the sidebar lead to pages that do not exist yet and return 404.
- **Tests.** Jest and React Testing Library are configured, but there are no frontend
  tests yet, so `npm test` reports that no tests were found.

## Layout

```
src/
  app/          pages: / , /auth , /dashboard (+ ads, billing, integrations)
  components/   Sidebar, CreditBalance, MetricCard, PlatformBadge, StatusBadge
  lib/          api-client.ts (fetch wrapper with token refresh), one *-api.ts per
                backend area, storage.ts (localStorage keys), hooks/
```

`lib/api-client.ts` adds the access token to each request, retries once through
`/api/v1/auth/refresh` after a 401, and unwraps the backend's
`{ "status": "success", "data": ... }` response.
