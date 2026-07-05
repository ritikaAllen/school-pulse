"""
SchoolPulse Orchestrator

Coordinates the full pipeline run. Owns session state, enforces pipeline
ordering, assembles the Daily Brief from trend reports, invokes the
LLM-as-judge evaluation layer, and gates output behind the HITL check.

Responsibility boundary (enforced at code review):
  - Does NOT implement parsing, trend computation, or sanitisation logic.
  - Calls, receives, routes, and assembles. All logic lives in sub-agents/skills.

LLM seams (Option B — see DECISIONS.md):
  signal_llm       — injected into Signal Detector (text path)
  orchestrator_llm — used for recommended_action generation and judge evaluation

HITL gate:
  hitl_callback(brief_text, scorecard, session_state) → action
  action is one of:
    "APPROVE_AND_LOG"
    "OVERRIDE_NO_ACTION"
    ("REQUEST_MORE_CONTEXT", student_id)
  Pass None to use CLI input() for demo mode.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.privacy_guard as privacy_guard
import agents.signal_detector as signal_detector
import agents.memory_keeper as memory_keeper
from agents.llm_interface import FakeSignalLLM, FakeOrchestratorLLM


# ── Date range for the demo dataset ───────────────────────────────────────────
DEMO_DATES = [
    "2026-06-22", "2026-06-23", "2026-06-24",
    "2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28",
]


class SchoolPulseOrchestrator:
    """
    Coordinates the full mental-health-first-responder pipeline.

    memory_store persists across run_batch() calls so that multi-day runs
    accumulate student history correctly (used in T6 sequential run).
    """

    def __init__(
        self,
        signal_llm=None,
        orchestrator_llm=None,
        hitl_callback: Optional[Callable] = None,
        log_dir: Optional[Path] = None,
    ):
        self._signal_llm = signal_llm or FakeSignalLLM()
        self._orchestrator_llm = orchestrator_llm or FakeOrchestratorLLM()
        self._hitl_callback = hitl_callback
        self._log_dir = log_dir

        # Persistent across run_batch() calls
        self.memory_store: dict = {}

        # Last completed session (overwritten each run_batch)
        self.session_state: dict = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def run_batch(
        self,
        run_date: str,
        checkins: list[dict],
        teacher_observations: list[dict],
        student_registry: list[dict],
    ) -> dict:
        """
        Run the full pipeline for one day's batch.

        Args:
            run_date:             ISO8601 date string (e.g. "2026-06-28").
            checkins:             Raw check-in records for this date.
            teacher_observations: All teacher observations (any date); filtered by date internally.
            student_registry:     List of {student_id, fictional_name, age_group, ...}.

        Returns:
            Result dict with keys: session_state, daily_brief, judge_scorecard,
            hitl_outcome, referral_log, audit_log, skipped_students.
        """
        self.session_state = {
            "run_date": run_date,
            "student_id_registry": {
                r["fictional_name"]: r["student_id"]
                for r in student_registry
                if r.get("fictional_name")
            },
            "processed_students": [],
            "skipped_students": [],
            "trend_reports": [],
            "daily_brief": "",
            "judge_scorecard": {},
            "hitl_outcome": "pending",
            "referral_log": [],
            "audit_log": [],
        }
        self._action_cache: dict = {}  # student_id -> recommended_action; avoids duplicate LLM calls
        self._signal_cache: dict = {}  # student_id -> signal_dict; populated by batch prefetch

        # Build student_id → obs lookup for this date
        day_obs = {
            o["student_id"]: o
            for o in teacher_observations
            if o.get("date") == run_date
        }

        # ── Phase 1: Privacy Guard (all students) ─────────────────────────────
        sanitised_records: list[dict] = []
        for checkin in checkins:
            sid = checkin.get("student_id", "")
            sanitised = self._sanitise_checkin(checkin, day_obs.get(sid), student_registry)
            if sanitised is not None:
                sanitised_records.append(sanitised)

        # ── Phase 2: Batch LLM call — senior text signals (1 call vs N) ───────
        self._signal_cache = self._batch_prefetch_signals(sanitised_records)

        # ── Phase 3: Signal Detector + Memory Keeper (per student) ────────────
        for sanitised in sanitised_records:
            self._detect_and_track(sanitised)

        # ── Step 6: Assemble Daily Brief ──────────────────────────────────────
        known_names = [
            r["fictional_name"] for r in student_registry if r.get("fictional_name")
        ]
        brief = self._assemble_brief(run_date, known_names)
        self.session_state["daily_brief"] = brief

        # ── Step 7: LLM-as-judge ──────────────────────────────────────────────
        scorecard = self._run_judge(brief, known_names)
        self.session_state["judge_scorecard"] = scorecard

        # ── Steps 8–10: HITL gate ─────────────────────────────────────────────
        hitl_outcome = self._present_hitl_gate(brief, scorecard)
        self.session_state["hitl_outcome"] = hitl_outcome

        result = {
            "session_state": dict(self.session_state),
            "daily_brief": brief,
            "judge_scorecard": scorecard,
            "hitl_outcome": hitl_outcome,
            "referral_log": list(self.session_state["referral_log"]),
            "audit_log": list(self.session_state["audit_log"]),
            "skipped_students": list(self.session_state["skipped_students"]),
        }

        if self._log_dir:
            self._write_logs(result)

        return result

    def run_sequential_days(
        self,
        dates: list[str],
        all_checkins: list[dict],
        teacher_observations: list[dict],
        student_registry: list[dict],
    ) -> list[dict]:
        """
        Run pipeline for multiple days in sequence.
        memory_store accumulates across days (T6 use case).
        """
        results = []
        for date in dates:
            day_checkins = [c for c in all_checkins if c.get("date") == date]
            result = self.run_batch(date, day_checkins, teacher_observations, student_registry)
            results.append(result)
        return results

    def get_student_history_summary(self, student_id: str) -> str:
        """Return a formatted 7-day history string for HITL REQUEST_MORE_CONTEXT."""
        if student_id not in self.memory_store:
            return f"No history found for {student_id}."
        mem = self.memory_store[student_id]
        history = mem.get("signal_history", [])
        lines = [f"7-day signal history for {student_id}:"]
        for entry in history:
            lines.append(
                f"  {entry['date']}: valence={entry['emotional_valence']:.2f} "
                f"energy={entry['energy_level']:.2f} "
                f"withdrawal={entry['social_withdrawal_flag']}"
            )
        lines.append(f"  consecutive_low_days: {mem.get('consecutive_low_days', 0)}")
        lines.append(f"  trend_direction: {mem.get('trend_direction', 'stable')}")
        return "\n".join(lines)

    # ── Private: pipeline phases ───────────────────────────────────────────────

    def _sanitise_checkin(
        self,
        raw_checkin: dict,
        teacher_obs: Optional[dict],
        student_registry: list[dict],
    ) -> Optional[dict]:
        """Phase 1: run Privacy Guard. Returns sanitised record or None (skipped)."""
        student_id = raw_checkin.get("student_id", "unknown")
        record = dict(raw_checkin)
        if teacher_obs:
            record["teacher_observation"] = {
                "note": teacher_obs.get("note", ""),
                "flag_level": teacher_obs.get("flag_level", "none"),
            }
        pg_result = privacy_guard.process(record, student_registry)
        if pg_result["status"] != "ok":
            self.session_state["skipped_students"].append({
                "student_id": student_id,
                "reason": pg_result.get("error_type", "unknown"),
                "message": pg_result.get("message", ""),
            })
            return None
        sanitised = pg_result["record"]
        if not sanitised.get("sanitisation_manifest", {}).get("boundary_checks_passed", False):
            self.session_state["skipped_students"].append({
                "student_id": student_id,
                "reason": "boundary_violation",
                "message": "boundary_checks_passed=False from Privacy Guard",
            })
            return None
        return sanitised

    def _batch_prefetch_signals(self, sanitised_records: list[dict]) -> dict:
        """
        Phase 2: one LLM call for all senior text responses.
        Returns {student_id: signal_dict}. Empty dict if batch not supported (Fake path).
        """
        if not hasattr(self._signal_llm, "batch_parse"):
            return {}
        senior_records = [
            {
                "student_id": r.get("student_id"),
                "date": r.get("date"),
                "response": (r.get("senior_input") or {}).get("response", ""),
            }
            for r in sanitised_records
            if (r.get("senior_input") or {}).get("response")
        ]
        if not senior_records:
            return {}
        return self._signal_llm.batch_parse(senior_records)

    def _detect_and_track(self, sanitised: dict) -> None:
        """Phase 3: Signal Detector + Memory Keeper for one already-sanitised record."""
        student_id = sanitised.get("student_id", "unknown")

        sd_result = signal_detector.process(
            sanitised,
            llm_client=self._signal_llm,
            text_signal_cache=self._signal_cache,
        )
        if sd_result["status"] != "ok":
            self.session_state["skipped_students"].append({
                "student_id": student_id,
                "reason": sd_result.get("error_type", "signal_failure"),
                "message": sd_result.get("message", ""),
            })
            return

        signal = sd_result["signal"]

        mk_result = memory_keeper.process(
            signal,
            memory_store=self.memory_store,
            expected_student_id=student_id,
        )
        if mk_result["status"] != "ok":
            self.session_state["skipped_students"].append({
                "student_id": student_id,
                "reason": mk_result.get("error_type", "memory_failure"),
                "message": mk_result.get("message", ""),
            })
            return

        self.memory_store = mk_result["memory_store"]
        self.session_state["trend_reports"].append(mk_result["report"])
        self.session_state["processed_students"].append(student_id)

    # ── Private: Daily Brief assembly ─────────────────────────────────────────

    def _assemble_brief(self, run_date: str, known_names: list[str]) -> str:
        reports = self.session_state["trend_reports"]

        urgent = [r for r in reports if r["recommended_priority"] == "urgent"]
        elevated = [r for r in reports if r["recommended_priority"] == "elevated"]
        routine = [r for r in reports if r["recommended_priority"] == "routine"]

        lines = [
            f"DAILY BRIEF — {run_date}",
            "Generated by: Mental Health First Responder Agent",
            "",
        ]

        # URGENT section
        lines.append(f"URGENT ACTION REQUIRED ({len(urgent)} student(s)):")
        if urgent:
            for r in urgent:
                action = self._orchestrator_llm.generate_recommended_action(
                    r["student_id"], r
                )
                self._action_cache[r["student_id"]] = action
                detail = []
                if r.get("pattern_break_detected"):
                    detail.append("Pattern break detected.")
                if r.get("consecutive_low_days", 0) >= 1:
                    detail.append(f"{r['consecutive_low_days']} consecutive low-mood day(s).")
                detail_str = " ".join(detail)
                lines.append(
                    f"  • {r['student_id']} — {detail_str}"
                    f"\n    Recommend: {action}"
                )
        else:
            lines.append("  None.")
        lines.append("")

        # ELEVATED section
        lines.append(f"ELEVATED WATCH ({len(elevated)} student(s)):")
        if elevated:
            for r in elevated:
                action = self._elevated_action(r)
                self._action_cache[r["student_id"]] = action  # reused in _write_referrals
                delta = r.get("delta_from_baseline", 0.0)
                lines.append(
                    f"  • {r['student_id']} — "
                    f"Trend: {r['trend_direction']}. "
                    f"Delta from baseline: {delta:.2f}. Monitor."
                )
        else:
            lines.append("  None.")
        lines.append("")

        # ROUTINE section
        lines.append(f"ROUTINE ({len(routine)} student(s)): No action required today.")
        lines.append("")
        lines.append("─" * 41)

        brief = "\n".join(lines)

        # PII guard: fail loudly if any known name leaked into the brief
        # Uses word-boundary regex (same as sanitizer) so "Lin" ≠ "declining".
        for name in known_names:
            for part in name.split():
                if len(part) > 2 and re.search(
                    r"\b" + re.escape(part) + r"\b", brief, re.IGNORECASE
                ):
                    raise ValueError(
                        f"PII LEAK: name part '{part}' found in assembled Daily Brief — "
                        "check recommended_action generation and section content."
                    )

        return brief

    def _elevated_action(self, report: dict) -> str:
        """Rule-based referral text for elevated students — no LLM call required."""
        n = report.get("consecutive_low_days", 0)
        delta = abs(report.get("delta_from_baseline", 0.0))
        if n >= 3:
            return f"Schedule a well-being check-in this week. {n} consecutive low-mood days observed."
        if delta >= 0.3:
            return f"Monitor closely. Dip of {delta:.2f} from baseline detected. Consider an informal check-in."
        return "Continue monitoring. Follow up if trend persists or worsens."

    # ── Private: LLM-as-judge ─────────────────────────────────────────────────

    def _run_judge(self, brief: str, known_names: list[str]) -> dict:
        reports = self.session_state["trend_reports"]
        judge_input = {
            "daily_brief": brief,
            "expected_urgent_students": [
                r["student_id"] for r in reports if r["recommended_priority"] == "urgent"
            ],
            "expected_elevated_students": [
                r["student_id"] for r in reports if r["recommended_priority"] == "elevated"
            ],
            "expected_routine_students": [
                r["student_id"] for r in reports if r["recommended_priority"] == "routine"
            ],
            "known_names": known_names,
        }
        return self._orchestrator_llm.judge_brief(judge_input)

    # ── Private: HITL gate ────────────────────────────────────────────────────

    def _present_hitl_gate(self, brief: str, scorecard: dict) -> str:
        """
        Present brief to counselor and process their action.
        Hard rule: referral_log is NEVER written without APPROVE_AND_LOG.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            if self._hitl_callback is not None:
                action = self._hitl_callback(brief, scorecard, self.session_state)
            else:
                action = _cli_prompt(brief, scorecard)

            if action == "APPROVE_AND_LOG":
                self._write_referrals(now_iso)
                self._write_audit(now_iso, "approved")
                return "approved"

            elif action == "OVERRIDE_NO_ACTION":
                self._write_audit(now_iso, "overridden")
                return "overridden"

            elif isinstance(action, tuple) and action[0] == "REQUEST_MORE_CONTEXT":
                student_id = action[1] if len(action) > 1 else ""
                # Re-invoke Memory Keeper read-only — no full pipeline re-run
                context = self.get_student_history_summary(student_id)
                if self._hitl_callback is None:
                    print(f"\n--- Additional context for {student_id} ---\n{context}\n")
                # Loop: counselor must still select APPROVE or OVERRIDE
                continue

            else:
                # Unrecognised action: treat as pending (no referrals)
                self._write_audit(now_iso, "pending")
                return "pending"

    def _write_referrals(self, approved_at: str) -> None:
        reports = self.session_state["trend_reports"]
        for r in reports:
            if r["recommended_priority"] in ("urgent", "elevated"):
                entry = {
                    "student_id": r["student_id"],
                    "date": self.session_state["run_date"],
                    "priority": r["recommended_priority"],
                    "recommended_action": (
                        self._action_cache.get(r["student_id"])
                        or self._orchestrator_llm.generate_recommended_action(r["student_id"], r)
                    ),
                    "counselor_approved_at": approved_at,
                }
                self.session_state["referral_log"].append(entry)

    def _write_audit(self, action_at: str, outcome: str) -> None:
        reports = self.session_state["trend_reports"]
        entry = {
            "run_date": self.session_state["run_date"],
            "students_processed": len(self.session_state["processed_students"]),
            "students_skipped": len(self.session_state["skipped_students"]),
            "urgent_count": sum(1 for r in reports if r["recommended_priority"] == "urgent"),
            "elevated_count": sum(1 for r in reports if r["recommended_priority"] == "elevated"),
            "routine_count": sum(1 for r in reports if r["recommended_priority"] == "routine"),
            "judge_score": self.session_state["judge_scorecard"].get("weighted_score", 0.0),
            "hitl_outcome": outcome,
            "referrals_logged": len(self.session_state["referral_log"]),
            "counselor_action_at": action_at,
        }
        self.session_state["audit_log"].append(entry)

    def _write_logs(self, result: dict) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        referral_path = self._log_dir / "referral_log.json"
        audit_path = self._log_dir / "audit_log.json"

        existing_referrals = json.loads(referral_path.read_text()) if referral_path.exists() else []
        existing_audit = json.loads(audit_path.read_text()) if audit_path.exists() else []

        existing_referrals.extend(result["referral_log"])
        existing_audit.extend(result["audit_log"])

        referral_path.write_text(json.dumps(existing_referrals, indent=2))
        audit_path.write_text(json.dumps(existing_audit, indent=2))


