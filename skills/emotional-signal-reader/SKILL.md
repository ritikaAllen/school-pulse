---
name: emotional-signal-reader
description: |
  Parses raw student check-in records into structured emotional signal objects.
  Use when the Signal Detector sub-agent receives a check-in record with an
  emoji_sequence (age_group=junior), a free-text response (age_group=senior),
  or a teacher_observation note (any age group).
  Do NOT use for trend analysis, memory updates, or PII sanitization — those
  are handled by student-trend-tracker and pii-context-sanitizer respectively.
version: 1.0.0
allowed-tools: Read
metadata:
  author: ritika
  project: mental-health-first-responder-agent
  course-concept: Agent Skills (Day 3)
---

# emotional-signal-reader

Converts raw student check-in input into a structured `signal` object
with valence, energy, withdrawal flag, distress keywords, and confidence score.
Handles three distinct input modalities: emoji sequences, free-text responses,
and teacher observation notes.

---

## When to use

- Signal Detector receives a junior check-in with an `emoji_sequence` field
- Signal Detector receives a senior check-in with a `prompt` + `response` field
- Signal Detector receives a `teacher_observation` note with a `flag_level`
- Any record where `raw_input_type` is unknown and must be inferred from schema

## When NOT to use

- Task is to compute trend or baseline — use `student-trend-tracker` instead
- Task is to generate the counselor brief — that belongs to the Orchestrator
- Input contains no check-in data (e.g. configuration files, registry lookups)

> **Sanitization assumption:** This skill assumes all input has already been processed
> by `pii-context-sanitizer`. This skill cannot verify provenance — sanitization order
> is enforced by the Orchestrator pipeline, which routes records from Privacy Guard
> output only. Never route raw MCP records directly to this skill.

---

## Workflow

### Step 1 — Detect input modality

Inspect the incoming record. Determine `raw_input_type`:

| Condition | `raw_input_type` |
|---|---|
| `age_group == junior` AND `emoji_sequence` present | `emoji` |
| `age_group == senior` AND `response` present | `text` |
| `teacher_observation.note` present | `teacher_note` |
| Multiple present | Process ALL; merge into single signal (teacher note overrides on `flag_level`) |

---

### Step 2 — Parse by modality

**A. Emoji input (junior)**

Use the emoji-to-affect lookup table in `references/emoji_affect_table.md`.

Rules:
- Map each emoji to its `(valence_delta, energy_delta)` pair
- Average across the full sequence to get `emotional_valence` and `energy_level`
- Set `social_withdrawal_flag = true` if 😶 🙈 😶‍🌫️ or equivalent isolation emoji present
- `distress_keywords_detected = []` (emoji path does not produce keywords)
- `signal_confidence`: 1.0 if sequence ≥ 2 emojis; 0.6 if single emoji

**B. Free-text input (senior)**

Use LLM reasoning to extract:

- `emotional_valence`: score from -1.0 to +1.0 based on overall sentiment and affect
- `energy_level`: 0.0 (exhausted, passive, flat) to 1.0 (energised, engaged)
- `social_withdrawal_flag`: true if response contains isolation language —
  phrases like "nobody", "alone", "nobody would notice", "don't want to",
  "haven't talked", "disappear", or similar withdrawal markers
- `distress_keywords_detected`: list of specific words or phrases flagged
  (e.g. "nobody would notice if I disappeared", "I can't stop crying")
- `signal_confidence`: 0.9 if response ≥ 15 words; 0.6 if 5–14 words; 0.3 if < 5 words

**C. Teacher observation note**

- Map `flag_level` to valence adjustment:

```yaml
flag_level_map:
  none:    valence_adjustment: 0.0
  watch:   valence_adjustment: -0.2
  concern: valence_adjustment: -0.5
```

- Apply NER scan to `note` field to detect distress language (crying, withdrawn,
  not eating, aggressive, isolated)
- Add detected phrases to `distress_keywords_detected`
- `social_withdrawal_flag`: true if note contains withdrawal language
- `signal_confidence`: 0.7 (teacher notes are subjective; never set to 1.0)

---

### Step 3 — Merge signals (if multiple modalities present)

When more than one modality is present for the same student on the same date:

