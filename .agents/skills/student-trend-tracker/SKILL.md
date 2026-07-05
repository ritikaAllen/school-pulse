---
name: student-trend-tracker
description: |
  Integrates a new signal record into a student's 7-day rolling memory, recomputes
  trend direction and baseline, detects pattern breaks, and emits a trend_report
  with recommended_priority. Use when the Memory Keeper sub-agent receives a
  validated signal object from the Signal Detector.
  Do NOT use for parsing raw check-in input (use emotional-signal-reader), for
  PII sanitization (use pii-context-sanitizer), or for accessing another student's
  memory record (cross-student access is forbidden).
version: 1.0.0
license: CC-BY 4.0
allowed-tools: Read Write
metadata:
  author: ritika
  project: mental-health-first-responder-agent
  course-concept: Long-Term Memory (Day 3)
---

# student-trend-tracker

Updates a student's long-term rolling memory with today's signal, recomputes
their emotional trend and baseline, detects pattern breaks, and produces a
`trend_report` that tells the Orchestrator whether and how urgently a counselor
should be alerted.

This skill is the only component that reads and writes student memory. No other
skill or sub-agent may access or mutate `student_memory` records directly.

---

## When to use

- Memory Keeper receives a new `signal` object from Signal Detector for a known student
- Memory Keeper receives a first-ever signal for a new student (initialise memory)
- Orchestrator requests a trend report for a student without a new signal today
  (read-only mode â€” emit report from existing memory, no update)

## When NOT to use

- Input is a raw check-in record, not a structured `signal` â€” run `emotional-signal-reader` first
- Task is PII sanitization â€” use `pii-context-sanitizer`
- Request involves more than one student in a single call â€” process each student separately
- Request asks to compare two students' records â€” cross-student access is forbidden

> **Memory ownership:** This skill is the sole reader and writer of `student_memory`.
> The Orchestrator may request a `trend_report` output but must never read or write
> the memory store directly. Memory boundary enforcement is also reinforced by
> `pii-context-sanitizer` at the pipeline level.

---

## Workflow

### Step 1 â€” Load or initialise student memory

Retrieve the `student_memory` record for `student_id`.

**If record exists:** proceed to Step 2.

**If no record exists (first check-in ever):** initialise as:

```yaml
student_memory:
  student_id: <from signal>
  age_group: <from signal>
  signal_history: []           # empty; today's signal will be first entry
  rolling_baseline:
    avg_valence: null          # null until 3+ days of history exist
    avg_energy: null
  trend_direction: stable      # default for new students
  consecutive_low_days: 0
  last_counselor_referral: null
```

---

### Step 2 â€” Integrate new signal into rolling window

Append today's signal to `signal_history`:

```yaml
new_entry:
  date: <signal.date>
  emotional_valence: <signal.emotional_valence>
  energy_level: <signal.energy_level>
  social_withdrawal_flag: <signal.social_withdrawal_flag>
```

Apply the rolling window policy:
- Maximum history length: **7 days**
- If appending would exceed 7 entries: drop the oldest entry first (FIFO)
- Each entry is uniquely identified by `date` â€” if today's date already exists in
  history, **replace** it (handles re-runs or corrections); do not duplicate

---

### Step 3 â€” Recompute rolling baseline

Baseline is computed only when `signal_history` contains **3 or more entries**.
With fewer than 3 days, baseline fields remain `null` and pattern break detection
is skipped (insufficient history).

```
avg_valence = mean(signal_history[*].emotional_valence)
avg_energy  = mean(signal_history[*].energy_level)
```

Update `rolling_baseline.avg_valence` and `rolling_baseline.avg_energy`.

---

### Step 4 â€” Detect pattern break

**Precondition:** `rolling_baseline` is not null (3+ days of history).

A pattern break is detected when today's valence drops sharply from the student's
established baseline in a single day:

```
delta_from_baseline = rolling_baseline.avg_valence - signal.emotional_valence
pattern_break_detected = (delta_from_baseline > 0.4)
```

