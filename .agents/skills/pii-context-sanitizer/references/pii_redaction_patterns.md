# PII Redaction Patterns

Used by `pii-context-sanitizer` in Pass 1c to detect and redact contact
identifiers from `senior_input.response` fields.

---

## Email addresses

```
Pattern:  [a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}
Replaces: [REDACTED_EMAIL]
Examples:
  maya@school.edu          → [REDACTED_EMAIL]
  student.name@gmail.com   → [REDACTED_EMAIL]
  m.chen+tag@district.org  → [REDACTED_EMAIL]
```

---

## Phone numbers

```
Patterns (covers US and common international formats):
  \b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b          # 555-867-5309, 5558675309
  \(\d{3}\)\s?\d{3}[-.\s]?\d{4}              # (555) 867-5309
  \+\d{1,3}[\s-]?\d{1,4}[\s-]?\d{4,10}      # +1 555 867 5309

Replaces: [REDACTED_PHONE]
Examples:
  call me at 555-867-5309      → call me at [REDACTED_PHONE]
  my number is (555) 867 5309  → my number is [REDACTED_PHONE]
  +44 20 7946 0958             → [REDACTED_PHONE]
```

---

## Physical addresses

```
Detection: NER (LOC entity type)
Replaces: [REDACTED_ADDRESS]
Examples:
  I live at 42 Maple Street, Springfield  → I live at [REDACTED_ADDRESS]
  come find me near 5th and Main          → come find me near [REDACTED_ADDRESS]
```

Note: Regex is insufficient for address detection — NER is required. If NER
is unavailable, flag for manual review rather than skipping redaction.

---

## Social media handles

```
Pattern:  @[a-zA-Z0-9_.]{1,50}(?!\.[a-zA-Z])
Replaces: [REDACTED_HANDLE]
Examples:
  find me @maya_chen22    → find me [REDACTED_HANDLE]
  dm me at @student_life  → dm me at [REDACTED_HANDLE]

Exclusion: Do not redact @mentions that are part of an email address
(handled separately by the email pattern above).
```

---

## URLs and profile links

```
Pattern:  https?://[^\s]+
Replaces: [REDACTED_URL]
Examples:
  my profile is https://instagram.com/maya  → my profile is [REDACTED_URL]
```

---

## What NOT to redact

These patterns must be explicitly excluded to avoid over-sanitisation:

| Pattern | Reason to preserve |
|---|---|
| Distress language ("nobody", "alone", "disappear") | Core emotional signal |
| School or class names | Not personally identifying |
| Teacher-referenced locations ("the hallway", "lunch") | Context, not PII |
| Times and general dates ("last Monday", "yesterday") | Signal context |
| Single first names in emotional context ("my friend said") | Insufficient to identify; only redact in teacher notes via NER |

---

## Application order

Apply patterns in this order to avoid partial matches interfering with each other:

1. Email addresses (most specific — catches `user@domain`)
2. URLs (catches `https://...`)
3. Phone numbers (numeric patterns)
4. Social handles (catches `@handle`)
5. Physical addresses (NER — broadest, applied last)

---

## Redaction manifest entries

Each pattern type that fires contributes to `sanitisation_manifest.identifiers_redacted`:

```json
"identifiers_redacted": ["email", "phone", "address", "handle", "url"]
```

Only include types that were actually redacted in the given record.
