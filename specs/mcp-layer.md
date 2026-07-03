---
component: mcp-layer
type: mcp-integration
framework: Google ADK MCP client wrapper
mcp-server: Google Sheets MCP
course-concept: MCP — Model Context Protocol
downstream: privacy-guard      # raw records flow to Privacy Guard first
---

# MCP Layer

Ingests raw daily check-in data and teacher observation notes from
Google Sheets via the Model Context Protocol (MCP). This is the
system's only external data interface. All records emitted from this
layer are raw and unsanitised — they flow immediately to Privacy Guard
before any other component sees them.

---

## Role

Provide a structured, protocol-standard data ingestion interface between
the Google Sheets data source and the pipeline. Abstracts sheet-access
details so sub-agents see clean records, not spreadsheet mechanics.

---

## MCP integration details

```yaml
mcp:
  protocol: Model Context Protocol (MCP)
  server: Google Sheets MCP
  client: Google ADK MCP client wrapper
  transport: stdio (local demo) / SSE (production)
  auth: OAuth2 service account (production); mock in Kaggle notebook demo
```

---

## Data sources

```yaml
sources:
  student_checkins:
    sheet: "daily_checkins"
    columns:
      - student_name: string     # real name — Privacy Guard will replace with ID
      - student_id: string
      - age_group: enum [junior, senior]
      - date: ISO8601
      - emoji_sequence: string   # junior only
      - prompt: string           # senior only
      - response: string         # senior only
    read_frequency: once per pipeline run (morning batch)

  teacher_observations:
    sheet: "teacher_observations"
    columns:
      - student_name: string
      - student_id: string
      - date: ISO8601
      - note: string
      - flag_level: enum [none, watch, concern]
      - teacher_name: string    # present in sheet; stripped before pipeline emission
    read_frequency: once per pipeline run (morning batch)
    note: teacher_name is removed from the record by MCP layer before passing
          to Privacy Guard — it never enters the pipeline payload
```

---

## Record assembly

The MCP layer joins `student_checkins` and `teacher_observations` on
`student_id` + `date` to produce a single composite record per student
per day before passing to Privacy Guard:

```yaml
composite_record:
  student_name: string         # carries forward for Privacy Guard to replace
  student_id: string
  age_group: enum [junior, senior]
  date: ISO8601
  junior_input:
    emoji_sequence: string | null
  senior_input:
    prompt: string | null
    response: string | null
  teacher_observation:
    note: string | null
    flag_level: enum [none, watch, concern]
    # teacher_name: REMOVED here — not passed downstream
```

---

## Kaggle notebook demo mode

In the notebook demo, the MCP layer is simulated using static JSON files:

```yaml
demo_mode:
  source: data/synthetic_checkins.json + data/teacher_observations.json
  mcp_simulation: ADK mock MCP tool returning records from JSON files
  auth: not required
  note: the MCP interface contract (record schema, join logic) is identical
        to production; only the transport differs
```

This allows the full pipeline to run in a Kaggle notebook without live
Google Sheets access while still demonstrating the MCP concept correctly.

---

## Production deployment path

```yaml
production:
  platform: Google Vertex AI Agent Engine
  mcp_server: Google Sheets MCP (cloud-hosted)
  auth: OAuth2 service account with read-only Sheets scope
  scheduling: Cloud Scheduler — 08:00 daily school-day trigger
  upgrade_path:
    memory_store: in-process dict → Firestore
    mcp_transport: stdio → SSE
    scaling: one pipeline run per school cohort
```

---

## Error handling

```yaml
errors:
  sheet_unreachable:
    action: abort pipeline run; log error; alert Orchestrator

  missing_student_in_checkins:
    action: student is absent for the day; no record emitted; log absence

  malformed_record:
    action: skip record; log malformation; continue batch

  teacher_observation_without_matching_checkin:
    action: emit teacher_observation-only record; signal-reader handles this modality
```

---

## See also

- `specs/privacy-guard.md` — immediate downstream consumer of all MCP records
- `specs/orchestrator.md` — owns the MCP layer invocation trigger
- `SPEC.md` §6 — Course concept mapping (MCP)
- `SPEC.md` §7 — Synthetic dataset schema
- `SPEC.md` §8 — Tooling stack
