"""
Privacy Guard tests — 3 EDD cases + 3 adversarial cases.

EDD cases from skills/pii-context-sanitizer/SKILL.md:
  pcs_teacher_note_redaction_001
  pcs_email_redaction_002
  pcs_clean_note_passthrough_003

Adversarial cases:
  adv_name_in_senior_response    — student name embedded in their own free-text
  adv_boundary_violation         — counselor name in payload → reject
  adv_empty_optional_fields      — no PII, missing optional fields → no crash
"""

import json
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.privacy_guard as pg

# Minimal registry sufficient for the test cases
REGISTRY = [
    {"student_id": "S_004", "fictional_name": "Rohan Mehta",   "age_group": "senior"},
    {"student_id": "S_011", "fictional_name": "Kezia Adeyemi", "age_group": "junior"},
    {"student_id": "S_012", "fictional_name": "Priya Nair",    "age_group": "senior"},
    {"student_id": "S_017", "fictional_name": "Lily Chen",     "age_group": "junior"},
    {"student_id": "S_042", "fictional_name": "Maya Kapoor",   "age_group": "senior"},
]


# ─────────────────────────────────────────────────────────────────────────────
# EDD cases
# ─────────────────────────────────────────────────────────────────────────────

def test_pcs_teacher_note_redaction_001():
    """
    Teacher note with two student names — both replaced by [PERSON].
    entities_redacted == 2; emotional content preserved.
    """
    raw = {
        "student_id": "S_042",
        "student_name": "Maya Kapoor",
        "age_group": "senior",
        "date": "2026-06-28",
        "teacher_observation": {
            "flag_level": "concern",
            "note": "Maya seems very withdrawn. Her friend Priya mentioned she hasn't been eating.",
        },
    }
    result = pg.process(raw, REGISTRY)
    assert result["status"] == "ok", f"Expected ok, got: {result}"

    rec = result["record"]
    note = rec["teacher_observation"]["note"]
    manifest = rec["sanitisation_manifest"]

    # Both names replaced
    assert "Maya" not in note, f"'Maya' still present in note: {note}"
    assert "Priya" not in note, f"'Priya' still present in note: {note}"
    assert "[PERSON]" in note, "Expected [PERSON] tokens in note"
    assert manifest["entities_redacted"] >= 2, (
        f"Expected ≥2 entities redacted, got {manifest['entities_redacted']}"
    )
    assert manifest["pii_masking_applied"] is True

    # Emotional content preserved
    assert "withdrawn" in note.lower()
    assert "hasn't been eating" in note.lower()

    # student_name absent everywhere
    out_str = json.dumps(rec)
    assert "Maya Kapoor" not in out_str
    assert manifest["boundary_checks_passed"] is True


def test_pcs_email_redaction_002():
    """
    Senior response with email address — redacted, emotional content preserved.
    """
    raw = {
        "student_id": "S_004",
        "student_name": "Rohan Mehta",
        "age_group": "senior",
        "date": "2026-06-28",
        "senior_input": {
            "prompt": "How are you feeling today and why?",
            "response": "I can be reached at maya@school.edu if anyone cares",
        },
    }
    result = pg.process(raw, REGISTRY)
    assert result["status"] == "ok", f"Expected ok, got: {result}"

    rec = result["record"]
    response = rec["senior_input"]["response"]
    manifest = rec["sanitisation_manifest"]

    assert "[REDACTED_EMAIL]" in response, f"Email not redacted in: {response}"
    assert "maya@school.edu" not in response
    assert "if anyone cares" in response, "Emotional context was over-redacted"
    assert "email" in manifest["identifiers_redacted"]
    assert manifest["boundary_checks_passed"] is True


