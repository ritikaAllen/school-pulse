---
name: pii-context-sanitizer
description: |
  Sanitizes raw student check-in records before any LLM context window sees them.
  Implements three context hygiene techniques: (1) PII masking â€” replace student
  names with anonymised IDs, redact contact info and named entities from free text;
  (2) context windowing â€” enforce a 7-day max history, drop oldest first;
  (3) memory boundary enforcement â€” block cross-student record access and counselor
  name leakage. Use as the mandatory first step in the pipeline on every inbound
  record, before Signal Detector or Memory Keeper touch it.
  Do NOT use for signal parsing, trend computation, or any downstream analysis.
version: 1.0.0
license: CC-BY 4.0
allowed-tools: Read
metadata:
  author: ritika
  project: mental-health-first-responder-agent
  course-concept: Context Hygiene / Sensitive Data Masking (Day 5)
---

# pii-context-sanitizer

The mandatory privacy gate for every record entering the pipeline. No data
reaches the Signal Detector, Memory Keeper, or any LLM context window without
passing through this skill first. Its job is not analysis â€” it is boundary
enforcement. It transforms raw records into sanitised records; it makes no
judgements about emotional content.

This skill enforces three distinct context hygiene techniques as defined in the
Day 5 whitepaper: PII masking, context windowing, and memory boundary enforcement.

---

## When to use

- Any raw check-in record arrives from the MCP ingestion layer (Google Sheets)
- Any teacher observation note is received before being passed to Signal Detector
- Any record is about to enter an LLM context window for the first time
- The Orchestrator is constructing a context payload for any downstream sub-agent

## When NOT to use

- Record has already been sanitised in the current pipeline run â€” do not double-sanitise
- Task is signal parsing â€” use `emotional-signal-reader` after sanitisation
- Task is trend computation â€” use `student-trend-tracker` after sanitisation
- Task is to evaluate output quality â€” that belongs to the LLM-as-judge layer

> **Pipeline position:** This skill runs first, always. The Orchestrator enforces
> this ordering at the pipeline level. Downstream skills (`emotional-signal-reader`,
> `student-trend-tracker`) assume input is already sanitised and cannot verify
> provenance themselves. The sanitisation guarantee lives here and in the
> Orchestrator's routing logic â€” not in downstream skill guards.

---

## Workflow

This skill applies three independent hygiene passes in sequence. Each pass is
scoped to a different type of privacy risk. They do not interact â€” run all three
on every record.

---

### Pass 1 â€” PII Masking

**Purpose:** Prevent real student names and contact identifiers from entering
any LLM context window.

#### 1a. Student name â†’ anonymised ID

```yaml
rule:
  field: student_name
  action: replace_with_id
  mapping_source: student_id_registry  # maintained by Orchestrator, not this skill
  example:
    input:  student_name = "Maya Chen"
    output: student_id = "S_042"
            student_name field: REMOVED from record
```

The `student_name` field is physically removed from the output record. Only
`student_id` remains. This skill does not own the nameâ†’ID mapping â€” it receives
the registry from the Orchestrator as part of its input context.

#### 1b. Teacher observation notes â€” named entity redaction

```yaml
rule:
  field: teacher_observation.note
  action: NER_pass_replace_names
  replacement_token: "[PERSON]"
  scope: all PERSON entities detected
  example:
    input:  "Maya seems withdrawn. Her friend Priya mentioned she hasn't been eating."
    output: "[PERSON] seems withdrawn. Her friend [PERSON] mentioned she hasn't been eating."
```

Apply NER only to the `note` text field. Do not alter `flag_level` or any
structured fields. Replace every detected PERSON entity â€” including student
names, peer names, family member names. Do not attempt to identify *which*
person the name refers to.

#### 1c. Senior free-text responses â€” contact identifier redaction

```yaml
rule:
  field: senior_input.response
  action: redact_identifiers
  patterns:
    - email_addresses:   regex â†’ [REDACTED_EMAIL]
    - phone_numbers:     regex â†’ [REDACTED_PHONE]
    - physical_addresses: NER â†’ [REDACTED_ADDRESS]
    - social_handles:    regex â†’ [REDACTED_HANDLE]
  example:
    input:  "I can be reached at maya@school.edu if anyone cares"
    output: "I can be reached at [REDACTED_EMAIL] if anyone cares"
```

Do NOT redact emotional content, distress keywords, or any non-identifying text.
The purpose is to remove contact identifiers, not to sanitise emotional signal.
Over-redaction that removes distress-relevant language is a failure mode.

---

### Pass 2 â€” Context Windowing

**Purpose:** Prevent stale history from inflating the LLM context window beyond
the 7-day rolling boundary.

```yaml
context_window_rules:
  max_history_days: 7
  drop_policy: oldest_first
  scope: signal_history in student_memory records
```

When a `student_memory` record is included in a context payload:

1. Count entries in `signal_history`
2. If count > 7: drop oldest entries until exactly 7 remain
3. Return the trimmed record; do not modify the memory store itself â€”
   the trimmed view is for context construction only; `student-trend-tracker`
   owns the authoritative memory write

**Note:** This pass applies when the Orchestrator is building context payloads
for sub-agents. It does not apply to individual check-in records (which have no
history attached).

---

### Pass 3 â€” Memory Boundary Enforcement

**Purpose:** Prevent cross-student data leakage and counselor identity exposure
in any context payload.

```yaml
memory_boundary_rules:
  cross_student_access: denied
  counselor_name_in_context: denied
```

#### 3a. Cross-student access block

Any context payload may contain records for exactly **one** `student_id`.
If a payload being constructed contains records for more than one `student_id`:

- **Reject the payload** â€” do not sanitise and pass through
- Return a `boundary_violation` error to the Orchestrator
- Log: `BOUNDARY_VIOLATION: cross_student_payload detected, payload rejected`

