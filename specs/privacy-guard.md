---
component: privacy-guard
type: sub-agent
skill: pii-context-sanitizer
orchestrator: mental-health-first-responder-orchestrator
upstream: mcp-layer            # receives raw records from MCP ingestion
downstream: signal-detector    # emits sanitised records to Signal Detector
---

# Privacy Guard Sub-Agent

Mandatory first-pass gate for every record entering the pipeline.
No data reaches Signal Detector, Memory Keeper, or any LLM context
window without passing through Privacy Guard first. Implements three
context hygiene techniques: PII masking, context windowing,
and memory boundary enforcement.

---

## Role

Enforce privacy at the pipeline boundary. Transform raw inbound records
into sanitised records with a `sanitisation_manifest` attached. Block
any record that fails boundary checks from proceeding downstream.

---

## Pipeline position

```
MCP Layer
    │
    ▼
Privacy Guard  ◄── MANDATORY FIRST — no bypass permitted
    │
    ▼
Signal Detector
    │
    ▼
Memory Keeper
    │
    ▼
Orchestrator
```

The Orchestrator enforces this ordering. Signal Detector rejects any
record without a valid `sanitisation_manifest`.

---

## Input schema

```yaml
raw_check_in:
  student_name: string         # real name from source data — MUST be removed
  student_id: string           # anonymised ID from student_registry
  age_group: enum [junior, senior]
  date: ISO8601
  junior_input:
    emoji_sequence: string
  senior_input:
    prompt: string
    response: string           # may contain PII
  teacher_observation:
    note: string               # may contain names
    flag_level: enum [none, watch, concern]
```

---

## Output schema

```yaml
sanitised_record:
  student_id: string           # anonymised ID only; student_name REMOVED
  age_group: enum [junior, senior]
  date: ISO8601
  junior_input:
    emoji_sequence: string     # unchanged
  senior_input:
    prompt: string             # unchanged
    response: string           # contact identifiers redacted
  teacher_observation:
    note: string               # PERSON entities replaced with [PERSON]
    flag_level: enum           # unchanged
  sanitisation_manifest:
    pii_masking_applied: boolean
    entities_redacted: int
    identifiers_redacted: list[string]
    context_window_trimmed: boolean
    boundary_checks_passed: boolean
    sanitised_at: ISO8601
```

---

## Sanitisation rules

```yaml
pii_rules:
  - field: student_name
    action: replace_with_id
    note: field physically removed from output; student_id carries forward

  - field: teacher_observation.note
    action: NER_pass_replace_PERSON_entities
    replacement: "[PERSON]"

  - field: senior_input.response
    action: redact_contact_identifiers
    patterns: [email, phone, address, social_handle, url]
    replacements:
      email:   "[REDACTED_EMAIL]"
      phone:   "[REDACTED_PHONE]"
      address: "[REDACTED_ADDRESS]"
      handle:  "[REDACTED_HANDLE]"
      url:     "[REDACTED_URL]"

context_window_rules:
  max_history_days: 7
  drop_policy: oldest_first
  scope: student_memory context payloads only
  note: this pass produces a trimmed view; does not write to memory store

memory_boundary_rules:
  cross_student_access: denied
  counselor_name_in_context: denied
  violation_action: reject payload, return boundary_violation error
```

---

## Hard rules

- A record with `boundary_checks_passed = false` must never proceed downstream
- `student_name` must not appear anywhere in the output record or manifest
- If NER or regex passes fail technically, return `sanitisation_error` —
  never silently pass an unsanitised record as clean
- Do not redact emotional content or distress language — over-sanitisation
  that removes signal is a failure mode equal in severity to under-sanitisation

---

## Skill invocation

Delegates all sanitisation logic to `pii-context-sanitizer` skill.
Privacy Guard handles pipeline routing, error propagation, and the
blocking gate. It does not implement sanitisation logic itself.

---

## Error handling

```yaml
errors:
  boundary_violation:
    condition: cross-student payload or counselor name detected
    action: reject entire payload, return error to Orchestrator, do not sanitise

  sanitisation_failure:
    condition: NER or regex pass fails technically
    action: return sanitisation_error; mark boundary_checks_passed = false;
            do not pass record downstream

  missing_registry:
    condition: student_id_registry not provided by Orchestrator
    action: halt; cannot perform name→ID replacement without registry
```

---

## BDD scenarios

See `SPEC.md` §9 — Scenario 4 is the canonical Privacy Guard scenario
(teacher note with student name sanitised before LLM context).

---

## See also

- `.agent/skills/pii_context_sanitizer/SKILL.md`
- `.agent/skills/pii_context_sanitizer/references/pii_redaction_patterns.md`
- `specs/mcp-layer.md` — upstream data source
- `specs/signal-detector.md` — downstream consumer of sanitised records
- `specs/orchestrator.md` — owns pipeline ordering enforcement
