# DECISIONS.md — Architecture Decisions

## LLM Seam Strategy

**Decision: Fake LLM stubs for integration tests; real LLM injected for demo only.**

The pipeline contains two LLM seams:

1. **Signal Detector text path** — `emotional-signal-reader` uses an LLM to parse senior
   students' free-text check-in responses into structured signal objects.
2. **Orchestrator** — uses an LLM to generate `recommended_action` text for urgent students
   in the Daily Brief, and a second LLM call for the LLM-as-judge evaluation layer.

### Rationale

Both seams are wrapped by thin interfaces in `agents/llm_interface.py`:

- `FakeSignalLLM` — deterministic stub that mimics the Anthropic client's
  `messages.create()` API shape. Keyed by exact response text for the three arc-critical
  senior students (S_004, S_003, S_012); falls back to keyword heuristic for all others.
  No API key required.

- `FakeOrchestratorLLM` — deterministic stub for `generate_recommended_action()` (rule-based
  text) and `judge_brief()` (rubric check against student IDs and PII in brief text).

- `RealSignalLLM` / `RealOrchestratorLLM` — `gemini-3.1-flash-lite` wrappers via the
  `google-genai` SDK. Used only in demo/Kaggle notebook runs when `GOOGLE_API_KEY`
  is set. `RealSignalLLM` adapts the Gemini SDK to the Anthropic `messages.create()`
  interface that `reader.py` expects.

### Consequences

- All integration tests (T1–T6) run without credentials. CI is deterministic.
- The demo Kaggle notebook swaps in the real clients by setting `GOOGLE_API_KEY`.
- The two LLM seams are explicit in code — a reader of `orchestrator.py` can see exactly
  where LLM calls occur and swap implementations without touching business logic.

### Platform: Vertex AI Agent Engine → Google AI Studio

The original SPEC targeted Google Vertex AI Agent Engine as the runtime platform.
The implementation uses Google AI Studio (Gemini API) with `GOOGLE_API_KEY`
authentication instead.

**Reason:** Google AI Studio is freely accessible to all Kaggle competition participants
without a GCP project, billing account, or IAM setup — in line with competition §6.b
(equal accessibility requirement). The underlying model and all LLM behaviour are unchanged.

### Model migration: Gemini 2.5 Flash Lite → 3.1 Flash Lite (July 2026)

`gemini-2.5-flash-lite` free tier imposes a 20 requests/day hard cap. The 7-day demo
pipeline makes ~24 API calls total (after batch-signal and action-cache optimisations),
which exceeds the cap and causes a `429 RESOURCE_EXHAUSTED` on Day 3.

Migrated to `gemini-3.1-flash-lite` (500 RPD, 15 RPM on free tier) — well within budget
for the full 7-day run. Drop-in replacement: same SDK, same call signature, same auth.

All references updated to `gemini-3.1-flash-lite` across:
- `agents/llm_interface.py` — `RealSignalLLM` and `RealOrchestratorLLM` default model param
- `skills/emotional-signal-reader/reader.py` — direct `generate_content()` fallback path
- `SPEC.md §8` — Tooling & Library Stack yaml
- `notebook/schoolpulse_demo.ipynb` — setup cell print statement and architecture description

### Arc-label vs. algorithm discrepancy

The `student_registry.json` arc labels (`_arc_label`) were design-time intents written
before the algorithm ran end-to-end. Three students land in a different priority bucket
than their arc label suggests:

- **S_009** (arc: `elevated_late_dip`) and **S_018** (arc: `elevated_inconsistent`) both
  trigger `pattern_break_detected=True` because their strongly-positive 5-day baselines
  make the consecutive-low-day drops exceed the 0.4 delta threshold. The algorithm
  correctly classifies both as urgent.

- **S_003** (arc: `elevated_declining`) goes urgent in the real-LLM run. Gemini scores the
  progressively worsening texts ("Kind of meh", "Pretty low", "Really struggling") below
  the −0.3 low-valence boundary from Day 3 onward, producing 5 consecutive low days by
  Day 7 and triggering the crisis-watch rule (≥ 3). With `FakeSignalLLM`, the keyword
  heuristic scores Days 1–5 above −0.3, giving only 2 consecutive lows → elevated.
  The difference reflects real LLM sensitivity to gradual negative language, not a data bug.

- The Day 7 expected outputs that T2 asserts are derived from actual algorithmic runs with
  `FakeSignalLLM`, not from the arc labels. The arc labels remain as human-readable design
  notes.

### API call optimisation: batch signals + elevated action cache (July 2026)

**Problem:** The original per-student pipeline made ~90 API calls across a 7-day run,
hitting the 15 RPM free-tier limit and producing 429 RESOURCE_EXHAUSTED errors on busy days.

**Three structural changes made:**