This rule exists because even anonymised signals from multiple students
in one context window create a de-anonymisation risk through combination.

#### 3b. Counselor name block

Scan the entire context payload for counselor names (provided by Orchestrator
as a blocked name list). If any counselor name appears:

- Redact with `[COUNSELOR]`
- Log the redaction

Counselor names must not appear in any LLM context window â€” their identity
is irrelevant to signal analysis and creates unnecessary data exposure.

---

### Output â€” sanitised record

Return the sanitised record with a `sanitisation_manifest` attached:

```yaml
sanitised_record:
  student_id: string              # anonymised ID only; student_name field removed
  date: ISO8601
  age_group: enum [junior, senior]
  junior_input:                   # unchanged (emoji sequences contain no PII)
    emoji_sequence: string
  senior_input:
    prompt: string                # unchanged
    response: string              # contact identifiers redacted
  teacher_observation:
    flag_level: enum              # unchanged
    note: string                  # PERSON entities replaced with [PERSON]
  sanitisation_manifest:
    pii_masking_applied: boolean
    entities_redacted: int        # count of [PERSON] replacements made
    identifiers_redacted: list    # types redacted e.g. ["email"]
    context_window_trimmed: boolean
    boundary_checks_passed: boolean
    sanitised_at: ISO8601
```

**Hard rules:**
- Never pass a record downstream without a `sanitisation_manifest` attached
- A manifest with `boundary_checks_passed: false` must halt the pipeline â€”
  the Orchestrator must not route a boundary-violated record to any sub-agent
- `student_name` must not appear anywhere in the output record or manifest
- If NER or regex passes fail technically, return a `sanitisation_error` â€”
  never silently pass an unsanitised record as if it were clean

---

## Output format

```json
{
  "student_id": "S_042",
  "date": "2026-06-28",
  "age_group": "senior",
  "junior_input": null,
  "senior_input": {
    "prompt": "How are you feeling today and why?",
    "response": "I can be reached at [REDACTED_EMAIL] if anyone cares"
  },
  "teacher_observation": {
    "flag_level": "concern",
    "note": "[PERSON] seems withdrawn. Her friend [PERSON] mentioned she hasn't been eating."
  },
  "sanitisation_manifest": {
    "pii_masking_applied": true,
    "entities_redacted": 2,
    "identifiers_redacted": ["email"],
    "context_window_trimmed": false,
    "boundary_checks_passed": true,
    "sanitised_at": "2026-06-28T08:00:00Z"
  }
}
```

---

## Anti-patterns to avoid

- Do NOT redact emotional content or distress language â€” over-sanitisation
  that removes signal destroys the downstream skill's ability to detect distress
- Do NOT pass a record downstream if `boundary_checks_passed = false` â€”
  a boundary violation must halt, not warn-and-continue
- Do NOT attempt to infer which PERSON entity is the subject student â€”
  replace all PERSON entities uniformly
- Do NOT modify the `student_memory` store during context windowing â€”
  Pass 2 produces a trimmed *view* for the context payload; the authoritative
  memory write belongs to `student-trend-tracker`
- Do NOT cache or store the nameâ†’ID mapping â€” this skill receives it as
  input context from the Orchestrator each time; it does not own identity data

---

## EDD Eval Cases

```json
[
  {
    "case_id": "pcs_teacher_note_redaction_001",
    "description": "Teacher note containing two student names â€” both must be replaced",
    "input": {
      "student_id": "S_042",
      "teacher_observation": {
        "flag_level": "concern",
        "note": "Maya seems very withdrawn. Her friend Priya mentioned she hasn't been eating."
      }
    },
    "expected_output": {
      "teacher_observation.note": "[PERSON] seems very withdrawn. Her friend [PERSON] mentioned she hasn't been eating.",
      "sanitisation_manifest.entities_redacted": 2,
      "sanitisation_manifest.pii_masking_applied": true,
      "student_name_in_output": false
    },
    "rubric": ["both PERSON entities replaced", "note emotional content preserved", "manifest entity count correct"]
  },
  {
    "case_id": "pcs_email_redaction_002",
    "description": "Senior response containing email address â€” redacted, emotional content preserved",
    "input": {
      "student_id": "S_004",
      "senior_input": {
        "prompt": "How are you feeling today and why?",
        "response": "I can be reached at maya@school.edu if anyone cares"
      }
    },
    "expected_output": {
      "senior_input.response": "I can be reached at [REDACTED_EMAIL] if anyone cares",
      "sanitisation_manifest.identifiers_redacted": ["email"],
      "sanitisation_manifest.boundary_checks_passed": true
    },
    "rubric": ["email redacted", "surrounding text preserved", "manifest correct"]
  },
  {
    "case_id": "pcs_clean_note_passthrough_003",
    "description": "Teacher note with no PII â€” passes through unchanged",
    "input": {
      "student_id": "S_011",
      "teacher_observation": {
        "flag_level": "none",
        "note": "Student showed good engagement in group activity today."
      }
    },
    "expected_output": {
      "teacher_observation.note": "Student showed good engagement in group activity today.",
      "sanitisation_manifest.entities_redacted": 0,
      "sanitisation_manifest.pii_masking_applied": false,
      "sanitisation_manifest.boundary_checks_passed": true
    },
    "rubric": ["note unchanged", "no false redaction", "manifest entities_redacted = 0"]
  }
]
```

---

## See also

- `references/pii_redaction_patterns.md` â€” regex patterns for contact identifier detection
- `../emotional-signal-reader/SKILL.md` â€” runs immediately after this skill
- `../student-trend-tracker/SKILL.md` â€” runs after emotional-signal-reader
- `specs/privacy-guard.md` â€” sub-agent spec this skill belongs to