```yaml
merge_rules:
  emotional_valence:  weighted_average (emoji: 0.4, text: 0.5, teacher_note: 0.1)
  energy_level:       weighted_average (same weights)
  social_withdrawal_flag: true if ANY modality sets it true
  distress_keywords_detected: union of all lists (deduplicated)
  signal_confidence:  average of modality confidences
```

---

### Step 4 — Emit structured signal output

Return a complete `signal` object conforming to the output schema:

```yaml
signal:
  student_id: string          # carry through from input, unchanged
  date: ISO8601               # carry through from input
  emotional_valence: float    # -1.0 to +1.0
  energy_level: float         # 0.0 to 1.0
  social_withdrawal_flag: boolean
  distress_keywords_detected: list[string]   # empty list if none
  signal_confidence: float    # 0.0 to 1.0
  raw_input_type: enum [emoji, text, teacher_note, merged]
```

**Hard rules:**
- Never infer student name — `student_id` must come directly from the input record
- Never produce `signal_confidence > 0.95` — no single check-in warrants certainty
- If input is malformed or empty, return `signal_confidence = 0.0` and log the anomaly
- Do not escalate or set priority — that is the Memory Keeper's job

---

## Output format

```json
{
  "student_id": "S_042",
  "date": "2026-06-28",
  "emotional_valence": -0.65,
  "energy_level": 0.2,
  "social_withdrawal_flag": true,
  "distress_keywords_detected": ["nobody would notice", "don't want to go to class"],
  "signal_confidence": 0.9,
  "raw_input_type": "text"
}
```

---

## Anti-patterns to avoid

- Do NOT set `recommended_priority` — this field belongs to the trend report, not the signal
- Do NOT look up prior days' signal history — this skill processes within a single
  date's record only. Cross-day trend analysis belongs to `student-trend-tracker`.
  Step 3 (merge) only combines multiple modalities arriving together in the same
  record for the same date — it does not reach backwards into previous days.
- Do NOT pass raw student name or PII into any reasoning step — input must already be sanitized
- Do NOT combine unrelated students' records in one call

---

## EDD Eval Cases

```json
[
  {
    "case_id": "esr_emoji_distress_001",
    "description": "Junior student with clearly negative emoji sequence",
    "input": {
      "student_id": "S_017",
      "age_group": "junior",
      "date": "2026-06-28",
      "junior_input": { "emoji_sequence": "😔😴😠" }
    },
    "expected_output": {
      "emotional_valence_lt": -0.4,
      "energy_level_lt": 0.3,
      "social_withdrawal_flag": false,
      "distress_keywords_detected": [],
      "raw_input_type": "emoji"
    },
    "rubric": ["valence negative", "energy low", "no false withdrawal flag", "correct modality"]
  },
  {
    "case_id": "esr_text_withdrawal_002",
    "description": "Senior student with explicit withdrawal and distress language",
    "input": {
      "student_id": "S_004",
      "age_group": "senior",
      "date": "2026-06-28",
      "senior_input": {
        "prompt": "How are you feeling today and why?",
        "response": "I haven't talked to anyone in three days and I don't want to go to class"
      }
    },
    "expected_output": {
      "emotional_valence_lt": -0.5,
      "social_withdrawal_flag": true,
      "distress_keywords_detected_contains": ["haven't talked", "don't want to go"],
      "raw_input_type": "text"
    },
    "rubric": ["valence strongly negative", "withdrawal flag set", "distress keywords captured"]
  },
  {
    "case_id": "esr_emoji_positive_003",
    "description": "Junior student with positive emoji — no false escalation",
    "input": {
      "student_id": "S_011",
      "age_group": "junior",
      "date": "2026-06-28",
      "junior_input": { "emoji_sequence": "😊😄🌟" }
    },
    "expected_output": {
      "emotional_valence_gt": 0.5,
      "social_withdrawal_flag": false,
      "distress_keywords_detected": [],
      "raw_input_type": "emoji"
    },
    "rubric": ["valence positive", "no withdrawal flag", "no false distress keywords"]
  }
]
```

---

## See also

- `references/emoji_affect_table.md` — full emoji → (valence, energy) lookup table
- `../student-trend-tracker/SKILL.md` — next step after signal is produced
- `../pii-context-sanitizer/SKILL.md` — must run BEFORE this skill on raw input
- `specs/signal-detector.md` — sub-agent spec this skill belongs to
