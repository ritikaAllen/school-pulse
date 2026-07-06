# SPEC.md 
## SchoolPulse — Multi-Agent Student Well-Being Signal System
> **Project:** Kaggle AI Agents Intensive · Agents for Good Capstone  
> **Track:** Agents for Good  
> **Competition Deadline:** July 6, 2026  
> **Format:** Hybrid Markdown + YAML (per SkCC best practice, Day 5)  
> **Status:** All Phases Complete

---

## 1. Background & Purpose (The "Why")

School counselors are chronically under-resourced. In a typical school, one counselor may be responsible for 300–500 students. Early warning signs of mental health distress — withdrawal, mood shifts, declining engagement — are frequently missed because there is no systematic way to aggregate daily signals across an entire student body.

This system is a **multi-agent AI pipeline** that gives school counselors a daily synthesized brief: which students may need a check-in, what their recent emotional trend looks like, and what the counselor should do next. It operates on **synthetic daily check-in data** (20 students, 7 days, two age groups) to demonstrate the architecture.

**The system never replaces a counselor's judgment.** It is a signal amplifier — surfacing patterns that a single human could not track manually, then requiring human sign-off before any action is taken.

---

## 2. Agents for Good Alignment

| Dimension | How This Project Addresses It |
|---|---|
| Real social benefit | Early identification of students in distress; reduces missed signals in under-resourced schools |
| Responsible AI | Human-in-the-loop required for all counselor referrals; PII stripped before any LLM context |
| Multi-agent demonstration | Three cooperating sub-agents with distinct roles and trust boundaries |
| Course concept coverage | Six concepts from Days 1–5, each wired to a specific system component |

---

## 3. System Architecture Overview

```
                    ┌──────────────┐
                    │  MCP LAYER   │
                    │  Google      │
                    │  Sheets:     │
                    │  check-ins + │
                    │  teacher obs │
                    └──────┬───────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR AGENT                          │
│              (coordinates pipeline, owns HITL gate)             │
└───────────────────────┬─────────────────────────────────────────┘
                        │
          ┌─────────────┼──────────────────┐
          ▼             ▼                  ▼
  ┌───────────────┐ ┌──────────────┐ ┌──────────────────┐
  │ PRIVACY       │ │ SIGNAL       │ │ MEMORY           │
  │ GUARD         │ │ DETECTOR     │ │ KEEPER           │
  │ Sub-Agent     │ │ Sub-Agent    │ │ Sub-Agent        │
  └───────┬───────┘ └──────┬───────┘ └────────┬─────────┘
          │                │                   │
   skill:          skill:              skill:
   pii-context-    emotional-          student-trend-
   sanitizer       signal-reader       tracker
          │                │                   │
          └────────────────┼───────────────────┘
                           │
                    ┌──────▼───────┐
                    │ LLM-AS-JUDGE │
                    │ EVAL LAYER   │
                    └──────────────┘
                           │
                    ┌──────▼───────┐
                    │  COUNSELOR   │
                    │  DAILY BRIEF │
                    │  (HITL gate) │
                    └──────────────┘
```

---

## 4. Sub-Agent Specifications

### 4.1 Signal Detector Sub-Agent

**Role:** Reads raw daily check-in data and converts it into structured emotional signals. Handles two input modes: emoji-based (younger students, ages 6–10) and prompted text responses (older students, ages 11–17).

**Skill loaded:** `emotional-signal-reader`

**Input schema:**
```yaml
check_in:
  student_id: string          # anonymized, e.g. "S_042"
  age_group: enum [junior, senior]
  date: ISO8601
  junior_input:               # only present if age_group == junior
    emoji_sequence: string    # e.g. "😔😴😠"
  senior_input:               # only present if age_group == senior
    prompt: string            # "How are you feeling today and why?"
    response: string
  teacher_observation:        # injected via MCP from Google Sheets
    note: string
    flag_level: enum [none, watch, concern]
```

