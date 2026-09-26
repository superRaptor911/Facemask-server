# facemask

Node.js + TypeScript + Express (v5, ESM) backend for the facemask Android app.
Google sign-in, Play Billing consumable credits, Prisma 7 + PostgreSQL.

## Setup

```sh
npm install
cp .env.example .env   # fill in DATABASE_URL + GOOGLE_CLIENT_ID (+ Play vars)
npm run db:migrate     # apply migrations + generate the Prisma client
```

## Scripts

| Command                       | Description                              |
| ----------------------------- | ---------------------------------------- |
| `npm run dev`                 | Start dev server with watch mode (tsx)   |
| `npm run build`               | Compile TypeScript to `dist/`            |
| `npm start`                   | Run the compiled server                  |
| `npm test`                    | Run tests once (Vitest + Supertest)      |
| `npm run test:watch`          | Run tests in watch mode                  |
| `npm run test:coverage`       | Run tests with coverage report           |
| `npm run typecheck`           | Type-check without emitting              |
| `npm run db:migrate`          | Create/apply migrations (dev)            |
| `npm run db:deploy`           | Apply pending migrations (prod/CI)       |
| `npm run db:generate`         | Regenerate the Prisma client             |
| `npm run db:studio`           | Open Prisma Studio                       |
| `npm run job:voided-purchases`| Refund clawback job (see Jobs)           |

## API (contract for the Android app)

All endpoints are under `/api/v1`. Errors are always `{ "error": string }`.
Authenticated endpoints require `Authorization: Bearer <token>`.

| Method | Path                    | Auth | Description |
| ------ | ----------------------- | ---- | ----------- |
| POST   | `/api/v1/auth/google`   | –    | Body `{ idToken }` (Google ID token from Credential Manager). Returns `{ token, user }`. Store `token`; it's the bearer token for everything else. |
| POST   | `/api/v1/auth/logout`   | ✓    | Revokes the current session. 204. |
| GET    | `/api/v1/me`            | ✓    | Current user profile incl. `creditBalance`. |
| GET    | `/api/v1/credits`       | ✓    | `{ balance, transactions }` (last 50 ledger entries). |
| POST   | `/api/v1/billing/verify`| ✓    | Body `{ productId, purchaseToken }` from Play Billing. Verifies with Google, grants credits. 201 `{ credits, balance, alreadyGranted:false }`, or 200 with `alreadyGranted:true` on replay. **The app must call `consumeAsync` only after a 2xx.** |
| DELETE | `/api/v1/account`       | ✓    | Immediate hard delete; unspent credits forfeited (show this in the confirmation UI). 204. |

`GET /health` (unversioned) is for infra checks.

### Auth flow

Android (Credential Manager, "Sign in with Google") → Google **ID token** →
`POST /api/v1/auth/google` → backend verifies signature + audience
(`GOOGLE_CLIENT_ID`, the **web** OAuth client) → upserts user → returns an
opaque bearer token (hashed at rest, 90-day sliding expiry, one per device).
401 on any request means: re-run Google sign-in.

### Credits

- Append-only `CreditTransaction` ledger; `User.creditBalance` is the cached sum.
- Spending is server-authoritative: feature endpoints call
  `spendCredits(userId, amount, { idempotencyKey })` — atomic
  `balance >= cost` guard, safe under retries and races. 402 = insufficient.
- SKUs live in the `CreditProduct` table (Play product ID → credits), e.g.:
  ```sql
  INSERT INTO "CreditProduct" ("productId", credits) VALUES ('credits_100', 100);
  ```

## Jobs

`job:voided-purchases` — polls the Play Voided Purchases API and claws back
credits for refunded purchases (balances can go negative). Run **daily** via
cron/systemd-timer on the deployment:

```
0 4 * * * cd /srv/facemask && node dist/jobs/voided-purchases.js
```

## Environment

| Var | Purpose |
| --- | ------- |
| `DATABASE_URL` | Postgres connection string |
| `GOOGLE_CLIENT_ID` | **Web** OAuth client ID — ID-token audience |
| `PLAY_PACKAGE_NAME` | Android package name (billing verification) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to Play service-account JSON key |

## Google-side setup checklist

1. Google Cloud project → OAuth clients: one **Web application** (its ID is
   `GOOGLE_CLIENT_ID` here) and one **Android** client (teammate's package
   name + SHA-1s).
2. Play Console → app on an **internal testing** track, license testers added.
3. Play Console → consumable in-app products; mirror their IDs in `CreditProduct`.
4. Service account with **Play Developer API** access incl. financial data
   (voided purchases); key file → `GOOGLE_APPLICATION_CREDENTIALS`.
5. TODO before launch: public **web account-deletion page** (Play policy
   requires a deletion link outside the app too).

## Structure

```
src/
  app.ts                        # Express app factory (used by server and tests)
  server.ts                     # Entry point; graceful shutdown
  config/env.ts                 # Env config (fails fast on missing vars)
  errors/http-error.ts          # HttpError → JSON error responses
  lib/prisma.ts                 # Prisma client singleton (pg driver adapter)
  lib/serializers.ts            # Public response shapes
  middleware/                   # requireAuth, 404, error handler
  routes/                       # auth, me, credits, billing, account (+ health)
  services/                     # google-auth, play-billing, sessions, credits,
                                # voided-purchases (business logic, mockable)
  jobs/voided-purchases.ts      # Cron entry point
  generated/prisma/             # Generated Prisma client (gitignored)
prisma/                         # Schema + migrations
tests/                          # Vitest + Supertest; Google APIs mocked,
                                # Postgres real (tests clean up their own rows)
```

## Notes

- Express 5 forwards rejected async handlers to the error middleware — throw
  `new HttpError(status, message)` anywhere.
- Tests hit the database from `DATABASE_URL` but mock Google (`google-auth`,
  `play-billing` services), so they run without any Google credentials.
- After changing `prisma/schema.prisma`, run `npm run db:migrate`.
