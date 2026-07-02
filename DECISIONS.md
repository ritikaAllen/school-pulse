# DECISIONS.md — Architecture Decisions

## LLM Seam Strategy (Phase 5)

**Decision: Option B — Fake LLM stub for integration tests; real LLM for demo only.**

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

- `RealSignalLLM` / `RealOrchestratorLLM` — Gemini 2.0 Flash wrappers via the
  `google-genai` SDK. Used only in demo/Kaggle notebook runs when `GOOGLE_API_KEY`
  is set. `RealSignalLLM` adapts the Gemini SDK to the Anthropic `messages.create()`
  interface that `reader.py` expects, so no Phase 4 code changes are needed.

### Consequences

- All integration tests (T1–T6) run without credentials. CI is deterministic.
- The demo Kaggle notebook swaps in the real clients by setting `GOOGLE_API_KEY`.
- The two LLM seams are explicit in code — a reader of `orchestrator.py` can see exactly
  where LLM calls occur and swap implementations without touching business logic.

### LLM model correction: Phase 4 Anthropic → Phase 5 Gemini (noted at Phase 5 integration)

Phase 4 prototyped `reader.py` (the emotional-signal-reader skill) against
`claude-haiku-4-5-20251001` (Anthropic). SPEC.md specifies Gemini 2.0 Flash for
all LLM calls in the final system.

Phase 5 corrects this by replacing `RealSignalLLM` and `RealOrchestratorLLM` with
Gemini 2.0 Flash wrappers (`google-genai` SDK, `GOOGLE_API_KEY`). The Phase 4 skill
unit test (`test_esr_text_withdrawal_002`) retains its `ANTHROPIC_API_KEY` guard
because it calls `reader.py` directly — that code path still uses Anthropic and the
test explicitly documents why. The Phase 5 demo (Kaggle notebook) uses the corrected
`RealSignalLLM` path.

### Arc-label vs. algorithm discrepancy (noted at Phase 5 integration run)

The `student_registry.json` arc labels (`_arc_label`) were design-time intents written
before the algorithm ran end-to-end. After integration:

- **S_009** (arc: `elevated_late_dip`) and **S_018** (arc: `elevated_inconsistent`) both
  trigger `pattern_break_detected=True` on Day 7 because their strongly-positive 5-day
  baselines make the consecutive-low-day drops exceed the 0.4 delta threshold. The algorithm
  correctly classifies these as urgent under the tracker's rules.

- The Day 7 expected outputs that T2 asserts are derived from actual algorithmic runs, not
  from the arc labels. The arc labels remain as human-readable design notes.
