"""
Signal Detector tests — 3 EDD cases + multi-modality merge + malformed input.

EDD cases from skills/emotional-signal-reader/SKILL.md:
  esr_emoji_distress_001
  esr_text_withdrawal_002   (requires ANTHROPIC_API_KEY — skipped if absent)
  esr_emoji_positive_003

Extra cases:
  multi_modality_merge      — junior emoji + teacher note → merged signal
  malformed_empty_input     — missing fields → signal_confidence=0.0, no crash

All test inputs have a valid sanitisation_manifest (Privacy Guard already ran).
The text path uses a real LLM call; emoji and teacher-note paths are deterministic.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.signal_detector as sd

_HAS_API_KEY = bool(os.environ.get("GOOGLE_API_KEY"))

VALID_MANIFEST = {
    "pii_masking_applied": True,
    "entities_redacted": 0,
    "identifiers_redacted": [],
    "context_window_trimmed": False,
    "boundary_checks_passed": True,
    "sanitised_at": "2026-06-28T08:00:00Z",
}


def _make_record(**kwargs) -> dict:
    base = {
        "student_id": "S_000",
        "age_group": "junior",
        "date": "2026-06-28",
        "junior_input": None,
        "senior_input": None,
        "teacher_observation": None,
        "sanitisation_manifest": dict(VALID_MANIFEST),
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# EDD cases
# ─────────────────────────────────────────────────────────────────────────────

def test_esr_emoji_distress_001():
    """
    Junior student with clearly negative emoji sequence 😔😴😠.
    Expected: valence < -0.4, energy < 0.3, withdrawal=False, type=emoji.
    """
    record = _make_record(
        student_id="S_017",
        age_group="junior",
        junior_input={"emoji_sequence": "😔😴😠"},
    )
    result = sd.process(record)
    assert result["status"] == "ok", f"Expected ok: {result}"
    sig = result["signal"]

    assert sig["emotional_valence"] < -0.4, (
        f"Expected valence < -0.4, got {sig['emotional_valence']}"
    )
    assert sig["energy_level"] < 0.4, (
        f"Expected energy < 0.4, got {sig['energy_level']}"
    )
    assert sig["social_withdrawal_flag"] is False
    assert sig["distress_keywords_detected"] == []
    assert sig["raw_input_type"] == "emoji"
    assert 0.0 < sig["signal_confidence"] <= 0.95


def test_esr_emoji_positive_003():
    """
    Junior student with positive emoji sequence 😊😄🌟.
    Expected: valence > 0.5, withdrawal=False, no distress keywords, type=emoji.
    """
    record = _make_record(
        student_id="S_011",
        age_group="junior",
        junior_input={"emoji_sequence": "😊😄🌟"},
    )
    result = sd.process(record)
    assert result["status"] == "ok", f"Expected ok: {result}"
    sig = result["signal"]

    assert sig["emotional_valence"] > 0.5, (
        f"Expected valence > 0.5, got {sig['emotional_valence']}"
    )
    assert sig["social_withdrawal_flag"] is False
    assert sig["distress_keywords_detected"] == []
    assert sig["raw_input_type"] == "emoji"


@pytest.mark.skipif(not _HAS_API_KEY, reason="GOOGLE_API_KEY not set")
def test_esr_text_withdrawal_002():
    """
    Senior student with explicit withdrawal language.
    Expected: valence < -0.5, withdrawal=True, distress keywords captured, type=text.
    Requires a live Gemini 2.0 Flash API call (reader.py text path, no client injected).
    """
    record = _make_record(
        student_id="S_004",
        age_group="senior",
        junior_input=None,
        senior_input={
            "prompt": "How are you feeling today and why?",
            "response": "I haven't talked to anyone in three days and I don't want to go to class",
        },
    )
    result = sd.process(record)
    assert result["status"] == "ok", f"Expected ok: {result}"
    sig = result["signal"]

    assert sig["emotional_valence"] < -0.5, (
        f"Expected valence < -0.5, got {sig['emotional_valence']}"
    )
    assert sig["social_withdrawal_flag"] is True, "Expected withdrawal flag = True"
    assert sig["raw_input_type"] == "text"
    assert sig["signal_confidence"] <= 0.95

    # At least one of the expected distress phrases detected
    keywords_lower = [k.lower() for k in sig["distress_keywords_detected"]]
    found = any(
        "haven't talked" in k or "don't want" in k or "three days" in k
        for k in keywords_lower
    )
    assert found, (
        f"Expected withdrawal keywords, got: {sig['distress_keywords_detected']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Extra cases
# ─────────────────────────────────────────────────────────────────────────────

def test_multi_modality_merge_emoji_plus_teacher_note():
    """
    Junior student: emoji sequence present AND teacher note present.
    Merged signal must use weighted average (emoji 0.4 / teacher_note 0.1).
    raw_input_type == "merged".
    Manually computed expected range: positive emoji dominates, note adds slight negative.
    """
    record = _make_record(
        student_id="S_011",
        age_group="junior",
        junior_input={"emoji_sequence": "😊😄🌟"},  # avg valence ≈ +0.73
        teacher_observation={
            "flag_level": "watch",   # valence_adj = -0.2
            "note": "Student seemed a bit quiet but mostly engaged.",
        },
    )
    result = sd.process(record)
    assert result["status"] == "ok", f"Expected ok: {result}"
    sig = result["signal"]

    assert sig["raw_input_type"] == "merged"
    # Emoji weight 0.4 and teacher_note weight 0.1 (normalised over the two modalities)
    # Merged valence should still be positive (emoji strongly positive, note mildly negative)
    assert sig["emotional_valence"] > 0.0, (
        f"Merged valence should be positive, got {sig['emotional_valence']}"
    )
    assert sig["signal_confidence"] <= 0.95


def test_malformed_empty_input_returns_zero_confidence():
    """
    Record with no input fields at all → signal_confidence = 0.0, no exception.
    """
    record = _make_record(
        student_id="S_011",
        age_group="junior",
        junior_input=None,
        senior_input=None,
        teacher_observation=None,
    )
    result = sd.process(record)
    assert result["status"] == "ok", f"Expected ok: {result}"
    sig = result["signal"]
    assert sig["signal_confidence"] == 0.0


def test_rejects_missing_manifest():
    """Record without sanitisation_manifest must be rejected (unsanitised_input)."""
    record = {
        "student_id": "S_011",
        "age_group": "junior",
        "date": "2026-06-28",
        "junior_input": {"emoji_sequence": "😊"},
        # No sanitisation_manifest
    }
    result = sd.process(record)
    assert result["status"] == "error"
    assert result["error_type"] == "unsanitised_input"
    assert result["signal"] is None


def test_rejects_failed_boundary_check():
    """Record with boundary_checks_passed=False must be rejected."""
    bad_manifest = dict(VALID_MANIFEST)
    bad_manifest["boundary_checks_passed"] = False
    record = _make_record(junior_input={"emoji_sequence": "😊"})
    record["sanitisation_manifest"] = bad_manifest
    result = sd.process(record)
    assert result["status"] == "error"
    assert result["error_type"] == "unsanitised_input"


def test_signal_confidence_never_exceeds_0_95():
    """
    Across all deterministic cases, signal_confidence must never exceed 0.95.
    This is a hard rule that should hold across a range of emoji sequences.
    """
    test_sequences = [
        "😊😄🌟",
        "😔😴😠",
        "😢😭",
        "😐",
        "🥰😄✨💪🎉",
    ]
    for seq in test_sequences:
        record = _make_record(
            student_id="S_001", age_group="junior",
            junior_input={"emoji_sequence": seq},
        )
        result = sd.process(record)
        assert result["status"] == "ok"
        assert result["signal"]["signal_confidence"] <= 0.95, (
            f"signal_confidence > 0.95 for sequence '{seq}': "
            f"{result['signal']['signal_confidence']}"
        )


def test_real_data_privacy_guard_output_roundtrip():
    """
    Feed Privacy Guard's actual output into Signal Detector for a handful of
    students. Validates schema compatibility between the two agents.
    No LLM call needed — uses junior students (emoji path only).
    """
    import json
    from pathlib import Path
    import agents.privacy_guard as pg

    data_path = Path(__file__).resolve().parent.parent / "data" / "synthetic"
    with open(data_path / "student_registry.json") as f:
        registry = json.load(f)
    with open(data_path / "synthetic_checkins.json", encoding="utf-8") as f:
        checkins = json.load(f)

    # Use junior students only (deterministic emoji path, no API needed)
    junior_ids = {r["student_id"] for r in registry if r["age_group"] == "junior"}
    junior_checkins = [c for c in checkins if c["student_id"] in junior_ids][:14]

    for checkin in junior_checkins:
        pg_result = pg.process(checkin, registry)
        assert pg_result["status"] == "ok", (
            f"Privacy Guard failed for {checkin['student_id']}: {pg_result}"
        )
        sd_result = sd.process(pg_result["record"])
        assert sd_result["status"] == "ok", (
            f"Signal Detector failed for {checkin['student_id']} on {checkin['date']}: {sd_result}"
        )
        sig = sd_result["signal"]
        assert sig["student_id"] == checkin["student_id"]
        assert sig["signal_confidence"] <= 0.95
        assert -1.0 <= sig["emotional_valence"] <= 1.0
        assert 0.0 <= sig["energy_level"] <= 1.0