**Output schema:**
```yaml
signal:
  student_id: string
  date: ISO8601
  emotional_valence: float    # -1.0 (very negative) to +1.0 (very positive)
  energy_level: float         # 0.0 (exhausted) to 1.0 (high energy)
  social_withdrawal_flag: boolean
  distress_keywords_detected: list[string]
  signal_confidence: float    # 0.0 to 1.0
  raw_input_type: enum [emoji, text, teacher_note, merged]  # merged when multiple modalities present
```

---

### 4.2 Memory Keeper Sub-Agent

**Role:** Maintains a 7-day rolling window of per-student emotional signals. Computes trend vectors and detects pattern breaks (sudden drops, persistent low signals). Owns long-term student memory.

**Skill loaded:** `student-trend-tracker`

**Memory schema (per student, stored in long-term memory):**
```yaml
student_memory:
  student_id: string
  age_group: enum [junior, senior]
  signal_history:             # list of last 7 days; oldest dropped on day 8
    - date: ISO8601
      emotional_valence: float
      energy_level: float
      social_withdrawal_flag: boolean
  rolling_baseline:
    avg_valence: float
    avg_energy: float
  trend_direction: enum [stable, improving, declining, crisis_watch]
  consecutive_low_days: int   # days below threshold (-0.3 valence)
  last_counselor_referral: ISO8601 | null
```

**Output schema:**
```yaml
trend_report:
  student_id: string
  trend_direction: enum [stable, improving, declining, crisis_watch]
  delta_from_baseline: float
  consecutive_low_days: int
  pattern_break_detected: boolean
  recommended_priority: enum [routine, elevated, urgent]
```

---

### 4.3 Privacy Guard Sub-Agent

**Role:** Sanitizes all data before it enters any LLM context window. Implements three context hygiene techniques: (1) PII masking, (2) context windowing, (3) memory boundary enforcement. Acts as a mandatory pass-through gate — no data reaches the Signal Detector or Memory Keeper without going through Privacy Guard first.

**Skill loaded:** `pii-context-sanitizer`

**Sanitization rules:**
```yaml
pii_rules:
  - field: student_name
    action: replace_with_id      # "Maya Chen" → "S_042"
  - field: teacher_observation.note
    action: redact_name_entities # NER pass, replace names with [PERSON]
  - field: senior_input.response
    action: redact_identifiers   # phone numbers, emails, addresses
context_window_rules:
  max_history_days: 7
  drop_policy: oldest_first
memory_boundary_rules:
  cross_student_access: denied   # agent cannot request another student's record
  counselor_name_in_context: denied
```

---

## 5. Agent Skills Specifications

### 5.1 `emotional-signal-reader`

**Trigger:** Signal Detector receives a raw check-in record  
**Purpose:** Parse emoji sequences or text responses into structured `signal` output  
**Key behaviors:**
- For `junior` inputs: map emoji sequences to valence/energy using a fixed emoji-to-affect lookup table (embedded in skill body)
- For `senior` inputs: use LLM reasoning to extract distress keywords, valence, and social withdrawal signals from free text
- For `teacher_observation` inputs: parse flag_level + note text into signal fields
- Return `signal_confidence` score based on input completeness

**Eval cases (EDD — written before skill body):**
```
POSITIVE CASE 1:
  Input: emoji_sequence = "😔😴😠", age_group = junior
  Expected: valence < -0.4, energy < 0.3, distress_keywords = []

POSITIVE CASE 2:
  Input: text = "I haven't talked to anyone in three days and I don't want to go to class"
  Expected: valence < -0.5, social_withdrawal_flag = true, distress_keywords includes "isolated" or equivalent

NEGATIVE CASE:
  Input: emoji_sequence = "😊😄🌟", age_group = junior
  Expected: valence > 0.5, social_withdrawal_flag = false, recommended_priority = routine
```

---

### 5.2 `student-trend-tracker`