A drop of > 0.4 from baseline in one day constitutes a pattern break regardless
of the student's absolute valence level. A student who was at +0.5 and drops to
+0.05 is as flagged as one who drops from -0.1 to -0.6.

---

### Step 5 â€” Update consecutive low days

```
if signal.emotional_valence < -0.3:
    consecutive_low_days += 1
else:
    consecutive_low_days = 0    # reset on any non-low day
```

The threshold of -0.3 is the low-mood boundary. Any day at or above -0.3 resets
the counter â€” the system does not carry forward low-day counts across recovery days.

---

### Step 6 â€” Compute trend direction

Trend direction is derived from the last 3 days of `signal_history` (or all
available days if fewer than 3).

```yaml
trend_rules:
  crisis_watch:
    condition: consecutive_low_days >= 3 OR pattern_break_detected = true
    overrides: all other trend rules
  declining:
    condition: last 3 valences each lower than the one before (monotonic decline)
  improving:
    condition: last 3 valences each higher than the one before (monotonic increase)
  stable:
    condition: all other cases (fluctuating, plateau, insufficient history)
```

`crisis_watch` takes precedence â€” if the crisis condition is met, trend is
`crisis_watch` regardless of the valence trajectory.

---

### Step 7 â€” Set recommended priority

```yaml
priority_rules:
  urgent:
    - consecutive_low_days >= 3
    - pattern_break_detected = true AND signal.emotional_valence < -0.3
    - social_withdrawal_flag = true AND consecutive_low_days >= 2
  elevated:
    - consecutive_low_days == 2
    - pattern_break_detected = true AND signal.emotional_valence >= -0.3
    - trend_direction == declining AND delta_from_baseline > 0.2
  routine:
    - all other cases
```

Priority rules are evaluated top-to-bottom. First match wins.
`urgent` always overrides `elevated`; `elevated` always overrides `routine`.

---

### Step 8 â€” Write updated memory and emit trend report

**Write** the updated `student_memory` back to the memory store.

**Emit** the `trend_report` output:

```yaml
trend_report:
  student_id: string
  trend_direction: enum [stable, improving, declining, crisis_watch]
  delta_from_baseline: float      # 0.0 if baseline not yet established
  consecutive_low_days: int
  pattern_break_detected: boolean
  recommended_priority: enum [routine, elevated, urgent]
```

**Hard rules:**
- Never emit a `trend_report` containing the student's name â€” `student_id` only
- Never include raw signal text or teacher note content in the trend report
- `delta_from_baseline` must be 0.0 (not null) when baseline is not yet established,
  to keep the output schema consistent for the Orchestrator
- Do not write to memory if signal integration fails â€” emit error, leave memory intact

---

## Output format

```json
{
  "student_id": "S_017",
  "trend_direction": "crisis_watch",
  "delta_from_baseline": 0.55,
  "consecutive_low_days": 4,
  "pattern_break_detected": false,
  "recommended_priority": "urgent"
}
```

---

## Anti-patterns to avoid

- Do NOT read another student's memory record â€” each call is scoped to exactly one `student_id`
- Do NOT average today's signal into the baseline before appending it to history â€”
  compute baseline from history first, then append, to avoid circular dependency
- Do NOT reset `consecutive_low_days` when writing memory if today was a low day â€”
  only reset on a non-low day
- Do NOT set `pattern_break_detected = true` when baseline is null â€” insufficient
  history means no reliable baseline to break from
- Do NOT carry distress keywords or raw text into the trend report â€” the report
  is a structured numeric summary only

---

## EDD Eval Cases

