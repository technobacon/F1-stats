# TECHNICAL PIPELINE SPECS
## Automated Ingestion & Anti-Hallucination Pipeline
**Module:** Data Engineering Core
**Target Audience:** Backend Developers / Data Engineers

### 1. The Raw ETL Ingestion Engine
The ingestion pipeline acts as an isolated, asynchronous system responsible for transforming highly nested external API entities into relational database matrices. To safeguard production systems against downtime, direct runtime execution loops during user client sessions are strictly prohibited.

#### 1.1 API Ingestion Framework
- **Endpoint Targeting:** Primary historical extractions must map against the Jolpica F1 API endpoints (e.g., `http://api.jolpi.ca/ergast/f1/drivers/`).
- **Rate-Limit Strategy:** Implement a sliding-window token bucket controller. Outbound API requests must be limited to a maximum of 40 requests per minute. Violations caught via `429 Too Many Requests` statuses must halt the pipeline for 300 seconds sequentially.
- **Data Normalization Block:** Raw JSON trees must be flattened and committed to interim staging database tables. Data engineers should optimize local structural schemas specifically for the following master tables: `staging_drivers`, `staging_constructors`, and `staging_race_results`.

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
```python
def validate_ai_question(llm_output_json):
    params = llm_output_json["validation_parameters"]
    ai_answer = llm_output_json["proposed_answer"]

    # Construct a programmatic, deterministic query against trusted data
    factual_count = db.table("staging_race_results") \
                      .filter(driver_id=params["entity_id"]) \
                      .filter(constructor_id=params["filter_constructor_id"]) \
                      .filter(year__range=(params["start_year"], params["end_year"])) \
                      .filter(position=1) \
                      .count()

    # Rigid Boolean Verification Gate
    if factual_count == ai_answer:
        # Move records smoothly into production storage
        commit_to_production_db(llm_output_json["question_text"], factual_count)
        return True
    else:
        # Log AI miscalculations cleanly to diagnostic registers and discard
        log_validation_failure(reason="Hallucination detected", expected=factual_count, got=ai_answer)
        return False
```

### 4. Production Schema Serialization
Once verified, questions are transformed into permanent data objects inside the main production repository. Engineers must implement indices on the `is_active`, `game_mode`, and `scheduled_date` keys to guarantee optimal sub-second query evaluation speeds when production servers field client queries.

```sql
CREATE TABLE production_trivia_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_string TEXT NOT NULL UNIQUE,
    verified_answer INT NOT NULL,
    difficulty_scalar FLOAT DEFAULT 1.0,
    game_mode VARCHAR(30) NOT NULL, -- 'daily', 'race_week', 'one_shot'
    scheduled_date DATE NULL,       -- Used by cron systems for daily rotations
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
