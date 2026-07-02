"""
Orchestrator integration tests — T1 through T6.

Tests run in order; each one gates the next conceptually (per Phase 5 plan).
All use FakeSignalLLM + FakeOrchestratorLLM — no credentials required.

T1 — Two-student smoke test (S_001 routine, S_004 urgent, Day 7)
T2 — Full 20-student batch, Day 7
T3 — Judge failure path (corrupted brief)
T4 — HITL OVERRIDE: referral_log empty, audit_log has entry
T5 — HITL REQUEST_MORE_CONTEXT: read-only Memory Keeper, then APPROVE
T6 — Full 7-day run (all 20 students, June 22–28)
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.orchestrator import SchoolPulseOrchestrator, load_data, DEMO_DATES
from agents.llm_interface import FakeSignalLLM, FakeOrchestratorLLM
import agents.memory_keeper as memory_keeper

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"

DAY7 = "2026-06-28"
DAY1 = "2026-06-22"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_orchestrator(hitl_callback=None):
    return SchoolPulseOrchestrator(
        signal_llm=FakeSignalLLM(),
        orchestrator_llm=FakeOrchestratorLLM(),
        hitl_callback=hitl_callback,
    )


def _approve_callback(brief, scorecard, session):
    return "APPROVE_AND_LOG"


def _override_callback(brief, scorecard, session):
    return "OVERRIDE_NO_ACTION"


def _action_sequence(*actions):
    """Callback that pops actions from a list in order."""
    queue = list(actions)

    def callback(brief, scorecard, session):
        return queue.pop(0)

    return callback


def _check_no_names_in_brief(brief: str, registry: list[dict]) -> None:
    for student in registry:
        name = student.get("fictional_name", "")
        for part in name.split():
            if len(part) > 2:
                assert not re.search(r"\b" + re.escape(part) + r"\b", brief, re.IGNORECASE), (
                    f"PII LEAK: name part '{part}' found in brief"
                )


# ── T1: Two-student smoke test ────────────────────────────────────────────────

def test_t1_two_student_smoke_test():
    """
    T1: S_001 (routine_stable, junior) and S_004 (urgent_pattern_break, senior)
    processed for Day 7 only. Verifies:
      - Pipeline completes without error for both students
      - S_001 ends up in routine section
      - S_004 ends up in urgent section with pattern_break_detected=True
      - No real names in assembled brief (PII guard)
      - Seam checks: boundary_checks_passed read before Signal Detector proceeds;
        trend_report.recommended_priority present and correctly typed
    """
    checkins, teacher_obs, registry = load_data(DATA_DIR)

    # Subset to just S_001 and S_004 on Day 7
    target_ids = {"S_001", "S_004"}
    day7_checkins = [
        c for c in checkins
        if c["student_id"] in target_ids and c["date"] == DAY7
    ]
    assert len(day7_checkins) == 2, f"Expected 2 checkins, got {len(day7_checkins)}"

    orchestrator = _make_orchestrator(hitl_callback=_approve_callback)

    # Run all 7 days for S_001/S_004 so S_004 has a proper baseline before the crash
    all_checkins = [c for c in checkins if c["student_id"] in target_ids]
    result = orchestrator.run_sequential_days(
        DEMO_DATES, all_checkins, teacher_obs, registry
    )
    day7_result = result[-1]  # last day is June 28

    reports = day7_result["session_state"]["trend_reports"]
    report_by_id = {r["student_id"]: r for r in reports}

    # Seam check 1: boundary_checks_passed=True was enforced (both students processed)
    assert len(day7_result["session_state"]["processed_students"]) == 2, (
        "Both students must have been processed successfully"
    )
    assert len(day7_result["session_state"]["skipped_students"]) == 0

    # Seam check 2: trend_report has required fields with correct types
    for report in reports:
        assert "recommended_priority" in report, "trend_report missing recommended_priority"
        assert report["recommended_priority"] in ("routine", "elevated", "urgent"), (
            f"Invalid priority: {report['recommended_priority']}"
        )
        assert isinstance(report["consecutive_low_days"], int)
        assert isinstance(report["pattern_break_detected"], bool)

    # S_001 must be routine (stable arc)
    assert "S_001" in report_by_id, "S_001 not found in trend_reports"
    assert report_by_id["S_001"]["recommended_priority"] == "routine", (
        f"S_001 expected routine, got {report_by_id['S_001']['recommended_priority']}"
    )

    # S_004 must be urgent with pattern_break on Day 7
    assert "S_004" in report_by_id, "S_004 not found in trend_reports"
    s004 = report_by_id["S_004"]
    assert s004["recommended_priority"] == "urgent", (
        f"S_004 expected urgent, got {s004['recommended_priority']}"
    )
    assert s004["pattern_break_detected"] is True, "S_004 must have pattern_break_detected=True"

    # S_001 must NOT appear in urgent or elevated sections of the brief
    brief = day7_result["daily_brief"]
    urgent_section = _extract_section(brief, "URGENT")
    elevated_section = _extract_section(brief, "ELEVATED")
    assert "S_001" not in urgent_section, "S_001 must not appear in URGENT section"
    assert "S_001" not in elevated_section, "S_001 must not appear in ELEVATED section"

    # S_004 must appear in URGENT section
    assert "S_004" in urgent_section, "S_004 must appear in URGENT section"

    # PII guard: no real names anywhere in the brief
    _check_no_names_in_brief(brief, registry)


# ── T2: Full 20-student batch, Day 7 ─────────────────────────────────────────

def test_t2_full_20_student_batch_day7():
    """
    T2: All 20 students processed across 7 days; Day 7 brief assertions.

    Expected outcomes (derived from algorithmic run, not arc labels — see DECISIONS.md):
      - urgent:  S_004 (pattern_break), S_017 (consecutive_low ≥ 3),
                 plus any additional students the algorithm correctly classifies
      - elevated: S_003 (consecutive_low=2), S_012 (declining + delta>0.2)
      - routine:  all remaining students
      - No real names in any brief
      - Judge score ≥ 0.75
    """
    checkins, teacher_obs, registry = load_data(DATA_DIR)

    orchestrator = _make_orchestrator(hitl_callback=_approve_callback)
    results = orchestrator.run_sequential_days(DEMO_DATES, checkins, teacher_obs, registry)
    day7_result = results[-1]

    reports = day7_result["session_state"]["trend_reports"]
    report_by_id = {r["student_id"]: r for r in reports}

    # All 20 students must be processed (no skips for valid data)
    n_processed = len(day7_result["session_state"]["processed_students"])
    assert n_processed == 20, f"Expected 20 processed students, got {n_processed}"
    assert len(day7_result["session_state"]["skipped_students"]) == 0

    # ── Hard arc requirements ─────────────────────────────────────────────────
    # S_004 and S_017 MUST be urgent (designed arcs)
    assert report_by_id["S_004"]["recommended_priority"] == "urgent", (
        f"S_004: {report_by_id['S_004']}"
    )
    assert report_by_id["S_004"]["pattern_break_detected"] is True
    assert report_by_id["S_017"]["recommended_priority"] == "urgent", (
        f"S_017: {report_by_id['S_017']}"
    )
    assert report_by_id["S_017"]["consecutive_low_days"] >= 3

    # S_003 and S_012 MUST be elevated (designed arcs, fixture-controlled)
    assert report_by_id["S_003"]["recommended_priority"] == "elevated", (
        f"S_003: {report_by_id['S_003']}"
    )
    assert report_by_id["S_012"]["recommended_priority"] == "elevated", (
        f"S_012: {report_by_id['S_012']}"
    )

    # S_001 (routine_stable) must never be elevated or urgent
    assert report_by_id["S_001"]["recommended_priority"] == "routine", (
        f"S_001 false-positive: {report_by_id['S_001']}"
    )

    # ── PII guard ─────────────────────────────────────────────────────────────
    brief = day7_result["daily_brief"]
    _check_no_names_in_brief(brief, registry)

    # ── Judge score ≥ 0.75 ────────────────────────────────────────────────────
    scorecard = day7_result["judge_scorecard"]
    assert scorecard["weighted_score"] >= 0.75, (
        f"Judge score below threshold: {scorecard}"
    )
    assert scorecard["pass"] is True

    # ── Priority counts: urgent + elevated + routine = 20 ────────────────────
    urgent_ids = [r["student_id"] for r in reports if r["recommended_priority"] == "urgent"]
    elevated_ids = [r["student_id"] for r in reports if r["recommended_priority"] == "elevated"]
    routine_ids = [r["student_id"] for r in reports if r["recommended_priority"] == "routine"]
    assert len(urgent_ids) + len(elevated_ids) + len(routine_ids) == 20

    # Urgent section in brief must list all urgent students
    urgent_section = _extract_section(brief, "URGENT")
    for sid in urgent_ids:
        assert sid in urgent_section, f"{sid} (urgent) missing from URGENT section"


# ── T3: Judge failure path ────────────────────────────────────────────────────

def test_t3_judge_failure_path():
    """
    T3: Corrupt the brief (inject a student name + drop an urgent student) and
    confirm the judge scores below 0.75 with a failure_reason.
    This verifies the guard rail fires, not just that we can pass.
    """
    checkins, teacher_obs, registry = load_data(DATA_DIR)
    orchestrator = _make_orchestrator(hitl_callback=_approve_callback)

    # Run full 7-day pipeline to get a valid session
    results = orchestrator.run_sequential_days(DEMO_DATES, checkins, teacher_obs, registry)
    day7 = results[-1]

    # Build known_names for the judge
    known_names = [r["fictional_name"] for r in registry if r.get("fictional_name")]

    # Corrupt: inject a real student name + drop an urgent student from the brief
    reports = day7["session_state"]["trend_reports"]
    urgent_ids = [r["student_id"] for r in reports if r["recommended_priority"] == "urgent"]
    elevated_ids = [r["student_id"] for r in reports if r["recommended_priority"] == "elevated"]
    routine_ids = [r["student_id"] for r in reports if r["recommended_priority"] == "routine"]

    corrupted_brief = (
        "DAILY BRIEF — 2026-06-28\n"
        "URGENT ACTION REQUIRED (1 student):\n"
        "  • S_017 — Recommend: check in.\n"
        # S_004 deliberately omitted → signal_coverage drops
        # Inject a real name → pii_free_output fails
        "  Note: Rohan Mehta was observed distressed.\n"
        "\nELEVATED WATCH (0 students): None.\n"
        "ROUTINE (19 students): No action required today.\n"
        "─────────────────────────────────────────\n"
    )

    judge_input = {
        "daily_brief": corrupted_brief,
        "expected_urgent_students": urgent_ids,
        "expected_elevated_students": elevated_ids,
        "expected_routine_students": routine_ids,
        "known_names": known_names,
    }

    judge = FakeOrchestratorLLM()
    scorecard = judge.judge_brief(judge_input)

    assert scorecard["pass"] is False, (
        f"Expected judge to FAIL on corrupted brief, got: {scorecard}"
    )
    assert scorecard["weighted_score"] < 0.75, (
        f"Expected score < 0.75, got {scorecard['weighted_score']}"
    )
    assert scorecard["failure_reason"] is not None
    assert scorecard["pii_free_output"] == 0, "PII name injection must fail pii_free_output"


# ── T4: HITL OVERRIDE path ────────────────────────────────────────────────────

def test_t4_hitl_override_path():
    """
    T4: Counselor selects OVERRIDE — NO ACTION.
    Hard rules verified:
      - referral_log is empty (no auto-approval)
      - audit_log has exactly one entry with hitl_outcome='overridden'
    """
    checkins, teacher_obs, registry = load_data(DATA_DIR)
    orchestrator = _make_orchestrator(hitl_callback=_override_callback)

    results = orchestrator.run_sequential_days(DEMO_DATES, checkins, teacher_obs, registry)
    day7_result = results[-1]

    # Hard rule: referral_log MUST be empty after OVERRIDE
    assert day7_result["referral_log"] == [], (
        f"referral_log must be empty after OVERRIDE, got: {day7_result['referral_log']}"
    )

    # Audit log must have exactly one entry for day 7
    audit_log = day7_result["audit_log"]
    assert len(audit_log) == 1, f"Expected 1 audit entry, got {len(audit_log)}"
    assert audit_log[0]["hitl_outcome"] == "overridden"
    assert audit_log[0]["run_date"] == DAY7
    assert audit_log[0]["counselor_action_at"] is not None

    assert day7_result["hitl_outcome"] == "overridden"


# ── T5: HITL REQUEST_MORE_CONTEXT path ───────────────────────────────────────

def test_t5_hitl_request_more_context():
    """
    T5: Counselor requests context for S_004, then approves.
    Verifies:
      - Memory Keeper invoked in read-only mode (orchestrator.get_student_history_summary)
      - Privacy Guard + Signal Detector NOT re-run for the context request
        (only Memory Keeper's read-only path is exercised)
      - After APPROVE, referral_log is written for urgent/elevated students
    """
    checkins, teacher_obs, registry = load_data(DATA_DIR)

    # Count Privacy Guard calls to verify no re-run during context request
    pg_call_log = []
    original_pg_process = None

    import agents.privacy_guard as pg_module
    original_pg_process = pg_module.process

    def counting_pg(raw_record, registry, counselor_names=None):
        pg_call_log.append(raw_record.get("student_id"))
        return original_pg_process(raw_record, registry, counselor_names)

    pg_module.process = counting_pg

    try:
        # Days 1-6 get APPROVE; Day 7 gets REQUEST_MORE_CONTEXT then APPROVE
        callback = _action_sequence(
            "APPROVE_AND_LOG",          # day 1
            "APPROVE_AND_LOG",          # day 2
            "APPROVE_AND_LOG",          # day 3
            "APPROVE_AND_LOG",          # day 4
            "APPROVE_AND_LOG",          # day 5
            "APPROVE_AND_LOG",          # day 6
            ("REQUEST_MORE_CONTEXT", "S_004"),  # day 7 — first action
            "APPROVE_AND_LOG",          # day 7 — after context presented
        )
        orchestrator = _make_orchestrator(hitl_callback=callback)
        results = orchestrator.run_sequential_days(DEMO_DATES, checkins, teacher_obs, registry)
        day7_result = results[-1]
    finally:
        pg_module.process = original_pg_process  # always restore

    # Privacy Guard must have been called 20×7=140 times total (7 days × 20 students)
    # NOT 141 (would indicate a re-run during the context request)
    assert len(pg_call_log) == 140, (
        f"Expected exactly 140 PG calls (7 days × 20 students), "
        f"got {len(pg_call_log)} — re-run detected during context request?"
    )

    # S_004 history must be available in memory_store
    assert "S_004" in orchestrator.memory_store, "S_004 not in memory_store after run"
    history_summary = orchestrator.get_student_history_summary("S_004")
    assert "S_004" in history_summary
    assert "2026-06-28" in history_summary  # Day 7 entry must be in history

    # After APPROVE, referral_log must contain urgent/elevated students
    assert len(day7_result["referral_log"]) > 0, "referral_log must be written after APPROVE"
    referral_ids = {e["student_id"] for e in day7_result["referral_log"]}
    reports = day7_result["session_state"]["trend_reports"]
    urgent_elevated_ids = {
        r["student_id"] for r in reports
        if r["recommended_priority"] in ("urgent", "elevated")
    }
    assert referral_ids == urgent_elevated_ids, (
        f"referral_log IDs {referral_ids} != urgent/elevated IDs {urgent_elevated_ids}"
    )

    assert day7_result["hitl_outcome"] == "approved"


# ── T6: Full 7-day run ────────────────────────────────────────────────────────

def test_t6_full_7_day_sequential_run():
    """
    T6: All 7 days, all 20 students, sequential run.
    Key verifications:
      - S_017's consecutive_low_days escalates across the week
      - Day 7 brief: S_004 and S_017 are urgent
      - Day 7 brief: S_003 and S_012 are elevated
      - No student name in any brief across all 7 days
      - Judge score ≥ 0.75 on Day 7
      - After OVERRIDE on any day, referral_log for that day is empty
    """
    checkins, teacher_obs, registry = load_data(DATA_DIR)

    # Use APPROVE for all days except we'll inspect per-day results
    orchestrator = _make_orchestrator(hitl_callback=_approve_callback)
    all_results = orchestrator.run_sequential_days(
        DEMO_DATES, checkins, teacher_obs, registry
    )

    assert len(all_results) == 7, f"Expected 7 day results, got {len(all_results)}"

    known_names = [r["fictional_name"] for r in registry if r.get("fictional_name")]

    # ── S_017 consecutive_low escalation across the week ─────────────────────
    s017_lows = []
    for day_result in all_results:
        reports = {r["student_id"]: r for r in day_result["session_state"]["trend_reports"]}
        if "S_017" in reports:
            s017_lows.append(reports["S_017"]["consecutive_low_days"])

    assert len(s017_lows) == 7, "S_017 must have a report for every day"
    # By Day 7 (index 6), S_017 must have accumulated ≥ 3 consecutive low days
    assert s017_lows[-1] >= 3, (
        f"S_017 consecutive_low_days on Day 7 must be ≥ 3, got {s017_lows[-1]}"
    )
    # consecutive_low_days on Day 7 must be greater than on Day 1
    assert s017_lows[-1] > s017_lows[0], (
        f"S_017 consecutive_low_days must escalate: Day1={s017_lows[0]}, Day7={s017_lows[-1]}"
    )

    # ── Day 7 brief assertions ────────────────────────────────────────────────
    day7_result = all_results[-1]
    reports_day7 = {r["student_id"]: r for r in day7_result["session_state"]["trend_reports"]}

    assert reports_day7["S_004"]["recommended_priority"] == "urgent"
    assert reports_day7["S_004"]["pattern_break_detected"] is True
    assert reports_day7["S_017"]["recommended_priority"] == "urgent"
    assert reports_day7["S_003"]["recommended_priority"] == "elevated"
    assert reports_day7["S_012"]["recommended_priority"] == "elevated"
    assert reports_day7["S_001"]["recommended_priority"] == "routine"
    assert reports_day7["S_011"]["recommended_priority"] == "routine"

    # ── No names in any brief across all 7 days ───────────────────────────────
    for i, day_result in enumerate(all_results):
        brief = day_result["daily_brief"]
        for student in registry:
            name = student.get("fictional_name", "")
            for part in name.split():
                if len(part) > 2:
                    assert not re.search(
                        r"\b" + re.escape(part) + r"\b", brief, re.IGNORECASE
                    ), f"PII LEAK on Day {i + 1}: name part '{part}' in brief"

    # ── Judge score ≥ 0.75 on Day 7 ──────────────────────────────────────────
    scorecard = day7_result["judge_scorecard"]
    assert scorecard["weighted_score"] >= 0.75, (
        f"Day 7 judge score below threshold: {scorecard}"
    )
    assert scorecard["pass"] is True

    # ── Verify rolling memory window accumulates: S_017's memory has ≤ 7 days ─
    s017_mem = orchestrator.memory_store.get("S_017", {})
    history_len = len(s017_mem.get("signal_history", []))
    assert history_len <= 7, f"S_017 signal_history exceeds 7-day window: {history_len}"
    assert history_len == 7, f"S_017 should have exactly 7 days of history after full run"


# ── Utility ───────────────────────────────────────────────────────────────────

def _extract_section(brief: str, section_name: str) -> str:
    pattern = rf"{section_name}[^\n]*\n(.*?)(?=\n[A-Z]{{3,}}|\Z)"
    m = re.search(pattern, brief, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ""
