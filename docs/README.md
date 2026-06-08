# F1 StatGuesser — Documentation Index

This folder contains the design and engineering documentation for **F1 StatGuesser**, a gamified Formula 1 statistics quiz platform.

## Documents

| # | Document | Audience | Summary |
|---|----------|----------|---------|
| 1 | [Product Requirements Document](./PRD.md) | Product, Frontend, Backend, UI/UX, QA | Executive summary, scoring engine, data pipeline overview, gameplay modes, gamification & monetization. |
| 2 | [Technical Pipeline Specs](./TECHNICAL_PIPELINE_SPECS.md) | Backend / Data Engineers | ETL ingestion engine, LLM context chunking, deterministic anti-hallucination validation layer, production SQL schema. |
| 3 | [Full Architecture Blueprint](./ARCHITECTURE_BLUEPRINT.md) | Full-Stack / DevOps | Game logic & API state, guest-first frontend state, constructor theming, score-reveal animation, ad-network integration. |

## Reading Order

1. Start with the **PRD** for product scope and the scoring formula.
2. Read the **Technical Pipeline Specs** to understand how trivia questions are sourced, generated, and validated.
3. Read the **Architecture Blueprint** for runtime serving, client state, theming, and monetization.

## Key Cross-Cutting Invariants

- **Server-authoritative scoring:** True answers are never sent to the client; all scoring runs server-side using the percentage-error exponential decay formula (PRD §2).
- **Anti-hallucination validation:** No LLM-proposed answer is trusted; each is independently recomputed against trusted staging data before reaching production (Pipeline §3).
- **Trust boundary on sync:** Client-supplied aggregate totals are never trusted for leaderboards; totals are reconstructed from server-verified events (Architecture §2.2).