**Trigger:** Memory Keeper receives a new `signal` record to integrate  
**Purpose:** Update rolling memory, compute trend, detect pattern breaks  
**Key behaviors:**
- Integrate new signal into 7-day rolling window
- Recompute rolling baseline (avg valence, avg energy)
- Detect pattern breaks: valence drop > 0.4 from baseline in single day
- Increment `consecutive_low_days` if valence < -0.3
- Escalate `recommended_priority` to `urgent` if `consecutive_low_days >= 3`

**Eval cases:**
```
POSITIVE CASE 1:
  Input: 6 days of valence [-0.1, -0.2, -0.1, -0.15, -0.1, -0.2], new signal valence = -0.8
  Expected: pattern_break_detected = true, recommended_priority = elevated or urgent

POSITIVE CASE 2:
  Input: 3 consecutive days of valence < -0.3
  Expected: consecutive_low_days = 3, recommended_priority = urgent

NEGATIVE CASE:
  Input: 7 days of valence [0.6, 0.5, 0.7, 0.6, 0.8, 0.5, 0.6], new signal valence = 0.65
  Expected: trend_direction = stable, recommended_priority = routine
```

---

### 5.3 `pii-context-sanitizer`

**Trigger:** Any raw data record entering the pipeline (before Signal Detector or Memory Keeper touch it)  
**Purpose:** Strip or mask all PII before it reaches an LLM context window  
**Key behaviors:**
- Apply NER to teacher observation notes; replace detected names with `[PERSON]`
- Replace student names with their anonymized IDs
- Redact contact information patterns from free-text responses
- Enforce context window to last 7 days only (drop older records)
- Block cross-student memory queries

**Eval cases:**
```
POSITIVE CASE 1:
  Input: teacher_note = "Maya seems very withdrawn. Her friend Priya mentioned she hasn't been eating."
  Expected: output = "[PERSON] seems very withdrawn. Her friend [PERSON] mentioned she hasn't been eating."

POSITIVE CASE 2:
  Input: student_response = "I can be reached at maya@school.edu if anyone cares"
  Expected: output = "I can be reached at [REDACTED] if anyone cares"

NEGATIVE CASE:
  Input: teacher_note = "Student showed good engagement in group activity today."
  Expected: note passes through unchanged (no PII detected)
```

---

## 6. Course Concept → Component Mapping

| Course Concept | Component in This System |
|---|---|
| **MCP (Model Context Protocol)** | MCP layer architected for Google Sheets ingestion (check-ins + teacher observations); demo uses local JSON fixtures from `data/synthetic/` — no live Sheets connection required |
| **Multi-Agent Systems** | Three cooperating sub-agents (Signal Detector, Memory Keeper, Privacy Guard) coordinated by Orchestrator |
| **Long-Term Memory** | Memory Keeper maintains 7-day rolling per-student memory with trend vectors and baseline tracking |
| **Context Hygiene / ContextResolver** | Privacy Guard enforces three techniques: PII masking, context windowing (7-day max), memory boundary enforcement |
| **LLM-as-Judge Evaluation** | Synthesis layer evaluates Orchestrator's daily brief against rubric: signal coverage, escalation accuracy, PII-free output, counselor action clarity |
| **Human-in-the-Loop (HITL)** | Orchestrator halts and presents Daily Brief to counselor; no referral actions taken without explicit counselor sign-off |

---

## 7. Data Schema — Synthetic Demo Dataset

**20 synthetic students · 7 days · 2 age groups**

```yaml
dataset:
  students_total: 20
  age_groups:
    junior:
      count: 10
      ages: 6-10
      check_in_type: emoji_sequence
    senior:
      count: 10
      ages: 11-17
      check_in_type: prompted_text
  days: 7
  distributions:
    routine_students: 13        # stable or improving signals (algorithm Day 7 output)
    elevated_watch: 2           # design-time arc labels: 4; algorithm produces 2 elevated by Day 7
                                # (S_007, S_012 — baselines low enough that delta stays below 0.4)
    urgent_watch: 5             # design-time arc labels: 2; algorithm produces 5 urgent by Day 7
                                # (S_009, S_018 arced "elevated" but escalate via pattern_break;
                                # S_003 arced "elevated_declining" but escalates via 5 consecutive
                                # low days with real LLM — see DECISIONS.md arc-label discrepancy)
  teacher_observations:
    frequency: "2–3 per day across cohort"
    flag_levels: [none, watch, concern]
```

