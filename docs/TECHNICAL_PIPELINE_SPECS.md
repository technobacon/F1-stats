# TECHNICAL PIPELINE SPECS
## Automated Ingestion & Anti-Hallucination Pipeline
**Module:** Data Engineering Core
**Target Audience:** Backend Developers / Data Engineers

> **Design target, not as-built.** This is the original spec. For what's actually
> shipped (the Jolpica ETL, the metric/aggregation validation engine, the committed
> bank) see [HANDOFF](./HANDOFF.md) and [Implementation Notes](./IMPLEMENTATION_NOTES.md).

### 1. The Raw ETL Ingestion Engine
The ingestion pipeline acts as an isolated, asynchronous system responsible for transforming highly nested external API entities into relational database matrices. To safeguard production systems against downtime, direct runtime execution loops during user client sessions are strictly prohibited.

#### 1.1 API Ingestion Framework
- **Endpoint Targeting:** Primary historical extractions must map against the Jolpica F1 API endpoints (e.g., `https://api.jolpi.ca/ergast/f1/drivers/`). Always use `https://`; do not issue ingestion requests over plain `http://`.
- **Rate-Limit Strategy:** Implement a sliding-window token bucket controller. Violations caught via `429 Too Many Requests` statuses must halt the pipeline for 300 seconds sequentially.
  - **⚠️ Verify against Jolpica's published limits before launch.** A naive cap of 40 requests/minute sustains ≈ 2,400 requests/hour, which can exceed Jolpica's documented hourly ceiling for unauthenticated traffic (historically on the order of ~500/hour). The controller must respect **both** the burst (per-second/per-minute) and the sustained (per-hour) limits simultaneously, throttling to whichever is more restrictive. Confirm current values at the Jolpica documentation at ingestion-build time, as these limits change.
- **Data Normalization Block:** Raw JSON trees must be flattened and committed to interim staging database tables. Data engineers should optimize local structural schemas for the following master tables:

```sql
-- Race results: used for wins, podiums, points, fastest laps
CREATE TABLE staging_race_results (
    id          SERIAL PRIMARY KEY,
    driver_id   VARCHAR(50)  NOT NULL,  -- e.g. 'schumacher'
    constructor_id VARCHAR(50),          -- NULL for career-total queries
    year        INT          NOT NULL,
    round       INT          NOT NULL,
    position    INT,                     -- finish position; NULL = DNF/DNS
    grid        INT,                     -- starting grid position
    fastest_lap BOOLEAN      DEFAULT FALSE,
    points      NUMERIC(5,1) NOT NULL    -- NUMERIC preserves half-points (e.g. 0.5)
);

-- Qualifying results: used for pole position counts (NOT grid position,
-- which diverges from pole whenever a grid penalty is applied)
CREATE TABLE staging_qualifying_results (
    id          SERIAL PRIMARY KEY,
    driver_id   VARCHAR(50)  NOT NULL,
    constructor_id VARCHAR(50),
    year        INT          NOT NULL,
    round       INT          NOT NULL,
    quali_position INT        NOT NULL   -- 1 = pole position
);

CREATE TABLE staging_drivers (
    driver_id   VARCHAR(50) PRIMARY KEY,
    full_name   TEXT        NOT NULL,
    nationality VARCHAR(50),
    active_from INT,
    active_to   INT
);

CREATE TABLE staging_constructors (
    constructor_id VARCHAR(50) PRIMARY KEY,
    name           TEXT        NOT NULL,
    nationality    VARCHAR(50)
);
```

### 2. LLM Context Chunking & Prompt Specification
Rather than introducing entire database records to an LLM context frame—which dramatically inflates hallucination rates—the application utilizes granular data chunking. Data is queried from local staging mirrors in single entity sets (e.g., compiling a single historical driver profile at a time).

