"""
Memory Keeper Sub-Agent

Owns and operates per-student 7-day rolling memory.
Delegates all trend computation to the student-trend-tracker skill.
The in-process dict (memory_store) is the demo implementation;
the mcp-layer.md production upgrade path replaces it with a persistent store.

memory_store: dict[student_id, student_memory]  — managed by the caller;
              passed in on every call and returned with updates applied.

Returns:
    {"status": "ok",    "report": trend_report, "memory_store": updated_store}
    {"status": "error", "error_type": <type>, "message": <str>,
     "report": None, "memory_store": memory_store}

error_type values:
    "cross_student_payload"  — signal student_id doesn't match expected scope
    "memory_write_failure"   — tracker raised during update
    "skill_failure"          — tracker returned None
"""

import importlib.util
from pathlib import Path

# ── Load skill ────────────────────────────────────────────────────────────────
_SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "student-trend-tracker" / "tracker.py"
)
_spec = importlib.util.spec_from_file_location("tracker", _SKILL_PATH)
_tracker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tracker)


def process(
    signal: dict,
    memory_store: dict | None = None,
    expected_student_id: str | None = None,
) -> dict:
    """
    Integrate a new signal and emit a trend report.

    Args:
        signal:              Validated signal object from Signal Detector.
        memory_store:        Dict of {student_id: student_memory}. Pass None or {}
                             to start with an empty store.
        expected_student_id: If set, reject any signal whose student_id differs
                             (cross-student boundary enforcement).

    Returns:
        Result dict; memory_store is always present in return value (updated or original).
    """
    memory_store = dict(memory_store) if memory_store else {}
    signal_student_id: str = signal.get("student_id", "")

    # ── Cross-student boundary check ─────────────────────────────────────────
    if expected_student_id and signal_student_id != expected_student_id:
        return {
            "status": "error",
            "error_type": "cross_student_payload",
            "message": (
                f"signal student_id '{signal_student_id}' does not match "
                f"expected '{expected_student_id}' — boundary violation"
            ),
            "report": None,
            "memory_store": memory_store,
        }

    existing_memory = memory_store.get(signal_student_id)

    # ── Delegate to skill ─────────────────────────────────────────────────────
    try:
        result = _tracker.track(signal, existing_memory)
    except Exception as exc:
        return {
            "status": "error",
            "error_type": "memory_write_failure",
            "message": f"student-trend-tracker raised: {exc}",
            "report": None,
            "memory_store": memory_store,
        }

    if result is None:
        return {
            "status": "error",
            "error_type": "skill_failure",
            "message": "student-trend-tracker returned None",
            "report": None,
            "memory_store": memory_store,
        }

    trend_report, updated_memory = result

    # Persist updated memory
    updated_store = dict(memory_store)
    updated_store[signal_student_id] = updated_memory

    return {
        "status": "ok",
        "report": trend_report,
        "memory_store": updated_store,
    }


def read_only_report(student_id: str, memory_store: dict) -> dict:
    """
    Emit a trend report from current memory without integrating a new signal.
    Used when Orchestrator re-queries without new data.
    """
    if not memory_store or student_id not in memory_store:
        return {
            "status": "error",
            "error_type": "no_memory",
            "message": f"No memory found for student_id '{student_id}'",
            "report": None,
        }

    mem = memory_store[student_id]
    return {
        "status": "ok",
        "report": {
            "student_id": student_id,
            "trend_direction": mem.get("trend_direction", "stable"),
            "delta_from_baseline": 0.0,
            "consecutive_low_days": mem.get("consecutive_low_days", 0),
            "pattern_break_detected": False,
            "recommended_priority": "routine",
        },
    }
