"""
Signal Detector Sub-Agent

Reads sanitised check-in records and converts them into structured signal objects.
Handles routing, precondition checking, and error propagation.
Delegates all parsing logic to the emotional-signal-reader skill.

Precondition: input record MUST have sanitisation_manifest with
boundary_checks_passed=True. Any record that fails this check is rejected.

Returns:
    {"status": "ok",    "signal": signal_object}
    {"status": "error", "error_type": <type>, "message": <str>, "signal": None}

error_type values:
    "unsanitised_input"  — manifest absent or boundary_checks_passed=False
    "malformed_record"   — required fields missing / schema mismatch
    "skill_failure"      — emotional-signal-reader returned no output
"""

import importlib.util
from pathlib import Path

# ── Load skill ────────────────────────────────────────────────────────────────
_SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "emotional-signal-reader" / "reader.py"
)
_spec = importlib.util.spec_from_file_location("reader", _SKILL_PATH)
_reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reader)


def process(sanitised_record: dict, llm_client=None) -> dict:
    """
    Convert a sanitised check-in record into a signal object.

    Args:
        sanitised_record: Must include sanitisation_manifest.boundary_checks_passed=True.
        llm_client:       Optional Anthropic client (injected for testing).
    """
    # ── Precondition check ────────────────────────────────────────────────────
    manifest = sanitised_record.get("sanitisation_manifest")
    if manifest is None:
        return {
            "status": "error",
            "error_type": "unsanitised_input",
            "message": "sanitisation_manifest is absent — record rejected",
            "signal": None,
        }
    if not manifest.get("boundary_checks_passed", False):
        return {
            "status": "error",
            "error_type": "unsanitised_input",
            "message": "boundary_checks_passed=False — record rejected, not passed to skill",
            "signal": None,
        }

    # ── Basic schema check ────────────────────────────────────────────────────
    if not sanitised_record.get("student_id") or not sanitised_record.get("date"):
        return {
            "status": "error",
            "error_type": "malformed_record",
            "message": "student_id or date field is missing",
            "signal": None,
        }

    # ── Delegate to skill ─────────────────────────────────────────────────────
    try:
        signal = _reader.read_signal(sanitised_record, llm_client=llm_client)
    except Exception as exc:
        return {
            "status": "error",
            "error_type": "skill_failure",
            "message": f"emotional-signal-reader raised: {exc}",
            "signal": None,
        }

    if signal is None:
        return {
            "status": "error",
            "error_type": "skill_failure",
            "message": "emotional-signal-reader returned None",
            "signal": None,
        }

    return {"status": "ok", "signal": signal}