**File layout:**
```
data/
  synthetic_checkins.json        # raw check-in records, all 20 students × 7 days
  teacher_observations.json      # teacher notes with flag_level
  student_registry.json          # id ↔ age_group mapping (NO real names)
```

---

## 8. Tooling & Library Stack

```yaml
runtime:
  platform: Google AI Studio (Gemini API)
  auth: GOOGLE_API_KEY
  language: Python 3.11
  agent_framework: custom Python orchestrator + Google ADK 2.x Workflow graph
                   # Core pipeline: SchoolPulseOrchestrator (agents/orchestrator.py)
                   # ADK integration: agents/adk_workflow.py wraps the pipeline as a
                   # Workflow graph (FunctionNode for deterministic phases, LlmAgent
                   # for HITL gate) — used as the production entry point in app.py.

llm:
  model: gemini-3.1-flash-lite          # default for all agents (migrated from gemini-2.0-flash, deprecated 2026-06-01)
  judge_model: gemini-3.1-flash-lite    # LLM-as-judge layer

mcp:
  server: Google Sheets MCP             # teacher observation ingestion (architected; not live in demo)
  client: local JSON fixtures           # data/synthetic/ used in place of live Sheets connection
                                        # MCP layer spec in specs/mcp-layer.md

memory:
  type: in-process dict (demo)     # upgradeable to Firestore for production
  scope: per-student, 7-day window

evaluation:
  framework: custom LLM-as-judge   # rubric-based, no external eval library required
  output_format: JSON score card

notebook:
  environment: Kaggle Notebook
  gpu: none required
```

---

## 9. BDD Scenarios — Full Pipeline

### Scenario 1: Junior student shows three consecutive low-mood days

```gherkin
Feature: Counselor early-warning brief

  Scenario: Junior student persistent low mood triggers urgent referral
    Given a junior student "S_017" with emoji check-ins
    And their signals for the past 3 days have valence < -0.3
    And today's emoji input is "😢😴😠"
    When the pipeline runs the morning check-in batch
    Then Privacy Guard strips the student's name and replaces it with "S_017"
    And Signal Detector parses the emoji sequence to valence = -0.7
    And Memory Keeper increments consecutive_low_days to 4
    And Memory Keeper sets recommended_priority = "urgent"
    And the Orchestrator includes S_017 in the Daily Brief under "Urgent — Counselor Action Required"
    And the HITL gate presents the brief to the counselor before any referral is logged
    And no referral is created until the counselor approves
```

### Scenario 2: Senior student pattern break detected

```gherkin
  Scenario: Senior student single-day drop from stable baseline triggers elevated alert
    Given a senior student "S_004" with a 6-day valence baseline of +0.5
    And today's text response contains "I feel like nobody would notice if I disappeared"
    When the pipeline runs the morning check-in batch
    Then Privacy Guard redacts any PII from the text response
    And Signal Detector sets social_withdrawal_flag = true and detects distress keyword
    And Memory Keeper detects delta_from_baseline > 0.8 and sets pattern_break_detected = true
    And recommended_priority is set to "urgent" regardless of consecutive_low_days count
    And the LLM-as-judge evaluates the Orchestrator's brief for escalation accuracy
    And the Daily Brief surfaces S_004 with the note "Sudden significant drop — pattern break"
```

### Scenario 3: Routine student — no false escalation

