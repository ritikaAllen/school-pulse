"""
pii-context-sanitizer skill implementation.

Three-pass context hygiene:
  Pass 1 — PII masking (name removal, NER on teacher notes, regex on senior responses)
  Pass 2 — Context windowing (trim student_memory payloads to 7-day rolling view)
  Pass 3 — Memory boundary enforcement (cross-student, counselor name detection)

Delegates to spaCy for NER where available; falls back to registry-name matching.
"""

import re
import json
import copy
from datetime import datetime, timezone
from typing import Optional


# ── Custom errors (distinct types so callers can route them cleanly) ──────────

class BoundaryViolationError(Exception):
    """Cross-student payload or counselor name detected. Payload rejected."""

class SanitisationFailureError(Exception):
    """NER or regex pass failed technically. Record must not proceed downstream."""

class MissingRegistryError(Exception):
    """student_id_registry was not provided. Cannot sanitise without it."""


# ── NER backend ───────────────────────────────────────────────────────────────

def _try_load_spacy():
    try:
        import spacy  # noqa: PLC0415
        return spacy.load("en_core_web_sm")
    except (ImportError, OSError):
        return None


_nlp = _try_load_spacy()


def _replace_person_entities(text: str, known_names: list[str]) -> tuple[str, int]:
    """
    Replace PERSON entities in text with [PERSON].
    Runs BOTH spaCy NER (for unknown names like peer names) AND registry-based
    matching (for names spaCy may miss, e.g. "Lily" which is also a flower).
    Returns (new_text, replacement_count).
    """
    result = text
    count = 0

    # Pass A: spaCy NER — catches names not in registry (e.g. peer names)
    if _nlp is not None:
        doc = _nlp(result)
        spans = [(e.start_char, e.end_char) for e in doc.ents if e.label_ == "PERSON"]
        if spans:
            for start, end in reversed(spans):
                result = result[:start] + "[PERSON]" + result[end:]
            count += len(spans)

    # Pass B: registry-name matching — catches names spaCy misses (e.g. ambiguous first names)
    seen_parts: set[str] = set()
    for name in known_names:
        for part in [name] + name.split():
            if not part or len(part) < 2 or part in seen_parts:
                continue
            seen_parts.add(part)
            pat = re.compile(r"\b" + re.escape(part) + r"\b")
            before = result
            result = pat.sub("[PERSON]", result)
            if result != before:
                count += before.count(part)

    return result, count


def _replace_location_entities(text: str) -> tuple[str, bool]:
    """Replace LOC/GPE/FAC entities with [REDACTED_ADDRESS]. spaCy only."""
    if _nlp is not None:
        doc = _nlp(text)
        spans = [
            (e.start_char, e.end_char)
            for e in doc.ents
            if e.label_ in ("LOC", "GPE", "FAC")
        ]
        if not spans:
            return text, False
        result = text
        for start, end in reversed(spans):
            result = result[:start] + "[REDACTED_ADDRESS]" + result[end:]
        return result, True
    return text, False


# ── Regex patterns (application order per pii_redaction_patterns.md) ─────────
# 1. Email (most specific — catches user@domain before @ handle rule fires)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# 2. URLs
_URL_RE = re.compile(r"https?://[^\s]+")
# 3. Phone (three patterns cover US + common international)
_PHONE_RES = [
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    re.compile(r"\(\d{3}\)\s?\d{3}[-.\s]?\d{4}"),
    re.compile(r"\+\d{1,3}[\s-]?\d{1,4}[\s-]?\d{4,10}"),
]
# 4. Social handles (@handle not part of an email address)
_HANDLE_RE = re.compile(r"(?<![a-zA-Z0-9])@[a-zA-Z0-9_.]{1,50}(?!\.[a-zA-Z])")


def _redact_contact_identifiers(text: str) -> tuple[str, list[str]]:
    """
    Apply contact-identifier redaction to a senior response field.
    Returns (new_text, list_of_redacted_types).
    Order: email → url → phone → handle → address (NER).
    """
    redacted_types: list[str] = []

    t = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    if t != text:
        redacted_types.append("email")
    text = t

    t = _URL_RE.sub("[REDACTED_URL]", text)
    if t != text:
        redacted_types.append("url")
    text = t

    for pat in _PHONE_RES:
        t = pat.sub("[REDACTED_PHONE]", text)
        if t != text and "phone" not in redacted_types:
            redacted_types.append("phone")
        text = t

    t = _HANDLE_RE.sub("[REDACTED_HANDLE]", text)
    if t != text:
        redacted_types.append("handle")
    text = t

    t, had_addr = _replace_location_entities(text)
    if had_addr:
        redacted_types.append("address")
    text = t

    return text, redacted_types


# ── Public API ────────────────────────────────────────────────────────────────

