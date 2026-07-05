# AGENTS.md — SchoolPulse Project Agent Configuration

Shared multi-tool config for Gemini CLI, Antigravity, and any other
compliant coding agent working in this repository.

---

## Project identity

**SchoolPulse** is a multi-agent mental-health-first-responder pipeline.
It processes student check-in data daily, detects emotional signals,
tracks trends over a 7-day rolling window, and produces a counselor brief
that requires human sign-off before any referral is written.

- Language: Python 3.11
- LLM: Gemini via `google-genai` SDK (`gemini-3.1-flash-lite`, free tier)
- Auth: `GOOGLE_API_KEY` environment variable — never hardcode
- Test runner: `pytest tests/ -v` (33 pass without credentials, 1 skipped)

---

## Pipeline order — do not reorder

```
Privacy Guard → Signal Detector → Memory Keeper → Orchestrator → HITL gate
```

No data reaches an LLM context window without passing through Privacy Guard
first. This is a hard architectural rule enforced by the Orchestrator.

---

## Code conventions

- LLM seams are injected via `signal_llm` / `orchestrator_llm` constructor
  params — never instantiate `RealSignalLLM` / `RealOrchestratorLLM` inside
  agent code. Use `FakeSignalLLM` / `FakeOrchestratorLLM` in tests.
- Student identity: always `student_id` (e.g. `S_042`), never real names.
  Real names exist only in `data/synthetic/student_registry.json` and are
  the first thing Privacy Guard strips.
- No secrets in source: API keys via `.env` (gitignored).
- Specs live in `SPEC.md` (system spec) and `specs/` (per-agent specs).
  Read the relevant spec before modifying agent logic.

---

## Skills catalog

Three Antigravity-format skills are available in `.agents/skills/`.
Each has a `SKILL.md` with trigger description, step-by-step workflow,
anti-patterns, and EDD eval cases.

| Skill | Trigger | Owned by |
|---|---|---|
| `emotional-signal-reader` | Parse raw check-in record into structured signal | Signal Detector |
| `student-trend-tracker` | Integrate signal into rolling memory, emit trend report | Memory Keeper |
| `pii-context-sanitizer` | Strip PII from any record before it enters an LLM context | Privacy Guard |

**Load order:** `pii-context-sanitizer` → `emotional-signal-reader` → `student-trend-tracker`.
Never route a raw record to `emotional-signal-reader` without sanitizing first.

---

## What NOT to do

- Do not modify `tests/` and `agents/` in the same change — keep test
  integrity as an independent baseline.
- Do not add LLM calls inside `privacy_guard.py`, `memory_keeper.py`, or
  `signal_detector.py` — LLM access is exclusively via `llm_interface.py`.
- Do not write student names, emails, or contact info to any log file.
- Do not increase the `logs/` gitignore exclusion — log files are intentionally
  excluded from version control except `api_calls.log` on Kaggle runs.