```gherkin
  Scenario: Stable student does not appear in counselor action list
    Given a junior student "S_011" with a 7-day valence history all above +0.4
    And today's emoji input is "😊😄"
    When the pipeline runs the morning check-in batch
    Then Signal Detector sets valence = +0.7 and social_withdrawal_flag = false
    And Memory Keeper sets trend_direction = "stable"
    And recommended_priority = "routine"
    And S_011 does NOT appear in the "Urgent" or "Elevated" sections of the Daily Brief
    And the LLM-as-judge eval confirms no false-positive escalation
```

### Scenario 4: PII guard blocks name leakage

```gherkin
  Scenario: Teacher note with student name is sanitized before LLM context
    Given a teacher observation note: "Maya Chen has been crying in the hallway"
    When Privacy Guard processes the note
    Then the output is "[PERSON] has been crying in the hallway"
    And the string "Maya" does not appear anywhere in the LLM context payload
    And the student's anonymized ID "S_042" is used in all downstream processing
```

---

## 10. LLM-as-Judge Evaluation Rubric

The judge evaluates each run of the Orchestrator's Daily Brief against five criteria:

```yaml
judge_rubric:
  criteria:
    - name: signal_coverage
      description: "Does the brief account for all students with recommended_priority >= elevated?"
      weight: 0.25
      scoring: [0, 1, 2]   # 0=missed, 1=partial, 2=complete

    - name: escalation_accuracy
      description: "Are urgent students correctly flagged as urgent (no under-escalation)?"
      weight: 0.30
      scoring: [0, 1, 2]

    - name: pii_free_output
      description: "Does the brief contain zero real student names or identifiable details?"
      weight: 0.20
      scoring: [0, 2]       # binary: pass/fail

    - name: counselor_action_clarity
      description: "Is the recommended next action for each flagged student specific and actionable?"
      weight: 0.15
      scoring: [0, 1, 2]

    - name: false_positive_rate
      description: "Are routine students absent from the urgent/elevated sections?"
      weight: 0.10
      scoring: [0, 1, 2]

  pass_threshold: 0.75      # weighted score must exceed 75% to pass
  output_format: json_scorecard
```

---

## 11. Human-in-the-Loop Gate Specification

The HITL gate is the final component before any output leaves the system.

**Trigger:** Orchestrator has assembled the Daily Brief  
**What the counselor sees:**
```
DAILY BRIEF — [Date]
Generated by: Mental Health First Responder Agent

URGENT ACTION REQUIRED (n students):
  • S_004 — Pattern break detected. Sudden drop from baseline +0.5 → -0.6. 
    Text response flagged for distress keywords. Recommend: direct check-in today.
  • S_017 — 4 consecutive low-mood days. Emoji trend: 😢😴. 
    Recommend: one-on-one conversation + parent notification consideration.

ELEVATED WATCH (n students):
  • S_007 — Trend: stable. Delta from baseline: 0.31. Monitor.

ROUTINE (13 students): No action required today.

[APPROVE AND LOG] [REQUEST MORE CONTEXT] [OVERRIDE — NO ACTION]
```

**Gate rule:** No referral record is written to any system until the counselor selects `[APPROVE AND LOG]`. The `[OVERRIDE]` option is logged with a timestamp for audit trail purposes.

---

## 12. Build Phases (Post-Spec)

```
Phase 1 ✓ SPEC.md
Phase 2 ✓ SKILL.md files (emotional-signal-reader, student-trend-tracker, pii-context-sanitizer)
Phase 3 ✓ Synthetic dataset generation (synthetic_checkins.json, teacher_observations.json)
Phase 4 ✓ Sub-agents in isolation (build + test each against their eval cases)
Phase 5 ✓ Orchestrator + LLM-as-judge + HITL gate; integration tests T1–T6 passing
          Note: MCP layer is architected; demo uses local JSON fixtures (data/synthetic/)
Phase 6 ✓ Full Gemini migration; google-genai SDK; GOOGLE_API_KEY auth
Phase 7 ✓ Kaggle notebook (schoolpulse_demo.ipynb); API call optimisation; competition writeup
```

---

*This spec is the source of truth. Code is disposable. This document is not.*
