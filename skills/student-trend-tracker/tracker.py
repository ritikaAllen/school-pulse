"""
student-trend-tracker skill implementation.

Updates a student's 7-day rolling memory with a new signal, recomputes
trend direction and baseline, detects pattern breaks, and emits a trend_report.

This skill is the ONLY component that reads/writes student_memory.
All computation is pure Python — no LLM calls, no external I/O.

Thresholds (from memory-keeper.md):
  low_valence_boundary : < -0.3
  pattern_break_delta  : > 0.4 drop from baseline in one day
  crisis_watch_trigger : consecutive_low_days >= 3
  baseline_minimum_days: 3
  rolling_window_max   : 7
"""

import copy
from datetime import date as _date, datetime
from typing import Optional


# ── Thresholds ────────────────────────────────────────────────────────────────
LOW_VALENCE_BOUNDARY = -0.3
PATTERN_BREAK_DELTA = 0.4
CRISIS_WATCH_TRIGGER = 3
BASELINE_MINIMUM_DAYS = 3
ROLLING_WINDOW_MAX = 7


def _new_memory(student_id: str, age_group: str = "unknown") -> dict:
    return {
        "student_id": student_id,
        "age_group": age_group,
        "signal_history": [],
        "rolling_baseline": {"avg_valence": None, "avg_energy": None},
        "trend_direction": "stable",
        "consecutive_low_days": 0,
        "last_counselor_referral": None,
    }


def _mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _dates_are_consecutive(d1_str: str, d2_str: str) -> bool:
    """Return True if d2 is exactly one calendar day after d1."""
    try:
        d1 = datetime.fromisoformat(d1_str).date()
        d2 = datetime.fromisoformat(d2_str).date()
        return (d2 - d1).days == 1
    except (ValueError, TypeError):
        return False


def track(signal: dict, student_memory: Optional[dict] = None) -> tuple[dict, dict]:
    """
    Integrate a new signal into rolling memory and emit a trend_report.

    Args:
        signal:         Validated signal object from Signal Detector.
        student_memory: Existing memory for this student, or None to initialise.

    Returns:
        (trend_report, updated_memory) — caller is responsible for persisting memory.

    The returned memory is a NEW dict; the input is not mutated.
    """
    student_id: str = signal["student_id"]
    sig_date: str = signal["date"]
    sig_valence: float = float(signal["emotional_valence"])
    sig_energy: float = float(signal["energy_level"])
    sig_withdrawal: bool = bool(signal.get("social_withdrawal_flag", False))

    # ── Step 1: Load or initialise memory ────────────────────────────────────
    if student_memory is None:
        mem = _new_memory(student_id, signal.get("age_group", "unknown"))
    else:
        mem = copy.deepcopy(student_memory)

    # ── Step 2: Integrate signal into rolling window ──────────────────────────
    history: list[dict] = mem.get("signal_history", [])

    # Replace existing entry for same date (handles re-runs / corrections)
    history = [h for h in history if h.get("date") != sig_date]

    new_entry = {
        "date": sig_date,
        "emotional_valence": sig_valence,
        "energy_level": sig_energy,
        "social_withdrawal_flag": sig_withdrawal,
    }
    history.append(new_entry)

    # Sort by date ascending so oldest is index 0
    history.sort(key=lambda h: h["date"])

    # Drop oldest if window exceeds max
    if len(history) > ROLLING_WINDOW_MAX:
        history = history[-ROLLING_WINDOW_MAX:]

    mem["signal_history"] = history

    # ── Step 3: Recompute rolling baseline ────────────────────────────────────
    # Baseline is invalid until 3+ days exist
    if len(history) >= BASELINE_MINIMUM_DAYS:
        mem["rolling_baseline"]["avg_valence"] = round(
            _mean([h["emotional_valence"] for h in history]), 4
        )
        mem["rolling_baseline"]["avg_energy"] = round(
            _mean([h["energy_level"] for h in history]), 4
        )
    else:
        mem["rolling_baseline"]["avg_valence"] = None
        mem["rolling_baseline"]["avg_energy"] = None

    baseline_valence: Optional[float] = mem["rolling_baseline"]["avg_valence"]

    # ── Step 4: Detect pattern break ─────────────────────────────────────────
    # Requires established baseline (3+ days) AND baseline was computed BEFORE
    # today's entry was appended. Recompute pre-append baseline for accuracy.
    pre_append_history = [h for h in history if h["date"] != sig_date]
    pre_baseline_valence: Optional[float] = None
    if len(pre_append_history) >= BASELINE_MINIMUM_DAYS:
        pre_baseline_valence = _mean(
            [h["emotional_valence"] for h in pre_append_history]
        )

    pattern_break_detected = False
    delta_from_baseline = 0.0
    if pre_baseline_valence is not None:
        delta = pre_baseline_valence - sig_valence
        delta_from_baseline = round(delta, 4)
        if delta > PATTERN_BREAK_DELTA:
            pattern_break_detected = True

    # ── Step 5: Update consecutive low days ──────────────────────────────────
    # Check for gaps: if the most recent prior day is not consecutive, reset
    prior_history = [h for h in history if h["date"] < sig_date]
    if prior_history:
        most_recent_prior = prior_history[-1]
        if not _dates_are_consecutive(most_recent_prior["date"], sig_date):
            # Gap detected — reset consecutive counter
            mem["consecutive_low_days"] = 0

    if sig_valence < LOW_VALENCE_BOUNDARY:
        mem["consecutive_low_days"] = mem.get("consecutive_low_days", 0) + 1
    else:
        mem["consecutive_low_days"] = 0

    consecutive_low_days: int = mem["consecutive_low_days"]

    # ── Step 6: Compute trend direction ──────────────────────────────────────
    # crisis_watch overrides all other trends
    if consecutive_low_days >= CRISIS_WATCH_TRIGGER or pattern_break_detected:
        trend_direction = "crisis_watch"
    else:
        # Use last 3 entries (or all available) for monotonic check
        recent = history[-3:]
        if len(recent) >= 2:
            valences = [h["emotional_valence"] for h in recent]
            if all(valences[i] < valences[i - 1] for i in range(1, len(valences))):
                trend_direction = "declining"
            elif all(valences[i] > valences[i - 1] for i in range(1, len(valences))):
                trend_direction = "improving"
            else:
                trend_direction = "stable"
        else:
            trend_direction = "stable"

    mem["trend_direction"] = trend_direction

    # ── Step 7: Set recommended priority (first-match wins) ──────────────────
    if consecutive_low_days >= CRISIS_WATCH_TRIGGER:
        recommended_priority = "urgent"
    elif pattern_break_detected and sig_valence < LOW_VALENCE_BOUNDARY:
        recommended_priority = "urgent"
    elif sig_withdrawal and consecutive_low_days >= 2:
        recommended_priority = "urgent"
    elif consecutive_low_days == 2:
        recommended_priority = "elevated"
    elif pattern_break_detected and sig_valence >= LOW_VALENCE_BOUNDARY:
        recommended_priority = "elevated"
    elif trend_direction == "declining" and delta_from_baseline > 0.2:
        recommended_priority = "elevated"
    else:
        recommended_priority = "routine"

    # ── Step 8: Emit trend report ─────────────────────────────────────────────
    trend_report = {
        "student_id": student_id,
        "trend_direction": trend_direction,
        "delta_from_baseline": delta_from_baseline,
        "consecutive_low_days": consecutive_low_days,
        "pattern_break_detected": pattern_break_detected,
        "recommended_priority": recommended_priority,
    }

    return trend_report, mem
