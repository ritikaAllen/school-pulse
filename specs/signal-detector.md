---
component: signal-detector
type: sub-agent
skill: emotional-signal-reader
orchestrator: mental-health-first-responder-orchestrator
upstream: privacy-guard        # receives sanitised records from Privacy Guard
downstream: memory-keeper      # emits signal objects to Memory Keeper
---

# Signal Detector Sub-Agent

Reads sanitised daily check-in records and converts them into structured
`signal` objects. All input arrives pre-sanitised from Privacy Guard.
This sub-agent never touches raw MCP data directly.

---

## Role

Convert sanitised check-in records (emoji sequences, free-text responses,
teacher observation notes) into structured emotional signal objects for
Memory Keeper consumption.

---

## Input schema

```yaml
check_in:
  student_id: string          # anonymised ID, e.g. "S_042"
  age_group: enum [junior, senior]
  date: ISO8601
  junior_input:               # present only if age_group == junior
    emoji_sequence: string    # e.g. "😔😴😠"
  senior_input:               # present only if age_group == senior
    prompt: string
    response: string          # already PII-sanitised by Privacy Guard
  teacher_observation:        # injected via MCP, already NER-sanitised
    note: string
    flag_level: enum [none, watch, concern]
  sanitisation_manifest:      # must be present; boundary_checks_passed must be true
    boundary_checks_passed: boolean
```

**Precondition:** Signal Detector will reject any record where
`sanitisation_manifest` is absent or `boundary_checks_passed = false`.
Rejected records are returned to the Orchestrator with a
`pipeline_error: unsanitised_input` flag.

---

## Output schema

```yaml
signal:
  student_id: string
  date: ISO8601
  emotional_valence: float        # -1.0 to +1.0
  energy_level: float             # 0.0 to 1.0
  social_withdrawal_flag: boolean
  distress_keywords_detected: list[string]
  signal_confidence: float        # 0.0 to 1.0
  raw_input_type: enum [emoji, text, teacher_note, merged]
```

---

## Skill invocation

Delegates all parsing logic to `emotional-signal-reader` skill.
Signal Detector handles routing, precondition checking, and error
propagation. It does not implement parsing logic itself.

---

## Error handling

```yaml
errors:
  unsanitised_input:
    condition: sanitisation_manifest absent or boundary_checks_passed = false
    action: reject record, return pipeline_error to Orchestrator, do not invoke skill

  malformed_record:
    condition: required fields missing or schema mismatch
    action: return signal with signal_confidence = 0.0, log anomaly

  skill_failure:
    condition: emotional-signal-reader returns no output
    action: return signal with signal_confidence = 0.0, log failure
```

---

## BDD scenarios

See `SPEC.md` §9 — Scenarios 1, 2, 3, 4 all involve Signal Detector as
a core participant.

---

## See also

- `.agent/skills/emotional_signal_reader/SKILL.md`
- `specs/privacy-guard.md` — upstream; must run before this component
- `specs/memory-keeper.md` — downstream consumer of signal output
- `specs/orchestrator.md` — coordinates this sub-agent's invocation