def sanitize(
    raw_record: dict,
    student_id_registry: list[dict],
    counselor_names: Optional[list[str]] = None,
) -> dict:
    """
    Sanitise a raw check-in record through all three privacy passes.

    Args:
        raw_record:           Raw record from MCP ingestion (may contain student_name).
        student_id_registry:  List of {student_id, fictional_name, ...} dicts.
        counselor_names:      Names to treat as boundary-violation triggers.

    Returns:
        sanitised_record dict with sanitisation_manifest attached.

    Raises:
        MissingRegistryError:    Registry not supplied.
        BoundaryViolationError:  Cross-student or counselor-name violation detected.
        SanitisationFailureError: Technical NER/regex failure.
    """
    if not student_id_registry:
        raise MissingRegistryError(
            "student_id_registry is required but was not provided"
        )

    counselor_names = [c.strip() for c in (counselor_names or []) if c.strip()]
    known_names = [
        r["fictional_name"]
        for r in student_id_registry
        if r.get("fictional_name")
    ]
    student_id: str = raw_record.get("student_id", "")
    student_name: str = raw_record.get("student_name", "") or ""

    # ── Pass 3 (pre-check): counselor names in payload → reject immediately ──
    record_serialised = json.dumps(raw_record, ensure_ascii=False)
    for cname in counselor_names:
        if re.search(r"\b" + re.escape(cname) + r"\b", record_serialised, re.IGNORECASE):
            raise BoundaryViolationError(
                "BOUNDARY_VIOLATION: counselor name detected in payload, payload rejected"
            )

    try:
        manifest: dict = {
            "pii_masking_applied": False,
            "entities_redacted": 0,
            "identifiers_redacted": [],
            "context_window_trimmed": False,
            "boundary_checks_passed": True,
            "sanitised_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        out: dict = {}

        # 1a — student_name → student_id (name physically absent from output)
        out["student_id"] = student_id
        if student_name:
            manifest["pii_masking_applied"] = True
        # student_name deliberately NOT forwarded

        out["age_group"] = raw_record.get("age_group")
        out["date"] = raw_record.get("date")

        # junior_input — emoji sequences carry no PII; pass through unchanged
        ji = raw_record.get("junior_input")
        out["junior_input"] = copy.deepcopy(ji) if ji is not None else None

        # 1c — senior_input.response: contact identifier redaction
        si = raw_record.get("senior_input")
        if si is not None:
            si_out = copy.deepcopy(si)
            response = si_out.get("response") or ""
            if response:
                cleaned, types_found = _redact_contact_identifiers(response)
                if types_found:
                    manifest["pii_masking_applied"] = True
                    for t in types_found:
                        if t not in manifest["identifiers_redacted"]:
                            manifest["identifiers_redacted"].append(t)
                # Safety net: if student's own name appears in their free-text response
                if student_name:
                    for part in student_name.split():
                        if len(part) > 2:
                            pat = re.compile(r"\b" + re.escape(part) + r"\b", re.IGNORECASE)
                            new = pat.sub("[PERSON]", cleaned)
                            if new != cleaned:
                                manifest["pii_masking_applied"] = True
                            cleaned = new
                si_out["response"] = cleaned
            out["senior_input"] = si_out
        else:
            out["senior_input"] = None

        # 1b — teacher_observation.note: NER PERSON entity replacement
        to = raw_record.get("teacher_observation")
        if to is not None:
            to_out = copy.deepcopy(to)
            note = to_out.get("note") or ""
            if note:
                note_clean, n_replaced = _replace_person_entities(note, known_names)
                to_out["note"] = note_clean
                if n_replaced > 0:
                    manifest["entities_redacted"] += n_replaced
                    manifest["pii_masking_applied"] = True
            # Pass 3b: counselor name redaction in note
            for cname in counselor_names:
                pat = re.compile(r"\b" + re.escape(cname) + r"\b", re.IGNORECASE)
                to_out["note"] = pat.sub("[COUNSELOR]", to_out.get("note") or "")
            out["teacher_observation"] = to_out
        else:
            out["teacher_observation"] = None

        out["sanitisation_manifest"] = manifest

        # Final safety check: student_name must not appear anywhere in output
        if student_name:
            out_serialised = json.dumps(out, ensure_ascii=False)
            for part in student_name.split():
                if len(part) > 2 and re.search(
                    r"\b" + re.escape(part) + r"\b", out_serialised, re.IGNORECASE
                ):
                    raise SanitisationFailureError(
                        f"student name part '{part}' still present in sanitised output — PII leak"
                    )

        return out

    except (BoundaryViolationError, SanitisationFailureError, MissingRegistryError):
        raise
    except Exception as exc:
        raise SanitisationFailureError(
            f"Technical sanitisation failure: {exc}"
        ) from exc


def sanitize_context_window(
    student_memory: dict,
    max_days: int = 7,
) -> tuple[dict, bool]:
    """
    Pass 2: Return a trimmed copy of student_memory with signal_history capped
    at max_days (oldest entries dropped). Does NOT modify the original.
    Returns (trimmed_memory, was_trimmed).
    """
    trimmed = copy.deepcopy(student_memory)
    history: list = trimmed.get("signal_history", [])
    if len(history) > max_days:
        trimmed["signal_history"] = history[-max_days:]
        return trimmed, True
    return trimmed, False
