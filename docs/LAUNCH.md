# Launch runbook — ship it for $0

The cheapest, most efficient way to take GridMaster live. Everything here is free
and needs no credit card. Budget ~20 minutes end to end.

> Deeper detail lives in [`HANDOFF.md`](./HANDOFF.md) (§3 go-live, §7 durability)
> and the project [`README`](../README.md). This is the condensed checklist.

---

## What it costs

| Piece | Provider | Cost | Card? |
|---|---|---|---|
| Web host (HTTPS, auto-deploy) | **Render** free web service | $0 | No |
| Durable accounts/leaderboards | **Backblaze B2** (10 GB) via Litestream | $0 | No |
| Question data | committed bank in repo (`F1_DATA_SOURCE=dataset`) | $0 | — |
| Analytics | first-party, in-app (`/analytics`) | $0 | — |
| Domain (optional) | `*.onrender.com` is free; a custom domain is ~$10/yr | $0–10/yr | — |

Total to be fully live: **$0.** A custom domain is the only thing worth paying for,
and it's optional.

Why this stack is the efficient choice: it's a **single FastAPI service** (no
separate frontend host, no managed Postgres, no Redis), boots instantly from the
committed question bank (no network at boot), and stays on the free tier because
durability is handled by streaming SQLite to free object storage instead of a paid
disk.

---

## Step 1 — Deploy to Render (free, ~5 min)

1. Push/merge the launch code to **`main`** (Render deploys that branch).
2. Render dashboard → **New → Blueprint** → connect this repo → **Apply**.
   `render.yaml` wires the build (deps + Litestream binary), the `start.sh`
   entrypoint, `F1_DATA_SOURCE=dataset` (instant offline boot) and
   `F1_DEV_TOOLS=0` (answer-key endpoint off).
3. Wait for the green deploy, open the `*.onrender.com` URL — it works on mobile.

> ⚠️ **Free-tier cold starts:** a free Render service sleeps after ~15 min idle and
> takes a few seconds to wake. Fine for launch. If first-impression latency starts
> costing you on race weekends, the cheapest fix is a $7/mo paid instance — don't
> pay for it until traffic justifies it.

---

## Step 2 — Make accounts durable (free, ~5 min) — *do this before sharing the link*

Render's free filesystem is wiped on every redeploy **and** cold start, so without
this, accounts/leaderboards/streaks reset constantly. Litestream fixes it for free
by streaming the SQLite DB to object storage.

1. Create a **Backblaze B2** bucket (10 GB free, no card) — or Cloudflare R2.
2. Make an application key with read/write; note the **keyID**, **applicationKey**,
   **endpoint** (e.g. `s3.us-west-002.backblazeb2.com`) and **region**
   (e.g. `us-west-002`).
3. In Render → service → **Environment**, set the five `sync: false` vars:

   | Var | Example |
   |---|---|
   | `LITESTREAM_REPLICA_BUCKET` | `gridmaster-db` |
   | `LITESTREAM_REPLICA_ENDPOINT` | `s3.us-west-002.backblazeb2.com` |
   | `LITESTREAM_REPLICA_REGION` | `us-west-002` |
   | `LITESTREAM_ACCESS_KEY_ID` | *(keyID)* |
   | `LITESTREAM_SECRET_ACCESS_KEY` | *(applicationKey)* |

4. Redeploy. Logs should show `Starting uvicorn under Litestream replication`.

> Skip this only if you genuinely don't mind accounts resetting (e.g. a soft
> pre-launch demo). For a real launch, do it first — it's free and 5 minutes.

---

## Step 3 — Turn on the analytics dashboard (free, ~1 min)

Event *collection* is always on; this only gates *viewing*.

- Render → Environment → set **`F1_ANALYTICS_TOKEN`** to a strong secret.
- Open `https://<your-url>/analytics`, paste the token: DAU/WAU/MAU, the
  open→start→complete→share→signup funnel, D1/D7 retention, mode mix, growth.

---

## Step 4 — Pre-share checks (~5 min)

- [ ] Daily / Race / Hardcore / Free Practice / Arcade all load and score.
- [ ] Sign up, play, sign out, sign back in — totals persist (confirms Step 2).
- [ ] Share a result → the link unfurls with a rich preview
      ([opengraph.xyz](https://www.opengraph.xyz/)) and the deep link reopens the
      exact puzzle (`/?play=daily`).
- [ ] `/api/v1/dev/questions` returns **404/disabled** (answer key is off in prod).
- [ ] (If you changed `app.js`/`style.css`) the `?v=N` cache-buster in
      `index.html` was bumped, so returning users don't get stale assets.

---

## Step 5 — Then market it

Don't share the link until Steps 1–4 are green. When they are, follow
[`MARKETING.md`](./MARKETING.md) — the share loop is the channel; your only job is
to light the fuse each race weekend.

---

## Cheapest paths to *more* later (only when traffic justifies it)

1. **Custom domain** (~$10/yr) — the one worthwhile spend; better for sharing/SEO.
2. **Paid Render instance** ($7/mo) — kills cold-start latency once weekend
   traffic makes it matter.
3. **Managed Postgres / Redis** — only needed to scale past one instance; the free
   SQLite + Litestream + in-memory token store is correct until then. See
   HANDOFF §10–11.
