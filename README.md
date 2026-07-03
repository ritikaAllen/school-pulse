# SchoolPulse — Mental Health First Responder Agent

> Kaggle AI Agents Intensive · Agents for Good Capstone · July 2026

A multi-agent AI pipeline that gives school counselors a daily synthesized brief: which students may need a check-in, what their recent emotional trend looks like, and what the counselor should do next.

**The system never replaces a counselor's judgment.** It is a signal amplifier — surfacing patterns that a single human cannot track manually across 300–500 students, then requiring human sign-off before any action is taken.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  ORCHESTRATOR AGENT                  │
│          (coordinates pipeline, owns HITL gate)      │
└───────────────────────┬──────────────────────────────┘
                        │  per student, per day
          ┌─────────────┼─────────────────┐
          ▼             ▼                 ▼
  ┌──────────────┐ ┌───────────────┐ ┌──────────────┐
  │ PRIVACY      │ │ SIGNAL        │ │ MEMORY       │
  │ GUARD        │ │ DETECTOR      │ │ KEEPER       │
  │              │ │               │ │              │
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

### Pipeline flow (per student, per day)

1. **Privacy Guard** strips PII before any data enters an LLM context — student names replaced with IDs, contact info redacted, teacher notes NER-cleaned.
2. **Signal Detector** converts the sanitized check-in into a structured signal: `emotional_valence`, `energy_level`, `social_withdrawal_flag`, `distress_keywords_detected`. Junior students use an emoji-to-affect lookup table; senior students use a Gemini 2.0 Flash text parse.
3. **Memory Keeper** integrates the signal into a 7-day rolling window, recomputes the baseline, and sets `recommended_priority` (`routine` / `elevated` / `urgent`) based on pattern-break detection and consecutive low-day counts.
4. **Orchestrator** assembles a PII-free Daily Brief, runs an LLM-as-judge evaluation (5-criterion rubric, pass threshold 0.75), then presents the brief to the counselor via the HITL gate.
5. **HITL gate** halts the pipeline. No referral is written without `APPROVE_AND_LOG`. Every `OVERRIDE_NO_ACTION` is logged to the audit trail.

---

## Project layout

```
school-pulse/
├── agents/
│   ├── privacy_guard.py        # PII sanitization agent
│   ├── signal_detector.py      # Emotional signal extraction agent
│   ├── memory_keeper.py        # Rolling trend memory agent
│   ├── orchestrator.py         # Pipeline coordinator + HITL gate
│   └── llm_interface.py        # LLM seam abstraction (Fake* / Real*)
├── skills/
│   ├── emotional-signal-reader/
│   │   └── reader.py           # Emoji lookup + Gemini text parse
│   ├── student-trend-tracker/
│   │   └── tracker.py          # 7-day rolling window + priority rules
│   └── pii-context-sanitizer/
│       └── sanitizer.py        # NER + regex PII redaction
├── data/synthetic/
│   ├── student_registry.json   # 20 students, ID ↔ age_group mapping
│   ├── synthetic_checkins.json # 20 students × 7 days of check-ins
│   └── teacher_observations.json
├── tests/
│   ├── test_privacy_guard.py
│   ├── test_signal_detector.py
│   ├── test_memory_keeper.py
│   └── test_orchestrator.py    # Integration tests T1–T6
├── specs/                      # Per-agent specs (HITL, MCP, orchestrator…)
├── SPEC.md                     # Full system specification
├── DECISIONS.md                # Architecture decisions and trade-offs
└── requirements.txt
```

---

## Quickstart

**Prerequisites:** Python 3.11, spaCy `en_core_web_sm` model.

For the demo run you also need a **Google AI Studio API key** — get one free at [aistudio.google.com](https://aistudio.google.com) and export it as `GOOGLE_API_KEY`.

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### Run the integration tests (no API key required)

All 34 tests use deterministic `Fake*` stubs — no credentials needed.

```bash
pytest tests/ -v
# 33 passed, 1 skipped
```

The 1 skipped test (`test_esr_text_withdrawal_002`) exercises the live Gemini text path in `reader.py` directly. It runs when `GOOGLE_API_KEY` is set:

```bash
GOOGLE_API_KEY=<your-key> pytest tests/test_signal_detector.py -v
```

### Demo run (full pipeline with real LLM)

```python
from pathlib import Path
from agents.orchestrator import SchoolPulseOrchestrator, load_data, DEMO_DATES
from agents.llm_interface import RealSignalLLM, RealOrchestratorLLM

checkins, teacher_obs, registry = load_data(Path("data/synthetic"))

orchestrator = SchoolPulseOrchestrator(
    signal_llm=RealSignalLLM(),           # reads GOOGLE_API_KEY
    orchestrator_llm=RealOrchestratorLLM(),
    hitl_callback=None,                   # interactive CLI prompt
)
results = orchestrator.run_sequential_days(DEMO_DATES, checkins, teacher_obs, registry)
```

Set `GOOGLE_API_KEY` in your environment before running. The `Fake*` stubs are the default when no LLM is passed — useful for notebooks and offline development.

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
| Multi-Agent Systems | Three cooperating sub-agents coordinated by Orchestrator |
| MCP (Model Context Protocol) | Teacher observations ingested from Google Sheets via MCP connector |
| Long-Term Memory | Memory Keeper: 7-day rolling per-student window with baseline tracking |
| LLM-as-Judge Evaluation | 5-criterion rubric evaluated on every Daily Brief; pass threshold 0.75 |
| Context Hygiene | Privacy Guard: PII masking, 7-day context window, cross-student boundary enforcement |
| Human-in-the-Loop (HITL) | Orchestrator halts for counselor sign-off; no referral written without APPROVE_AND_LOG |

---

## Synthetic dataset

20 students · 7 days · 2 age groups (10 junior emoji / 10 senior text)

| Arc | Count | Description |
|---|---|---|
| `routine` | 14 | Stable or improving signals throughout |
| `elevated` | 4 | Declining trend or 1–2 consecutive low days |
| `urgent` | 2 | Pattern break or 3+ consecutive low days |

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