```json
[
  {
    "case_id": "stt_pattern_break_001",
    "description": "Student with stable near-zero baseline receives single sharp negative drop",
    "input": {
      "student_id": "S_004",
      "existing_signal_history": [
        {"date": "2026-06-22", "emotional_valence": -0.1, "energy_level": 0.5, "social_withdrawal_flag": false},
        {"date": "2026-06-23", "emotional_valence": -0.2, "energy_level": 0.6, "social_withdrawal_flag": false},
        {"date": "2026-06-24", "emotional_valence": -0.1, "energy_level": 0.5, "social_withdrawal_flag": false},
        {"date": "2026-06-25", "emotional_valence": -0.15, "energy_level": 0.6, "social_withdrawal_flag": false},
        {"date": "2026-06-26", "emotional_valence": -0.1, "energy_level": 0.5, "social_withdrawal_flag": false},
        {"date": "2026-06-27", "emotional_valence": -0.2, "energy_level": 0.5, "social_withdrawal_flag": false}
      ],
      "new_signal": {
        "student_id": "S_004",
        "date": "2026-06-28",
        "emotional_valence": -0.8,
        "energy_level": 0.1,
        "social_withdrawal_flag": true
      }
    },
    "expected_output": {
      "pattern_break_detected": true,
      "recommended_priority": "urgent",
      "delta_from_baseline_gt": 0.4
    },
    "rubric": ["pattern break fires on >0.4 drop", "urgent priority set", "delta computed correctly"]
  },
  {
    "case_id": "stt_consecutive_low_002",
    "description": "Student accumulates 3 consecutive low-valence days",
    "input": {
      "student_id": "S_017",
      "existing_signal_history": [
        {"date": "2026-06-26", "emotional_valence": -0.5, "energy_level": 0.2, "social_withdrawal_flag": false},
        {"date": "2026-06-27", "emotional_valence": -0.4, "energy_level": 0.2, "social_withdrawal_flag": false}
      ],
      "new_signal": {
        "student_id": "S_017",
        "date": "2026-06-28",
        "emotional_valence": -0.6,
        "energy_level": 0.2,
        "social_withdrawal_flag": false
      }
    },
    "expected_output": {
      "consecutive_low_days": 3,
      "trend_direction": "crisis_watch",
      "recommended_priority": "urgent"
    },
    "rubric": ["consecutive_low_days increments to 3", "crisis_watch triggered", "urgent priority set"]
  },
  {
    "case_id": "stt_stable_no_escalation_003",
    "description": "Student with consistently positive history receives another positive signal â€” no escalation",
    "input": {
      "student_id": "S_011",
      "existing_signal_history": [
        {"date": "2026-06-22", "emotional_valence": 0.6, "energy_level": 0.7, "social_withdrawal_flag": false},
        {"date": "2026-06-23", "emotional_valence": 0.5, "energy_level": 0.6, "social_withdrawal_flag": false},
        {"date": "2026-06-24", "emotional_valence": 0.7, "energy_level": 0.8, "social_withdrawal_flag": false},
        {"date": "2026-06-25", "emotional_valence": 0.6, "energy_level": 0.7, "social_withdrawal_flag": false},
        {"date": "2026-06-26", "emotional_valence": 0.8, "energy_level": 0.8, "social_withdrawal_flag": false},
        {"date": "2026-06-27", "emotional_valence": 0.5, "energy_level": 0.6, "social_withdrawal_flag": false},
        {"date": "2026-06-28", "emotional_valence": 0.6, "energy_level": 0.7, "social_withdrawal_flag": false}
      ],
      "new_signal": {
        "student_id": "S_011",
        "date": "2026-06-28",
        "emotional_valence": 0.65,
        "energy_level": 0.7,
        "social_withdrawal_flag": false
      }
    },
    "expected_output": {
      "trend_direction": "stable",
      "consecutive_low_days": 0,
      "pattern_break_detected": false,
      "recommended_priority": "routine"
    },
    "rubric": ["no false escalation", "stable trend confirmed", "routine priority", "no pattern break"]
  }
]
```

---

## See also

- `references/priority_decision_tree.md` â€” visual decision tree for priority rules
- `../emotional-signal-reader/SKILL.md` â€” upstream skill; must run before this one
- `../pii-context-sanitizer/SKILL.md` â€” sanitization; must run before both skills
- `specs/memory-keeper.md` â€” sub-agent spec this skill belongs to
