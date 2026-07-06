# SchoolPulse — Mental Health First Responder Agent

> Kaggle AI Agents Intensive · Agents for Good Capstone · July 2026

Schools measure what they value. Every student has a grade, an attendance record, a test score. But ask a school to show you their mental wellness data and most will have nothing — not because they don't care, but because no infrastructure exists to collect it.

SchoolPulse is a first step toward building that infrastructure. A lightweight daily check-in. A 7-day emotional trend per student. A synthesized brief that tells a counselor which students need attention today, and why — backed by data, not guesswork. Over time, this data becomes the foundation for integrating mental wellness programs into schools and measuring whether they work.

**The system never acts on a student autonomously.** Every referral requires explicit counselor sign-off. The agents surface patterns; the human makes the call.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  ORCHESTRATOR AGENT                  │
│          (coordinates pipeline, owns HITL gate)      │
└───────────────────────┬──────────────────────────────┘
                        │
          ┌─────────────┼─────────────────┐
          ▼             ▼                 ▼
  ┌──────────────┐ ┌───────────────┐ ┌──────────────┐
  │ PRIVACY      │ │ SIGNAL        │ │ MEMORY       │
  │ GUARD        │ │ DETECTOR      │ │ KEEPER       │
  │ [skill]      │ │ [skill]       │ │ [skill]      │
  │ pii-context- │ │ emotional-    │ │ student-     │
  │ sanitizer    │ │ signal-reader │ │ trend-tracker│
  └──────┬───────┘ └───────┬───────┘ └──────┬───────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
                   ┌───────────────┐
                   │ DAILY BRIEF   │
                   │ + LLM-AS-JUDGE│
                   └───────┬───────┘
                           ▼
                   ┌───────────────┐
                   │  HITL GATE    │  APPROVE_AND_LOG
                   │  (counselor)  │  OVERRIDE_NO_ACTION
                   └───────────────┘  REQUEST_MORE_CONTEXT
```

### Pipeline flow

The orchestrator runs in three phases per day, then assembles the brief:

1. **Privacy Guard** · `pii-context-sanitizer` (all students) — strips PII before any data enters an LLM context: student names replaced with IDs, contact info redacted, teacher notes NER-cleaned.
2. **Signal Detector** · `emotional-signal-reader` (one batch LLM call per day) — converts sanitized check-ins into structured signals: `emotional_valence`, `energy_level`, `social_withdrawal_flag`, `distress_keywords_detected`. Junior students use an emoji-to-affect lookup table; senior students' text responses are batched into a single `gemini-3.1-flash-lite` call, then distributed per student.
3. **Memory Keeper** · `student-trend-tracker` (per student) — integrates the signal into a 7-day rolling window, recomputes the baseline, and sets `recommended_priority` (`routine` / `elevated` / `urgent`) based on pattern-break detection and consecutive low-day counts.
4. **Orchestrator** assembles a PII-free Daily Brief (LLM call per urgent student), runs an LLM-as-judge evaluation (5-criterion rubric, pass threshold 0.75), then presents the brief to the counselor via the HITL gate.
5. **HITL gate** halts the pipeline. No referral is written without `APPROVE_AND_LOG`. Every `OVERRIDE_NO_ACTION` is logged to the audit trail.

---

## How SchoolPulse runs in a real school

> This section is for anyone reading the project for the first time. It explains what a production deployment looks like day-to-day, and how the 7-day demo in the notebook maps to that reality.

### The daily rhythm

Every school day, the pipeline runs **once** — typically in the morning after students have submitted their check-ins. Here is what a single day's run looks like:

```
8:00 am  Students submit check-ins
         ↳ Juniors: tap 2 emojis in the school app
         ↳ Seniors: type a short free-text response