def test_pcs_clean_note_passthrough_003():
    """
    Teacher note with no PII — passes through unchanged (matches EDD input exactly).
    EDD input omits student_name, so pii_masking_applied=False and entities_redacted=0.
    """
    raw = {
        "student_id": "S_011",
        # No student_name field — matching EDD input exactly
        "age_group": "junior",
        "date": "2026-06-28",
        "teacher_observation": {
            "flag_level": "none",
            "note": "Student showed good engagement in group activity today.",
        },
    }
    result = pg.process(raw, REGISTRY)
    assert result["status"] == "ok", f"Expected ok, got: {result}"

    rec = result["record"]
    note = rec["teacher_observation"]["note"]
    manifest = rec["sanitisation_manifest"]

    assert note == "Student showed good engagement in group activity today."
    assert manifest["entities_redacted"] == 0
    assert manifest["pii_masking_applied"] is False
    assert manifest["boundary_checks_passed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial cases
# ─────────────────────────────────────────────────────────────────────────────

def test_adv_name_in_senior_response():
    """
    Adversarial: student's own name embedded inside senior_input.response.
    Confirms PII detection is NOT purely positional — safety net must fire.
    """
    raw = {
        "student_id": "S_017",
        "student_name": "Lily Chen",
        "age_group": "senior",
        "date": "2026-06-28",
        "senior_input": {
            "prompt": "How are you feeling today?",
            "response": "My name is Lily and I feel like nobody cares about me.",
        },
    }
    result = pg.process(raw, REGISTRY)
    assert result["status"] == "ok", f"Expected ok, got: {result}"

    rec = result["record"]
    response = rec["senior_input"]["response"]
    out_str = json.dumps(rec)

    # Lily must not appear in output
    assert "Lily" not in out_str, (
        f"Student first name 'Lily' still present in output: {out_str}"
    )
    # Emotional signal preserved
    assert "nobody cares" in response


def test_adv_boundary_violation_counselor_name():
    """
    Adversarial: counselor name present in payload → reject entire payload.
    Must return error, not a (partially) sanitised record.
    """
    raw = {
        "student_id": "S_004",
        "student_name": "Rohan Mehta",
        "age_group": "senior",
        "date": "2026-06-28",
        "teacher_observation": {
            "flag_level": "concern",
            "note": "Referred by counselor Dr. Sarah Thompson. Student is very distressed.",
        },
    }
    result = pg.process(raw, REGISTRY, counselor_names=["Sarah Thompson"])

    # Must NOT succeed — boundary violation must halt, not warn-and-continue
    assert result["status"] == "error", (
        f"Expected error for counselor-name boundary violation, got: {result}"
    )
    assert result["error_type"] == "boundary_violation"
    assert result["record"] is None


def test_adv_empty_optional_fields():
    """
    Adversarial: no PII, missing optional fields (junior_input, teacher_observation).
    Must not crash; manifest counts must be zero; boundary_checks_passed = True.
    """
    raw = {
        "student_id": "S_011",
        "student_name": "",   # empty string — no name to redact
        "age_group": "junior",
        "date": "2026-06-28",
        # No junior_input, no senior_input, no teacher_observation
    }
    result = pg.process(raw, REGISTRY)
    assert result["status"] == "ok", f"Expected ok, got: {result}"

    rec = result["record"]
    manifest = rec["sanitisation_manifest"]

    assert manifest["entities_redacted"] == 0
    assert manifest["identifiers_redacted"] == []
    assert manifest["boundary_checks_passed"] is True
    assert rec["junior_input"] is None
    assert rec["senior_input"] is None
    assert rec["teacher_observation"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Cross-cutting: student_name never in output across registry
# ─────────────────────────────────────────────────────────────────────────────

def test_no_student_name_in_any_output():
    """
    Run real teacher_observations.json data through Privacy Guard.
    Zero fictional names must appear anywhere in any sanitised output.
    """
    import json as _json
    data_path = Path(__file__).resolve().parent.parent / "data" / "synthetic"
    registry_path = data_path / "student_registry.json"
    obs_path = data_path / "teacher_observations.json"

    with open(registry_path, encoding="utf-8") as f:
        full_registry = _json.load(f)
    with open(obs_path, encoding="utf-8") as f:
        observations = _json.load(f)

    # Build a set of all first names and full names to scan for leaks
    name_parts = set()
    for r in full_registry:
        fname = r.get("fictional_name", "")
        if fname:
            for part in fname.split():
                if len(part) > 2:
                    name_parts.add(part.lower())

    for obs in observations:
        raw = {
            "student_id": obs["student_id"],
            "student_name": next(
                (r["fictional_name"] for r in full_registry
                 if r["student_id"] == obs["student_id"]), ""
            ),
            "age_group": next(
                (r["age_group"] for r in full_registry
                 if r["student_id"] == obs["student_id"]), "junior"
            ),
            "date": obs["date"],
            "teacher_observation": {
                "flag_level": obs["flag_level"],
                "note": obs["note"],
            },
        }
        result = pg.process(raw, full_registry)
        assert result["status"] == "ok", (
            f"Unexpected error for {obs['student_id']} on {obs['date']}: {result}"
        )

        out_str = _json.dumps(result["record"]).lower()
        for part in name_parts:
            assert part not in out_str, (
                f"Name part '{part}' leaked into sanitised output for "
                f"{obs['student_id']} on {obs['date']}"
            )