1. **Batch senior signal detection** — `_batch_prefetch_signals()` collects all senior
   students' sanitised text responses and sends them in a single `signal_batch` LLM call
   per day, then distributes results via `text_signal_cache`. Reduced from N calls/day
   (one per senior student) to 1 call/day. Privacy Guard runs first so no PII reaches
   the batch prompt.

2. **Elevated student referral actions — rule-based, no LLM** — Elevated students'
   `recommended_action` text in the referral log is generated by `_elevated_action()`,
   a deterministic template method on the orchestrator. LLM calls are reserved for
   urgent students where the recommendation needs to be specific and contextual. This
   saves 1–2 calls/day on days with elevated (but not urgent) students, with no
   meaningful quality loss: elevated students are on a watch list, not immediate referral.

3. **Action cache across brief assembly and referral writing** — Urgent students'
   recommended actions are generated once in `_assemble_brief()` and cached in
   `self._action_cache`. `_write_referrals()` reads from the cache, eliminating
   duplicate calls for the same student in the same day.

**Result:** ~24 API calls for a full 7-day run (from ~90), well within 15 RPM and
500 RPD free-tier limits.

### ADK Workflow graph wrapping the pipeline (July 2026)

**Decision: Wrap the full pipeline as a Google ADK 2.x `Workflow` graph in `adk_workflow.py`.**

The orchestrator already coordinates the pipeline correctly as pure Python. ADK wrapping adds an explicit, inspectable graph structure on top of it:

- **`FunctionNode`** — used for the three deterministic phases (Privacy Guard, Signal Detector, Memory Keeper). Zero-arg closures capture the per-day context and run each phase as a graph node.
- **`LlmAgent`** — used for the HITL gate node (`schoolpulse_hitl`), signalling that this step involves an LLM-backed decision boundary.
- **`Edge` + `START`** — wires the nodes in sequence: `START → pg_node → sm_node → bj_node → hitl_node`.

**Rationale:** The competition whitepaper requires demonstrating ADK integration. Wrapping at this layer avoids modifying any existing agent logic — `adk_workflow.py` is an alternative entry point that calls the same orchestrator internally, so tests and the notebook demo can continue using the direct path.

**Consequences:**
- `run_one_day_via_adk()` provides a second callable entry point (used by `app.py` Cloud Run endpoint)
- `build_workflow()` makes the graph structure inspectable — callers can iterate `wf.edges` to print the topology
- All integration tests (T1–T6) continue to exercise the direct orchestrator path — no duplication needed

---

### MCP layer: local stdio server + production Google Sheets path (July 2026)

**Decision: Implement `mcp_server.py` (FastMCP, stdio transport) for the demo and wire `mcp_config.json` to point to `@modelcontextprotocol/server-gdrive` for the production path.**

The competition whitepaper's MCP section establishes a **consumption-over-creation** principle: projects should consume existing MCP servers rather than building bespoke wrappers. SchoolPulse applies this in two layers:

1. **Demo path** (`schoolpulse-local` entry in `mcp_config.json`): `mcp_server.py` exposes the four synthetic JSON fixtures as MCP tools with identical signatures to the production path (`get_daily_checkins`, `get_teacher_observations`, `get_student_registry`, `list_available_dates`). This lets the notebook demonstrate the MCP protocol boundary without a live Google Sheets connection.

2. **Production path** (`google-sheets-mcp` entry in `mcp_config.json`): the official `@modelcontextprotocol/server-gdrive` MCP server replaces `mcp_server.py`. No code change is needed — same four tool names, same input/output shapes, same pipeline entry point.

**Rationale:** Privacy Guard runs immediately after MCP ingestion — student names and contact details are stripped before any data enters an LLM context window. Routing data through the MCP boundary first makes this trust boundary explicit: the pipeline never touches raw Google Sheets data directly.

**Consequences:**
- `mcp_config.json` can be dropped into `~/.gemini/config/` to wire either path into Gemini CLI / Antigravity with no code changes
- `mcp_server.py` uses `__file__`-relative paths so it runs correctly regardless of working directory (fixed during integration after a FileNotFoundError when invoked from the notebook directory)
- The four MCP tool signatures are stable — swapping demo ↔ production is a config change only

---

### HITL OVERRIDE scenario uses deterministic stubs (July 2026)

Cell 10 (Scenario B: OVERRIDE_NO_ACTION) originally used the same real LLM clients
as the main 7-day run, causing 7 redundant API calls that duplicated Day 7's pipeline
output — only to immediately discard it when OVERRIDE fires.

**Decision:** Cell 10's `override_orch` uses `FakeSignalLLM` + `FakeOrchestratorLLM`.
The scenario demonstrates HITL blocking behavior, not LLM output quality. Student
priorities (urgent/elevated) are determined by Memory Keeper's deterministic algorithm
running on the seeded Day 6 memory snapshot — not by LLM outputs. The HITL guarantee
(`_write_referrals()` never called without `APPROVE_AND_LOG`) is independent of which
LLM backend is used.