8:30 am  Scheduled pipeline run (Cloud Scheduler / cron)
         │
         ├─ 1. Load today's check-ins via MCP (Google Sheets)
         │
         ├─ 2. Privacy Guard
         │      Strip all student names → anonymised IDs
         │      Redact contact info and teacher names from notes
         │
         ├─ 3. Signal Detector
         │      Juniors : emoji → valence lookup table  (no LLM, instant)
         │      Seniors : one batched Gemini call for all 10 free-text responses
         │                → emotional_valence, energy_level, withdrawal_flag
         │
         ├─ 4. Memory Keeper  (per student)
         │      Load previous 6 days from the database
         │      Append today's signal → 7-day rolling window
         │      Compute rolling baseline, pattern-break detection
         │      Set priority: routine / elevated / urgent
         │
         ├─ 5. Daily Brief assembly
         │      Urgent students → one Gemini call each for recommended action
         │      Elevated students → rule-based watch entry (no LLM)
         │      LLM-as-judge evaluates the full brief (must score ≥ 0.75)
         │
         └─ 6. HITL gate
                Counselor reviews the brief
                APPROVE_AND_LOG  → referrals written, timestamped
                OVERRIDE_NO_ACTION → override logged to audit trail
                REQUEST_MORE_CONTEXT → Memory Keeper queried, no re-run

8:35 am  Counselor receives the daily brief on their dashboard
```

### What stays the same every day vs. what's new

| Component | Each day |
|---|---|
| Privacy Guard | Runs on that day's new check-ins only |
| Signal Detector — juniors | Instant lookup, no LLM, no cost |
| Signal Detector — seniors | **One batch LLM call** for today's 10 new responses |
| Memory Keeper | Reads persisted history, appends today, saves back |
| Recommended action | One LLM call per urgent student (zero if no urgency) |
| LLM-as-judge | One LLM call per day |
| **Minimum LLM calls/day** | **2** (signal batch + judge, zero urgent students) |
| **Typical LLM calls/day** | **3–5** (batch + 1–3 urgent actions + judge) |

Old check-in text is **never re-scored**. Once a day's signals are stored in Memory Keeper, they persist as numbers — no raw text is kept in the rolling window.

### How the notebook demo maps to this

The notebook's Cell 5 simulates 7 school days in one sitting by looping through dates. Each iteration of the loop is exactly one real-world daily run. The only difference from production is that the demo's Memory Keeper state lives in a Python dict that disappears when the kernel restarts — a real deployment would persist it to a database (Firestore, Postgres, etc.) so each morning's run can read the previous days automatically.

### LLM cost at school scale

A school with 500 students (250 juniors + 250 seniors):
- Signal batch: 25 Gemini calls/day (250 seniors ÷ 10 per batch)
- Urgent actions: typically 2–5 calls/day
- Judge: 1 call/day
- **Total: ~28–31 Gemini Flash calls per school day**

At Gemini Flash pricing, this is well under $1/day for a full school.

---

## Project layout

```
school-pulse/
├── agents/
│   ├── orchestrator.py         # Pipeline coordinator + HITL gate
│   ├── privacy_guard.py        # PII sanitization agent
│   ├── signal_detector.py      # Emotional signal extraction agent
│   ├── memory_keeper.py        # Rolling trend memory agent
│   ├── adk_workflow.py         # ADK 2.x Workflow graph wrapping the pipeline
│   └── llm_interface.py        # LLM seam abstraction (Fake* / Real*)
├── skills/                     # Skill implementations (called by agents)
│   ├── emotional-signal-reader/
│   │   ├── SKILL.md            # Skill spec: workflow, anti-patterns, eval cases
│   │   ├── reader.py           # Emoji lookup table + Gemini text parse
│   │   └── references/
│   │       └── emoji_affect_table.md
│   ├── student-trend-tracker/
│   │   ├── SKILL.md
│   │   ├── tracker.py          # 7-day rolling window + priority rules
│   │   └── references/
│   │       └── priority_decision_tree.md
│   └── pii-context-sanitizer/
│       ├── SKILL.md
│       ├── sanitizer.py        # NER + regex PII redaction
│       └── references/
│           └── pii_redaction_patterns.md
├── .agents/                    # Antigravity-format skill registry
│   ├── AGENTS.md               # Agent roster for Antigravity / Gemini CLI
│   └── skills/                 # Mirrors skills/ — SKILL.md + references/ only
│       ├── emotional-signal-reader/
│       ├── student-trend-tracker/
│       └── pii-context-sanitizer/
├── specs/                      # Per-component detailed specs
│   ├── hitl-gate.md
│   ├── mcp-layer.md
│   ├── memory-keeper.md
│   ├── orchestrator.md
│   ├── privacy-guard.md
│   └── signal-detector.md
├── data/synthetic/
│   ├── student_registry.json   # 20 students, ID ↔ age_group mapping
│   ├── synthetic_checkins.json # 20 students × 7 days of check-ins
│   └── teacher_observations.json
├── notebook/
│   └── schoolpulse_demo.ipynb  # Full demo walkthrough (Kaggle submission)
├── tests/
│   ├── test_privacy_guard.py   # 7 tests
│   ├── test_signal_detector.py # 9 tests
│   ├── test_memory_keeper.py   # 12 tests
│   └── test_orchestrator.py    # 6 integration tests (T1–T6)
├── logs/                       # API call logs (auto-generated, gitignored)
├── app.py                      # FastAPI server: GET /health, POST /run
├── Dockerfile                  # Cloud Run–compatible container
├── mcp_server.py               # Local stdio MCP server (demo path, FastMCP)
├── mcp_config.json             # MCP client config (local stdio + Google Sheets MCP)
├── SPEC.md                     # Full system specification
├── DECISIONS.md                # Architecture decisions and trade-offs
└── requirements.txt
```

---

## Quickstart

**Prerequisites:** Python 3.11+, spaCy `en_core_web_sm` model.

```bash
cd school-pulse
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

