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

## Implementation

```yaml
implementation:
  server: mcp_server.py          # FastMCP (mcp>=1.0.0, Python SDK)
  transport: stdio (demo) / SSE HTTPS (production)
  config: mcp_config.json        # two entries: schoolpulse-local + google-sheets-mcp
  tools_exposed:
    - get_daily_checkins(date)         # -> list[dict] filtered by date
    - get_teacher_observations(date)   # -> list[dict], teacher_name stripped
    - get_student_registry()           # -> list[dict], no PII
    - list_available_dates()           # -> list[str] discovery helper
  principle: >
    Follows whitepaper "consumption over creation" — mcp_config.json wires to
    the official @modelcontextprotocol/server-gdrive for production Google Sheets
    access rather than a bespoke REST wrapper.
```

## Demo mode

The `schoolpulse-local` entry in `mcp_config.json` invokes `mcp_server.py` via
stdio. It serves `data/synthetic/` JSON files through the same tool interface the
production Google Sheets server would expose — the record schema and join logic
are identical; only the transport differs.

```bash
# Start the server (stdio — mcp_config.json calls this automatically)
python mcp_server.py

# Visual inspection via MCP Inspector
mcp dev mcp_server.py
```

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
