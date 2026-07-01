---
component: memory-keeper
type: sub-agent
skill: student-trend-tracker
orchestrator: mental-health-first-responder-orchestrator
upstream: signal-detector      # receives signal objects from Signal Detector
downstream: orchestrator       # emits trend_reports to Orchestrator
---

# Memory Keeper Sub-Agent

Maintains per-student 7-day rolling memory. Integrates incoming signal
objects, recomputes trend direction and baseline, detects pattern breaks,
and emits trend reports with recommended priority for the Orchestrator's
Daily Brief assembly.

---

## Role

Own and operate student long-term memory. No other sub-agent or component
reads or writes `student_memory` records. The Orchestrator receives
`trend_report` output only — never raw memory.

---

## Memory schema (per student)

```yaml
student_memory:
  student_id: string
  age_group: enum [junior, senior]
  signal_history:                  # rolling window; max 7 entries
    - date: ISO8601
      emotional_valence: float
      energy_level: float
      social_withdrawal_flag: boolean
  rolling_baseline:
    avg_valence: float | null      # null until 3+ days of history
    avg_energy: float | null
  trend_direction: enum [stable, improving, declining, crisis_watch]
  consecutive_low_days: int
  last_counselor_referral: ISO8601 | null
```

---

## Input schema

```yaml
signal:
  student_id: string
  date: ISO8601
  emotional_valence: float
  energy_level: float
  social_withdrawal_flag: boolean
  distress_keywords_detected: list[string]
  signal_confidence: float
  raw_input_type: enum [emoji, text, teacher_note, merged]
```

---

## Output schema

```yaml
trend_report:
  student_id: string
  trend_direction: enum [stable, improving, declining, crisis_watch]
  delta_from_baseline: float       # 0.0 if baseline not yet established
  consecutive_low_days: int
  pattern_break_detected: boolean
  recommended_priority: enum [routine, elevated, urgent]
```

---

## Skill invocation

Delegates all computation to `student-trend-tracker` skill.
Memory Keeper handles memory load/write, error propagation, and
the read-only mode (trend report without new signal). It does not
implement trend logic itself.

---

## Operating modes

```yaml
modes:
  update_and_report:
    trigger: new signal object received for student_id
    action: integrate signal, recompute trend, write memory, emit trend_report

  read_only_report:
    trigger: Orchestrator requests trend_report with no new signal (e.g. re-query)
    action: emit trend_report from current memory state; do not modify memory

  initialise:
    trigger: signal received for student_id with no existing memory record
    action: create new student_memory record, integrate first signal, emit trend_report
```

---

## Memory boundary rules

```yaml
boundary_rules:
  cross_student_access: denied
    # Memory Keeper processes exactly one student_id per call.
    # Any request containing multiple student_ids is rejected.
  direct_orchestrator_read: denied
    # Orchestrator receives trend_report only; never accesses student_memory directly.
  skill_write_authority: student-trend-tracker only
    # No other skill or component may write to student_memory.
```

---

## Key thresholds

```yaml
thresholds:
  low_valence_boundary: -0.3          # below this increments consecutive_low_days
  pattern_break_delta: 0.4            # drop > 0.4 from baseline in one day
  crisis_watch_trigger: 3             # consecutive_low_days >= 3
  baseline_minimum_days: 3            # baseline not computed until 3+ history entries
  rolling_window_max: 7               # oldest entry dropped on day 8
```

---

## Error handling

```yaml
errors:
  cross_student_payload:
    condition: signal contains student_id not matching current memory scope
    action: reject, return boundary_violation error to Orchestrator

  memory_write_failure:
    condition: memory store write fails
    action: emit trend_report from pre-write state; log error; do not emit stale data

  skill_failure:
    condition: student-trend-tracker returns no output
    action: return trend_report with recommended_priority = routine and error flag
```

---

## BDD scenarios

See `SPEC.md` §9 — Scenarios 1 and 2 involve Memory Keeper as a core
participant (consecutive low days, pattern break detection).

---

## See also

- `.agent/skills/student_trend_tracker/SKILL.md`
- `specs/signal-detector.md` — upstream producer of signal objects
- `specs/privacy-guard.md` — sanitisation gate upstream of Signal Detector
- `specs/orchestrator.md` — coordinator and sole consumer of trend_report
