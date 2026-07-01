"""
Privacy Guard Sub-Agent

Mandatory first pipeline gate. Handles routing, the blocking gate, and
error propagation. Delegates all sanitisation logic to the
pii-context-sanitizer skill — no sanitisation logic lives here.

Returns:
    {"status": "ok",    "record": sanitised_record}
    {"status": "error", "error_type": <type>, "message": <str>, "record": None}

error_type values: "missing_registry" | "boundary_violation" | "sanitisation_failure"
"""

import importlib.util
from pathlib import Path

# ── Load skill (path-based import because directory name contains a dash) ─────
_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "pii-context-sanitizer" / "sanitizer.py"
_spec = importlib.util.spec_from_file_location("sanitizer", _SKILL_PATH)
_sanitizer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sanitizer)

BoundaryViolationError = _sanitizer.BoundaryViolationError
SanitisationFailureError = _sanitizer.SanitisationFailureError
MissingRegistryError = _sanitizer.MissingRegistryError


def process(
    raw_record: dict,
    student_id_registry: list[dict],
    counselor_names: list[str] | None = None,
) -> dict:
    """
    Route a raw check-in record through the privacy gate.

    The blocking gate enforces: a record with boundary_checks_passed=False
    is NEVER returned in a form a downstream caller can use.
    """
    try:
        sanitised = _sanitizer.sanitize(raw_record, student_id_registry, counselor_names)

        # Blocking gate — belt-and-suspenders check in addition to the skill's own logic
        manifest = sanitised.get("sanitisation_manifest", {})
        if not manifest.get("boundary_checks_passed", False):
            return {
                "status": "error",
                "error_type": "boundary_violation",
                "message": "Record failed boundary checks and is blocked from downstream processing",
                "record": None,
            }

        return {"status": "ok", "record": sanitised}

    except MissingRegistryError as exc:
        return {"status": "error", "error_type": "missing_registry", "message": str(exc), "record": None}

    except BoundaryViolationError as exc:
        return {"status": "error", "error_type": "boundary_violation", "message": str(exc), "record": None}

    except SanitisationFailureError as exc:
        return {"status": "error", "error_type": "sanitisation_failure", "message": str(exc), "record": None}