For the demo run and live LLM tests you also need a **Google AI Studio API key** — get one free at [aistudio.google.com](https://aistudio.google.com).

Set it in your environment before running anything that calls Gemini:

```bash
# macOS / Linux
export GOOGLE_API_KEY=your-key-here

# Windows PowerShell
$env:GOOGLE_API_KEY = "your-key-here"
```

Or add it to a `.env` file in the `school-pulse/` directory — the notebook reads it automatically:

```
GOOGLE_API_KEY=your-key-here
```

For MCP Inspector (`mcp dev mcp_server.py`) two additional prerequisites apply:
- **Node.js ≥ 22.7.5** — `winget install OpenJS.NodeJS.22` (Windows) or download from [nodejs.org](https://nodejs.org)
- **uv** — `pip install uv` (used by `mcp dev` as the Python runner)

### Run the integration tests (no API key required)

33 tests use deterministic `Fake*` stubs — no credentials needed. 1 test exercises the live Gemini text path and is skipped when no key is present.

```bash
# Run from school-pulse/ directory
pytest tests/ -v
# 33 passed, 1 skipped
```

The skipped test (`test_esr_text_withdrawal_002`) calls `reader.py`'s Gemini path directly. It runs when `GOOGLE_API_KEY` is set:

```bash
# macOS / Linux
GOOGLE_API_KEY=<your-key> pytest tests/test_signal_detector.py -v

# Windows PowerShell
$env:GOOGLE_API_KEY = "<your-key>"; pytest tests/test_signal_detector.py -v
```

### Demo run — direct orchestrator (full pipeline with real LLM)

`run_sequential_days()` is a convenience wrapper that calls `run_batch()` for each date in sequence —
the same pipeline the notebook runs in Cell 5, without the per-day formatted output.

> **Run from the `school-pulse/` directory** — `Path("data/synthetic")` is relative.
> `GOOGLE_API_KEY` must be set in your environment.
> `hitl_callback=None` triggers an interactive CLI prompt after each day's brief —
> you will be asked to enter `1` (approve), `2` (request context), or `3` (override)
> **7 times** (once per day). To skip the prompts and auto-approve, replace
> `hitl_callback=None` with `hitl_callback=lambda *a: 'APPROVE_AND_LOG'`.

```bash
cd school-pulse
```

```python
from pathlib import Path
from agents.orchestrator import SchoolPulseOrchestrator, load_data, DEMO_DATES
from agents.llm_interface import RealSignalLLM, RealOrchestratorLLM

checkins, teacher_obs, registry = load_data(Path("data/synthetic"))

orchestrator = SchoolPulseOrchestrator(
    signal_llm=RealSignalLLM(),            # reads GOOGLE_API_KEY from environment
    orchestrator_llm=RealOrchestratorLLM(),
    hitl_callback=None,                    # interactive CLI prompt — see note above
)
results = orchestrator.run_sequential_days(DEMO_DATES, checkins, teacher_obs, registry)
print("Done —", len(results), "days processed")
```

### Demo run — MCP server (local stdio)

The easiest way to see the MCP tools in action is **notebook Cell 4**, which imports
`mcp_server.py` directly and calls all four tools — no Node.js required.

For interactive inspection via MCP Inspector:

> **Prerequisites:** Node.js ≥ 22.7.5 and `uv` (`pip install uv`) must both be installed.
> `mcp dev` uses `uv` as its Python runner and the MCP Inspector requires Node.js ≥ 22.7.5.

```bash
# Requires Node.js ≥ 22.7.5 and uv (pip install uv)
mcp dev mcp_server.py
```

The server also runs in raw stdio mode, invoked automatically by an MCP client
via `mcp_config.json`. Running it standalone is not useful — it will appear to
hang because it is waiting for JSON-RPC messages on stdin:

```bash
python mcp_server.py   # appears to hang — expected; exit with Ctrl+C
```

The server exposes four tools: `get_daily_checkins(date)`, `get_teacher_observations(date)`, `get_student_registry()`, `list_available_dates()`. Copy `mcp_config.json` to `~/.gemini/config/mcp_config.json` to wire it into Gemini CLI / Antigravity.

### ADK Workflow graph

The ADK integration lives in [`agents/adk_workflow.py`](agents/adk_workflow.py). It wraps the pipeline as a Google ADK 2.x `Workflow` graph:

| Node | ADK type | What it does |
|---|---|---|
| `privacy_guard` | `FunctionNode` | Phase 1 — PII masking + NER redaction |
| `signal_and_memory` | `FunctionNode` | Phases 2–3 — batch signal detection + Memory Keeper |
| `brief_and_judge` | `FunctionNode` | Phase 4 — Daily Brief assembly + LLM-as-judge |
| `schoolpulse_hitl` | `LlmAgent` | HITL gate — APPROVE / OVERRIDE / REQUEST_MORE_CONTEXT |

```
START → privacy_guard → signal_and_memory → brief_and_judge → schoolpulse_hitl
```

The three `FunctionNode`s use zero-argument closures bound to a shared `_PipelineCtx` object so pipeline state (sanitised records, trend reports, brief text) flows between nodes without going through ADK session state. The `LlmAgent` HITL node requires an ADK `Runner` for interactive use.

For end-to-end results with the full 7-day rolling memory, use the notebook (Cell 5) or the direct orchestrator path above — those show the meaningful urgent/elevated output. The ADK path is the production entry point wired in `app.py` (`run_one_day_via_adk`).

---

## LLM seam abstraction

`agents/llm_interface.py` wraps both LLM seams behind thin interfaces so integration tests run deterministically without credentials. The demo path swaps in real Gemini calls by injecting `RealSignalLLM` / `RealOrchestratorLLM`.

| Class | Used by | Credentials |
|---|---|---|
| `FakeSignalLLM` | All integration tests (T1–T6) | None |
| `FakeOrchestratorLLM` | All integration tests (T1–T6) | None |
| `RealSignalLLM` | Demo / Kaggle notebook | `GOOGLE_API_KEY` |
| `RealOrchestratorLLM` | Demo / Kaggle notebook | `GOOGLE_API_KEY` |

---

## Test suite

| File | Tests | What they cover |
|---|---|---|
| `test_privacy_guard.py` | 7 | PII redaction, boundary checks, name NER |
| `test_signal_detector.py` | 9 | Emoji path, text path, merge, schema roundtrip |
| `test_memory_keeper.py` | 12 | Pattern break, consecutive low, rolling window, cross-student boundary |
| `test_orchestrator.py` | 6 | T1 smoke, T2 full batch, T3 judge failure, T4 OVERRIDE, T5 REQUEST_MORE_CONTEXT, T6 7-day sequential |

---

## Course concepts covered

| Concept | Where in this project |
|---|---|
| Multi-Agent Systems | Four cooperating agents (Privacy Guard, Signal Detector, Memory Keeper, Orchestrator) with explicit trust boundaries and a shared pipeline contract |
| ADK (Agent Development Kit) | `agents/adk_workflow.py` wraps the full pipeline as a Google ADK 2.x `Workflow` graph — `FunctionNode`s for deterministic phases, an `LlmAgent` HITL node, wired via `Edge` + `START`. `google-adk>=2.0.0` in `requirements.txt` |
| Agent Skills (Antigravity) | Three Antigravity-format skills in `skills/`: `emotional-signal-reader`, `student-trend-tracker`, `pii-context-sanitizer` — each with a `SKILL.md` (name, description, step-by-step workflow, anti-patterns, eval cases) and a `references/` folder for lookup assets |
| MCP (Model Context Protocol) | `mcp_server.py` — runnable stdio MCP server (FastMCP) exposing `get_daily_checkins`, `get_teacher_observations`, `get_student_registry`, `list_available_dates` tools backed by `data/synthetic/`. `mcp_config.json` wires both the local stdio server (demo) and the official `@modelcontextprotocol/server-gdrive` (production Google Sheets path). Follows whitepaper's *consumption-over-creation* principle. |
| Long-Term Memory | Memory Keeper: 7-day rolling per-student window with baseline tracking |
| LLM-as-Judge Evaluation | 5-criterion rubric (PII-free, student-specific, actionable, severity-matched, counselor-appropriate) evaluated on every Daily Brief; pass threshold 0.75 |
| Context Hygiene / Security | Privacy Guard: student names replaced with IDs before any LLM context window sees them, NER-based redaction on teacher notes, 7-day rolling context window cap, hard cross-student boundary enforcement with `BOUNDARY_VIOLATION` logging |
| Human-in-the-Loop (HITL) | Orchestrator halts before any high-stakes action — no referral written without `APPROVE_AND_LOG`; every `OVERRIDE_NO_ACTION` written to audit trail; execution trajectory logged to `logs/api_calls.log` |
| Deployability | `Dockerfile` + `app.py` (FastAPI, Cloud Run–compatible): `POST /run` calls the ADK Workflow pipeline; `GET /health` for liveness; `docker build -t schoolpulse . && docker run -p 8080:8080 schoolpulse` |

---

## Synthetic dataset

20 students · 7 days · 2 age groups (10 junior emoji / 10 senior text)

**Day 7 pipeline output** (what the 7-day notebook run with live Gemini actually produces):

| Priority | Count | Students |
|---|---|---|
| `urgent` | 5 | S_004, S_003, S_009, S_017, S_018 |
| `elevated` | 2 | S_007, S_012 |
| `routine` | 13 | all remaining |

### What each arc demonstrates

**Urgent students**

| Student | Age | Pattern | Why urgent |
|---|---|---|---|
| S_004 (senior) | 15 | Stable positive Days 1–6, single severe crash Day 7 with distress language | Pattern break: baseline +0.27, Day 7 valence −0.80 → delta 1.07 >> 0.4 threshold |
| S_003 (senior) | 14 | Slow steady decline across 7 days | 5 consecutive low days by Day 7 — Gemini scores progressively negative texts ("Kind of meh", "Pretty low", "Really struggling") below −0.3 boundary from Day 3 onward → CRISIS_WATCH_TRIGGER (≥3) fires |
| S_017 (junior) | 8 | Mildly positive Days 1–3, strongly negative emoji Days 4–7 (😭😔😶) | Consecutive low days = 4 by Day 7; pattern break also fires on Day 4 |
| S_009 (junior) | 7 | Consistently positive Days 1–5, then strong negative emoji Days 6–7 (😢😴😠) | Pattern break: baseline +0.46, dip to −0.50 → delta 0.96; strongly positive baseline makes any sharp crash look severe |
| S_018 (junior) | 9 | Strongly positive Days 1–4, crash Day 5, recovery Day 6, crash again Day 7 | Pattern break fires twice (Days 5 and 7); non-consecutive drops from a high baseline (+0.58) both exceed the 0.4 delta threshold |

**Elevated students**

| Student | Age | Pattern | Why elevated (not urgent) |
|---|---|---|---|
| S_007 (junior) | 7 | Neutral-to-mild Days 1–5 (🙂😐😬😑😴), sad Days 6–7 (😔😴) | Consecutive low days = 2; mild baseline (−0.09) means drop to −0.40 is only delta 0.31 — just below the pattern break threshold |
| S_012 (senior) | 16 | Neutral Days 1–5, clearly sad Days 6–7 | Consecutive low days = 2; neutral baseline keeps delta from Day 6 to 0.36 — just below the 0.4 pattern break threshold |

**Key threshold: why some students go urgent and others stay elevated**

The algorithm uses two urgency triggers: (1) `consecutive_low_days ≥ 3` (crisis watch) and (2) `pattern_break` — today's valence dropped more than 0.40 below the prior rolling baseline. S_007 and S_012 stay elevated because their baselines are already low or neutral, so the delta on their worst days stays just below 0.4. S_009 and S_018 go urgent because their strongly positive baselines make any sharp dip look severe. S_003 goes urgent because Gemini reads the gradually worsening language more sensitively than a keyword heuristic, scoring Days 3–7 below the −0.3 low-valence boundary.

---

## Deployment

A `Dockerfile` and `app.py` (FastAPI) are included so the pipeline can run on Cloud Run or any container platform.

```bash
# Local dev
pip install fastapi uvicorn
uvicorn app:app --reload

# Docker
docker build -t schoolpulse .
docker run -p 8080:8080 schoolpulse

# Cloud Run (one-shot deploy)
gcloud run deploy schoolpulse \
  --source . \
  --region us-central1 \
  --set-env-vars GOOGLE_API_KEY=$GOOGLE_API_KEY \
  --allow-unauthenticated
```

Endpoints: `GET /health`, `POST /run` (body: `{"date": "2026-06-28"}`). Add `?real_llm=1` to use live Gemini instead of `Fake*` stubs.

---

## Design decisions

See [DECISIONS.md](DECISIONS.md) for documented trade-offs, including:

- LLM seam strategy: fake stubs for integration tests, real Gemini for demo
- Why Google AI Studio was chosen over Vertex AI Agent Engine
- Arc-label vs. algorithm discrepancies found during integration

---

## Acknowledgements

Built as a capstone for the [Kaggle AI Agents Intensive 2026](https://www.kaggle.com/competitions/vibecoding-agents-capstone-project) course. <br/>
Course whitepapers © Google, licensed CC-BY 4.0.<br/>
Project planning and architecture via Claude.ai (Anthropic).<br/>
Implementation scaffolding via Claude Code (Anthropic).

## License

[CC-BY 4.0](LICENSE)
