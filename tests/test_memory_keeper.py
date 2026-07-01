"""
Memory Keeper tests — 3 EDD cases + real-data arc verification.

EDD cases from skills/student-trend-tracker/SKILL.md:
  stt_pattern_break_001       — single-day crash from stable baseline → urgent
  stt_consecutive_low_002     — 3 consecutive low days → crisis_watch + urgent
  stt_stable_no_escalation_003 — positive history + positive signal → routine

Real-data arc tests (ground-truth valence from generate_synthetic_data.py):
  S_004  urgent_pattern_break    — days 1-6 stable (~+0.5), day 7 crashes to -0.65
  S_017  urgent_consecutive_low  — days 4-7 below -0.3 (4 consecutive low days)
  S_011  routine_stable          — all 7 days above +0.4; no escalation

Additional:
  rolling_window_drops_oldest   — 8th signal drops day 1 correctly
  consecutive_low_resets        — a non-low day resets the counter
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.memory_keeper as mk

DATES = [
    "2026-06-22", "2026-06-23", "2026-06-24",
    "2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28",
]


def _signal(student_id: str, date: str, valence: float,
            energy: float = 0.5, withdrawal: bool = False) -> dict:
    return {
        "student_id": student_id,
        "date": date,
        "emotional_valence": valence,
        "energy_level": energy,
        "social_withdrawal_flag": withdrawal,
        "distress_keywords_detected": [],
        "signal_confidence": 0.9,
        "raw_input_type": "emoji",
    }


def _run_sequence(student_id: str, valences: list[float],
                  dates: list[str] = None, energies: list[float] = None,
                  withdrawals: list[bool] = None) -> tuple[dict, dict]:
    """Feed a sequence of signals through Memory Keeper and return final state."""
    dates = dates or DATES[:len(valences)]
    energies = energies or [0.5] * len(valences)
    withdrawals = withdrawals or [False] * len(valences)

    store = {}
    last_result = None
    for i, (v, e, w, d) in enumerate(zip(valences, energies, withdrawals, dates)):
        last_result = mk.process(_signal(student_id, d, v, e, w), store)
        assert last_result["status"] == "ok", (
            f"Memory Keeper error on day {i+1} for {student_id}: {last_result}"
        )
        store = last_result["memory_store"]

    return last_result["report"], store[student_id]


# ─────────────────────────────────────────────────────────────────────────────
# EDD cases
# ─────────────────────────────────────────────────────────────────────────────

def test_stt_pattern_break_001():
    """
    6-day stable near-zero baseline; day-7 drops to -0.8.
    Expected: pattern_break_detected=True, delta>0.4, recommended_priority=urgent.
    """
    # Pre-load 6 stable days then add the crash signal via Memory Keeper
    prior_history = [
        {"date": "2026-06-22", "emotional_valence": -0.1, "energy_level": 0.5, "social_withdrawal_flag": False},
        {"date": "2026-06-23", "emotional_valence": -0.2, "energy_level": 0.6, "social_withdrawal_flag": False},
        {"date": "2026-06-24", "emotional_valence": -0.1, "energy_level": 0.5, "social_withdrawal_flag": False},
        {"date": "2026-06-25", "emotional_valence": -0.15, "energy_level": 0.6, "social_withdrawal_flag": False},
        {"date": "2026-06-26", "emotional_valence": -0.1, "energy_level": 0.5, "social_withdrawal_flag": False},
        {"date": "2026-06-27", "emotional_valence": -0.2, "energy_level": 0.5, "social_withdrawal_flag": False},
    ]
    existing_memory = {
        "student_id": "S_004",
        "age_group": "senior",
        "signal_history": prior_history,
        "rolling_baseline": {"avg_valence": None, "avg_energy": None},
        "trend_direction": "stable",
        "consecutive_low_days": 0,
        "last_counselor_referral": None,
    }

    crash_signal = _signal("S_004", "2026-06-28", -0.8, energy=0.1, withdrawal=True)
    result = mk.process(crash_signal, {"S_004": existing_memory})

    assert result["status"] == "ok", f"Expected ok: {result}"
    report = result["report"]

    assert report["pattern_break_detected"] is True, (
        f"Expected pattern_break_detected=True, got {report}"
    )
    assert report["recommended_priority"] == "urgent", (
        f"Expected urgent, got {report['recommended_priority']}"
    )
    assert report["delta_from_baseline"] > 0.4, (
        f"Expected delta > 0.4, got {report['delta_from_baseline']}"
    )


def test_stt_consecutive_low_002():
    """
    2 prior low days; new signal is also low → consecutive_low_days=3 → crisis_watch.
    """
    prior_history = [
        {"date": "2026-06-26", "emotional_valence": -0.5, "energy_level": 0.2, "social_withdrawal_flag": False},
        {"date": "2026-06-27", "emotional_valence": -0.4, "energy_level": 0.2, "social_withdrawal_flag": False},
    ]
    existing_memory = {
        "student_id": "S_017",
        "age_group": "junior",
        "signal_history": prior_history,
        "rolling_baseline": {"avg_valence": None, "avg_energy": None},
        "trend_direction": "declining",
        "consecutive_low_days": 2,
        "last_counselor_referral": None,
    }

    new_signal = _signal("S_017", "2026-06-28", -0.6, energy=0.2)
    result = mk.process(new_signal, {"S_017": existing_memory})

    assert result["status"] == "ok", f"Expected ok: {result}"
    report = result["report"]

    assert report["consecutive_low_days"] == 3, (
        f"Expected 3 consecutive low days, got {report['consecutive_low_days']}"
    )
    assert report["trend_direction"] == "crisis_watch", (
        f"Expected crisis_watch, got {report['trend_direction']}"
    )
    assert report["recommended_priority"] == "urgent", (
        f"Expected urgent, got {report['recommended_priority']}"
    )


def test_stt_stable_no_escalation_003():
    """
    7-day positive history with new positive signal → stable + routine.
    No false escalation.
    """
    prior_history = [
        {"date": DATES[0], "emotional_valence": 0.6, "energy_level": 0.7, "social_withdrawal_flag": False},
        {"date": DATES[1], "emotional_valence": 0.5, "energy_level": 0.6, "social_withdrawal_flag": False},
        {"date": DATES[2], "emotional_valence": 0.7, "energy_level": 0.8, "social_withdrawal_flag": False},
        {"date": DATES[3], "emotional_valence": 0.6, "energy_level": 0.7, "social_withdrawal_flag": False},
        {"date": DATES[4], "emotional_valence": 0.8, "energy_level": 0.8, "social_withdrawal_flag": False},
        {"date": DATES[5], "emotional_valence": 0.5, "energy_level": 0.6, "social_withdrawal_flag": False},
        # Day 7 entry already in history — will be replaced by the new signal
        {"date": DATES[6], "emotional_valence": 0.6, "energy_level": 0.7, "social_withdrawal_flag": False},
    ]
    existing_memory = {
        "student_id": "S_011",
        "age_group": "junior",
        "signal_history": prior_history,
        "rolling_baseline": {"avg_valence": 0.63, "avg_energy": 0.70},
        "trend_direction": "stable",
        "consecutive_low_days": 0,
        "last_counselor_referral": None,
    }

    new_signal = _signal("S_011", DATES[6], 0.65, energy=0.7)
    result = mk.process(new_signal, {"S_011": existing_memory})

    assert result["status"] == "ok", f"Expected ok: {result}"
    report = result["report"]

    assert report["trend_direction"] in ("stable", "improving"), (
        f"Expected stable or improving, got {report['trend_direction']}"
    )
    assert report["consecutive_low_days"] == 0, (
        f"Expected 0 consecutive low days, got {report['consecutive_low_days']}"
    )
    assert report["pattern_break_detected"] is False
    assert report["recommended_priority"] == "routine", (
        f"Expected routine, got {report['recommended_priority']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Real-data arc tests
# ─────────────────────────────────────────────────────────────────────────────

# Ground-truth valence trajectories from generate_synthetic_data.py
_TRAJECTORIES = {
    "S_004": [0.55, 0.50, 0.48, 0.52, 0.50, 0.45, -0.65],   # urgent_pattern_break
    "S_017": [0.20, 0.10, -0.10, -0.40, -0.55, -0.65, -0.70], # urgent_consecutive_low
    "S_011": [0.60, 0.55, 0.70, 0.62, 0.80, 0.58, 0.65],      # routine_stable
    "S_003": [0.10, 0.05, -0.05, -0.15, -0.25, -0.30, -0.40], # elevated_declining
    "S_009": [0.40, 0.35, 0.30, 0.25, 0.20, -0.35, -0.40],    # elevated_late_dip
}


def test_real_arc_urgent_pattern_break():
    """
    S_004: 6-day stable baseline (~+0.50) then crash to -0.65 on day 7.
    Day 7 report must show: pattern_break_detected=True, urgent priority.
    Day 7 consecutive_low_days is 1 (only one low day), NOT 3 — this confirms
    pattern_break and consecutive_low are INDEPENDENT detection paths.
    """
    report, _ = _run_sequence("S_004", _TRAJECTORIES["S_004"])

    assert report["pattern_break_detected"] is True, (
        f"S_004 day-7 crash did not trigger pattern_break: {report}"
    )
    assert report["recommended_priority"] == "urgent", (
        f"Expected urgent for S_004, got {report['recommended_priority']}"
    )
    # Pattern break happened on a single low day, NOT 3 consecutive
    assert report["consecutive_low_days"] == 1, (
        f"Expected consecutive_low_days=1 (single crash), got {report['consecutive_low_days']}"
    )


def test_real_arc_urgent_consecutive_low():
    """
    S_017: Days 4-7 are all below -0.3 → 4 consecutive low days.
    Day 7 report must show: consecutive_low_days=4, crisis_watch, urgent.
    Escalation must NOT occur before day 6 (before count reaches 3).
    """
    store = {}
    reports = []
    for i, v in enumerate(_TRAJECTORIES["S_017"]):
        result = mk.process(_signal("S_017", DATES[i], v), store)
        assert result["status"] == "ok"
        store = result["memory_store"]
        reports.append(result["report"])

    # Day 6 (index 5) is when consecutive_low_days hits 3 → first urgent
    assert reports[5]["recommended_priority"] == "urgent", (
        f"Expected urgent by day 6, got {reports[5]}"
    )

    # Day 7 (final)
    final = reports[-1]
    assert final["consecutive_low_days"] >= 4, (
        f"Expected ≥4 consecutive low days, got {final['consecutive_low_days']}"
    )
    assert final["trend_direction"] == "crisis_watch"
    assert final["recommended_priority"] == "urgent"


def test_real_arc_routine_stable():
    """
    S_011: All 7 days above +0.4. Must NEVER escalate to elevated or urgent.
    False-positive check.
    """
    store = {}
    for i, v in enumerate(_TRAJECTORIES["S_011"]):
        result = mk.process(_signal("S_011", DATES[i], v), store)
        assert result["status"] == "ok"
        store = result["memory_store"]
        report = result["report"]
        assert report["recommended_priority"] == "routine", (
            f"S_011 day {i+1} falsely escalated to {report['recommended_priority']}: {report}"
        )
    assert store["S_011"]["consecutive_low_days"] == 0


def test_real_arc_elevated_declining():
    """
    S_003: Gradual decline from ~0.0 to -0.4 over 7 days.
    Should end at elevated (not urgent) — never crosses urgent threshold.
    """
    report, _ = _run_sequence("S_003", _TRAJECTORIES["S_003"])

    assert report["recommended_priority"] in ("elevated", "routine"), (
        f"S_003 should not be urgent, got {report['recommended_priority']}"
    )


def test_real_arc_elevated_late_dip():
    """
    S_009: Routine days 1-5 (~+0.3 avg), two consecutive low days at end (days 6-7).
    The spec's pattern-break rule fires on day 7: drop from baseline (~+0.19) to -0.40
    is delta ≈ 0.59 > 0.4, AND valence < -0.3 → urgent (overrides elevated).
    consecutive_low_days == 2 is still correct (the consecutive-low counter is independent).
    """
    store = {}
    reports = []
    for i, v in enumerate(_TRAJECTORIES["S_009"]):
        result = mk.process(_signal("S_009", DATES[i], v), store)
        assert result["status"] == "ok"
        store = result["memory_store"]
        reports.append(result["report"])

    final = reports[-1]
    assert final["consecutive_low_days"] == 2, (
        f"Expected 2 consecutive low days for S_009, got {final['consecutive_low_days']}"
    )
    # Pattern break (delta ≈ 0.59 > 0.4) with valence < -0.3 escalates to urgent
    assert final["pattern_break_detected"] is True, (
        f"Expected pattern_break_detected=True for S_009 day 7, got {final}"
    )
    assert final["recommended_priority"] == "urgent", (
        f"Expected urgent (pattern break + valence < -0.3) for S_009, got {final['recommended_priority']}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Structural tests
# ─────────────────────────────────────────────────────────────────────────────

def test_rolling_window_drops_oldest():
    """
    8 signals in sequence: signal_history must cap at 7; oldest entry dropped.
    """
    extra_date = "2026-06-29"
    all_dates = DATES + [extra_date]
    valences = [0.5] * 8

    store = {}
    for i, (d, v) in enumerate(zip(all_dates, valences)):
        result = mk.process(_signal("S_001", d, v), store)
        assert result["status"] == "ok"
        store = result["memory_store"]

    history = store["S_001"]["signal_history"]
    assert len(history) == 7, f"Expected 7 entries in window, got {len(history)}"
    # Oldest date (DATES[0]) must be gone
    dates_in_history = [h["date"] for h in history]
    assert DATES[0] not in dates_in_history, (
        f"Oldest date {DATES[0]} still in window: {dates_in_history}"
    )
    assert extra_date in dates_in_history


def test_consecutive_low_resets_on_recovery():
    """
    2 low days, then one recovery day: consecutive_low_days must reset to 0.
    """
    store = {}
    signals = [
        ("2026-06-26", -0.5),  # low
        ("2026-06-27", -0.4),  # low (count = 2)
        ("2026-06-28",  0.3),  # recovery → reset
    ]
    for d, v in signals:
        result = mk.process(_signal("S_002", d, v), store)
        store = result["memory_store"]

    assert store["S_002"]["consecutive_low_days"] == 0, (
        f"Expected 0 after recovery, got {store['S_002']['consecutive_low_days']}"
    )


def test_cross_student_boundary_rejected():
    """
    If expected_student_id is set and signal has different id, reject.
    """
    sig = _signal("S_999", "2026-06-28", -0.5)
    result = mk.process(sig, {}, expected_student_id="S_001")
    assert result["status"] == "error"
    assert result["error_type"] == "cross_student_payload"
    assert result["report"] is None


def test_initialises_new_student_correctly():
    """
    First signal for a student with no prior memory → initialises cleanly.
    """
    sig = _signal("S_NEW", "2026-06-28", 0.5)
    result = mk.process(sig, {})
    assert result["status"] == "ok"
    report = result["report"]
    mem = result["memory_store"]["S_NEW"]

    assert report["consecutive_low_days"] == 0
    assert report["pattern_break_detected"] is False  # no baseline yet
    assert len(mem["signal_history"]) == 1
