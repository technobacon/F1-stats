# GridMaster — Documentation Index

This folder contains the design and engineering documentation for **GridMaster**, a gamified Formula 1 statistics quiz platform.

## Documents

| Document | Audience | Summary |
|----------|----------|---------|
| ⭐ [Engineering Handoff](./HANDOFF.md) | **Start here** — anyone picking up the project | Current state, go-live checklist, architecture, env vars, run/test/deploy, durability & analytics, roadmap, gotchas. |
| 🚀 [Launch Runbook](./LAUNCH.md) | Whoever ships it | The $0 go-live checklist: deploy to Render free tier, free durable accounts, analytics, pre-share checks. |
| [Marketing Plan](./MARKETING.md) | Growth | Minimal-effort, no-Reddit growth plan: ride the race calendar, let the share loop do the work. |
| [Status & Roadmap](./STATUS.md) | Everyone | Living snapshot of what's shipped and what's next. |
| [Product Requirements Document](./PRD.md) | Product, Frontend, Backend, UI/UX, QA | Executive summary, scoring engine, data pipeline overview, gameplay modes, gamification & monetization. |
| [Technical Pipeline Specs](./TECHNICAL_PIPELINE_SPECS.md) | Backend / Data Engineers | ETL ingestion engine, LLM context chunking, deterministic anti-hallucination validation layer, production SQL schema. |
| [Full Architecture Blueprint](./ARCHITECTURE_BLUEPRINT.md) | Full-Stack / DevOps | Game logic & API state, guest-first frontend state, constructor theming, score-reveal animation, ad-network integration. |
| [Implementation Notes (as built)](./IMPLEMENTATION_NOTES.md) | Anyone reading the code | How the running service maps to the specs: the generator + validation engine, accounts/leaderboards, analytics, durability, and the serving path. |
| [Question Types](./question-types.md) | Content / Backend | The question taxonomy and design. |

> **Note on the spec docs (PRD, Pipeline, Blueprint):** these describe the original
> *design target* (Next.js + Postgres + Redis + Celery + NextAuth + an LLM
> pipeline). The shipped service intentionally realizes that design as a single,
> dependency-light FastAPI + SQLite app — see **HANDOFF** / **Implementation
> Notes** for what is actually built.

## Reading Order

1. **Engineering Handoff** — orient on current state and how to run it.
2. **PRD** — product scope and the scoring formula.
3. **Technical Pipeline Specs** — how questions are sourced, generated, validated.
4. **Architecture Blueprint** — the target runtime/theming/monetization design.
5. **Implementation Notes** — how the running service realizes all of the above.

## Key Cross-Cutting Invariants

- **Server-authoritative scoring:** True answers are never sent to the client; all scoring runs server-side using the percentage-error exponential decay formula (PRD §2).
- **Anti-hallucination validation:** No LLM-proposed answer is trusted; each is independently recomputed against trusted staging data before reaching production (Pipeline §3).
- **Trust boundary on sync:** Client-supplied aggregate totals are never trusted for leaderboards; totals are reconstructed from server-verified events (Architecture §2.2).