#### 2.1 Strict Output Schema Constraint
The generative model must communicate exclusively through an unformatted, rigid JSON schema. The backend prompt controller will reject any responses containing markdown wrappers (such as ` ```json ` block declarations) or leading conversational introductory lines.

#### 2.2 Targeted LLM Output Interface
```json
{
  "question_text": "How many race wins did Michael Schumacher claim during his tenure with Benetton (1991-1995)?",
  "difficulty_weight": 2.5,
  "validation_parameters": {
    "target_entity": "driver",
    "entity_id": "schumacher",
    "filter_constructor_id": "benetton",
    "start_year": 1991,
    "end_year": 1995,
    "metric_target": "wins"
  },
  "proposed_answer": 19
}
```

### 3. The Deterministic Validation Layer
To preserve accuracy across thousands of trivia challenges, every single generated question object must undergo a mandatory automated data verification loop.

> **The Automated Verification Invariant:** The pipeline must never trust the integer supplied in the `proposed_answer` key by the language model. Instead, a dedicated validation module must interpret the structural contents of the `validation_parameters` payload block, re-compile a completely independent programmatic database query, and match outputs.

#### 3.1 Pseudocode Blueprint: The Validation Runner
The runner dispatches on `metric_target` to generalize across all supported statistics. Two important implementation notes:

- **Poles use `staging_qualifying_results`, not `staging_race_results`.** Pole position = qualifying P1. Using `grid=1` from race results diverges whenever a grid penalty is applied, producing wrong counts.
- **`filter_constructor_id` is optional.** Career-total questions (e.g., "Schumacher's total wins across all teams") must not apply a constructor filter. The LLM omits the key entirely for career-total questions; the runner must treat its absence as "no filter."

```python
METRIC_QUERY_MAP = {
    # Source: staging_race_results
    "wins":         ("race_results", lambda q: q.filter(position=1).count()),
    "podiums":      ("race_results", lambda q: q.filter(position__in=[1, 2, 3]).count()),
    "fastest_laps": ("race_results", lambda q: q.filter(fastest_lap=True).count()),
    "points":       ("race_results", lambda q: q.aggregate_sum("points")),  # returns Decimal
    # Source: staging_qualifying_results (NOT race grid — avoids grid-penalty drift)
    "poles":        ("qualifying_results", lambda q: q.filter(quali_position=1).count()),
}

def validate_ai_question(llm_output_json):
    params = llm_output_json["validation_parameters"]
    ai_answer = llm_output_json["proposed_answer"]
    metric = params["metric_target"]

    if metric not in METRIC_QUERY_MAP:
        log_validation_failure(reason="Unsupported metric_target", expected=None, got=metric)
        return False

    source_table, predicate = METRIC_QUERY_MAP[metric]

    # Base filters always applied
    base_query = db.table(f"staging_{source_table}") \
                   .filter(driver_id=params["entity_id"]) \
                   .filter(year__range=(params["start_year"], params["end_year"]))

    # Constructor filter is optional — omitted for career-total questions
    if "filter_constructor_id" in params and params["filter_constructor_id"]:
        base_query = base_query.filter(constructor_id=params["filter_constructor_id"])

    factual_value = predicate(base_query)

    # For points, compare as Decimal to preserve half-point accuracy
    if factual_value == type(ai_answer)(factual_value.__class__(ai_answer)):
        pass  # type-safe equality; implementation detail for the engineer
    if factual_value == ai_answer:
        commit_to_production_db(llm_output_json["question_text"], factual_value)
        return True
    else:
        log_validation_failure(reason="Hallucination detected", expected=factual_value, got=ai_answer)
        return False
```

### 4. Production Schema Serialization
Once verified, questions are transformed into permanent data objects inside the main production repository. Engineers must implement indices on the `is_active`, `game_mode`, and `scheduled_date` keys to guarantee optimal sub-second query evaluation speeds when production servers field client queries.

```sql
CREATE TABLE production_trivia_questions (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    question_string  TEXT         NOT NULL UNIQUE,
    verified_answer  NUMERIC(8,1) NOT NULL,     -- NUMERIC not INT: F1 has half-points races (e.g. 0.5, 12.5)
    difficulty_weight FLOAT       DEFAULT 1.0,  -- matches `difficulty_weight` from LLM output schema (§2.2)
    game_mode        VARCHAR(30)  NOT NULL,      -- 'daily', 'race_week', 'one_shot'
    is_active        BOOLEAN      DEFAULT TRUE,  -- referenced in §4 index requirement
    scheduled_date   DATE         NULL,          -- used by cron systems for daily rotations
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- Indices required for sub-second query performance under production load
CREATE INDEX idx_ptq_game_mode      ON production_trivia_questions (game_mode);
CREATE INDEX idx_ptq_is_active      ON production_trivia_questions (is_active);
CREATE INDEX idx_ptq_scheduled_date ON production_trivia_questions (scheduled_date);
```