# ── CLI HITL prompt (demo mode) ───────────────────────────────────────────────

def _cli_prompt(brief: str, scorecard: dict) -> str | tuple:
    print("\n" + "=" * 60)
    print(brief)
    score = scorecard.get("weighted_score", 0.0)
    passed = scorecard.get("pass", False)
    status = "PASS" if passed else "FAIL"
    print(f"\nEvaluation Score: {score:.2f} / 1.0  [{status}]")
    if not passed:
        print(f"Quality concern: {scorecard.get('failure_reason', 'unknown')}")
    print("=" * 60)
    print("\nOptions:")
    print("  1. APPROVE AND LOG")
    print("  2. REQUEST MORE CONTEXT")
    print("  3. OVERRIDE — NO ACTION")
    choice = input("\nEnter choice (1/2/3): ").strip()
    if choice == "1":
        return "APPROVE_AND_LOG"
    elif choice == "2":
        student_id = input("Enter student ID for context: ").strip()
        return ("REQUEST_MORE_CONTEXT", student_id)
    elif choice == "3":
        return "OVERRIDE_NO_ACTION"
    else:
        print("Unrecognised input — defaulting to OVERRIDE.")
        return "OVERRIDE_NO_ACTION"


# ── Convenience loader (used by tests and notebook) ───────────────────────────

def load_data(data_dir: Path) -> tuple[list, list, list]:
    """Load synthetic data files. Returns (checkins, teacher_observations, registry)."""
    with open(data_dir / "synthetic_checkins.json", encoding="utf-8") as f:
        checkins = json.load(f)
    with open(data_dir / "teacher_observations.json", encoding="utf-8") as f:
        teacher_observations = json.load(f)
    with open(data_dir / "student_registry.json", encoding="utf-8") as f:
        registry = json.load(f)
    return checkins, teacher_observations, registry
