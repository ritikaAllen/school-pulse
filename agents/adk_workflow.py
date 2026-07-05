"""
SchoolPulse — ADK Workflow Integration

Wraps the SchoolPulse multi-agent pipeline as a Google ADK 2.x Workflow graph.
Each pipeline stage maps to an ADK node:

  START
    ↓  privacy_guard      FunctionNode — Zero Ambient Authority: PII masking + NER redaction
    ↓  signal_and_memory  FunctionNode — 1 batch LLM call (seniors) + 7-day Memory Keeper
    ↓  brief_and_judge    FunctionNode — Daily Brief assembly + 5-criterion LLM-as-judge
    ↓  schoolpulse_hitl   LlmAgent    — HITL gate: APPROVE_AND_LOG / OVERRIDE / REQUEST_MORE_CONTEXT

Design notes:
  FunctionNodes use zero-argument closures bound to a shared _PipelineCtx object.
  All pipeline state (sanitized records, trend reports, brief text) lives on the
  ctx object rather than going through ADK session state — keeping SchoolPulse
  logic in Python and ADK's Workflow graph as the orchestration skeleton.

  The HITL node (schoolpulse_hitl LlmAgent) is wired into the graph to show the
  full pipeline. It requires a live API key and an interactive ADK Runner session;
  use build_workflow() + Runner for that path.

Usage (notebook / demo):
    from agents.adk_workflow import build_workflow, run_one_day_via_adk

    ctx = run_one_day_via_adk(all_checkins, teacher_obs, registry, date="2026-06-28")
    print(ctx.daily_brief)
    print(ctx.judge_scorecard)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.adk.workflow import Workflow, Edge, FunctionNode, START
from google.adk.agents import LlmAgent

from agents.orchestrator import SchoolPulseOrchestrator
from agents.llm_interface import (
    FakeSignalLLM, FakeOrchestratorLLM,
    RealSignalLLM, RealOrchestratorLLM,
)

MODEL = "gemini-3.1-flash-lite"


# ── Shared pipeline context ────────────────────────────────────────────────────

class _PipelineCtx:
    """Mutable context threaded through ADK FunctionNode closures for one day's run."""

    def __init__(
        self,
        orch: SchoolPulseOrchestrator,
        run_date: str,
        day_checkins: list,
        teacher_obs: list,
        registry: list,
    ):
        self.orch = orch
        self.run_date = run_date
        self.day_checkins = day_checkins
        self.teacher_obs = teacher_obs
        self.registry = registry
        # Populated as nodes execute
        self.sanitized_records: list = []
        self.trend_reports: list = []
        self.daily_brief: str = ""
        self.judge_scorecard: dict = {}


# ── FunctionNode closures (zero-arg — state flows through _PipelineCtx) ────────
#
# FunctionNode(parameter_binding='state') pulls individual named parameters
# from ctx.state by keyword. By using zero-argument closures that capture the
# _PipelineCtx object, we skip ADK state binding entirely: ADK calls fn()
# with no kwargs, and the closure does all the work via the captured ctx_obj.

def _privacy_guard_fn(ctx_obj: _PipelineCtx):
    """Return a zero-arg closure that runs Phase 1 (Privacy Guard)."""
    def fn() -> dict:
        """Phase 1 — PII masking and NER redaction; enforces Zero Ambient Authority."""
        day_obs = {
            o["student_id"]: o
            for o in ctx_obj.teacher_obs
            if o.get("date") == ctx_obj.run_date
        }
        sanitized = []
        for checkin in ctx_obj.day_checkins:
            sid = checkin.get("student_id", "")
            record = ctx_obj.orch._sanitise_checkin(
                checkin, day_obs.get(sid), ctx_obj.registry
            )
            if record is not None:
                sanitized.append(record)
        ctx_obj.sanitized_records = sanitized
        return {"phase": "privacy_guard_done", "sanitized_count": len(sanitized)}
    fn.__name__ = "privacy_guard"
    return fn


def _signal_memory_fn(ctx_obj: _PipelineCtx):
    """Return a zero-arg closure that runs Phases 2-3 (Signal Detector + Memory Keeper)."""
    def fn() -> dict:
        """Phase 2-3 — 1 batch LLM call for seniors; per-student Memory Keeper update."""
        orch = ctx_obj.orch
        # Phase 2: single batch LLM call for all senior check-in text
        orch._signal_cache = orch._batch_prefetch_signals(ctx_obj.sanitized_records)
        # Phase 3: per-student Signal Detector + Memory Keeper
        for record in ctx_obj.sanitized_records:
            orch._detect_and_track(record)
        ctx_obj.trend_reports = list(orch.session_state.get("trend_reports", []))
        urgent   = [r for r in ctx_obj.trend_reports if r.get("recommended_priority") == "urgent"]
        elevated = [r for r in ctx_obj.trend_reports if r.get("recommended_priority") == "elevated"]
        return {
            "phase": "signal_memory_done",
            "trend_count": len(ctx_obj.trend_reports),
            "urgent_count": len(urgent),
            "elevated_count": len(elevated),
        }
    fn.__name__ = "signal_and_memory"
    return fn


def _brief_judge_fn(ctx_obj: _PipelineCtx):
    """Return a zero-arg closure that runs Phase 4 (Brief assembly + LLM-as-judge)."""
    def fn() -> dict:
        """Phase 4 — Brief assembly (LLM per urgent student) + 5-criterion LLM-as-judge."""
        orch = ctx_obj.orch
        known_names = [r["fictional_name"] for r in ctx_obj.registry if r.get("fictional_name")]
        brief    = orch._assemble_brief(ctx_obj.run_date, known_names)
        scorecard = orch._run_judge(brief, known_names)
        orch.session_state["daily_brief"]    = brief
        orch.session_state["judge_scorecard"] = scorecard
        ctx_obj.daily_brief    = brief
        ctx_obj.judge_scorecard = scorecard
        return {
            "phase": "brief_judge_done",
            "judge_passed": scorecard.get("overall_pass", False),
            "judge_score":  scorecard.get("score", 0.0),
        }
    fn.__name__ = "brief_and_judge"
    return fn


# ── HITL gate — ADK LlmAgent ──────────────────────────────────────────────────

schoolpulse_hitl = LlmAgent(
    name="schoolpulse_hitl",
    model=MODEL,
    description=(
        "HITL counselor gate: presents the SchoolPulse Daily Brief and collects one of "
        "three decisions — APPROVE_AND_LOG, OVERRIDE_NO_ACTION, or REQUEST_MORE_CONTEXT."
    ),
    instruction=(
        "You are the SchoolPulse counselor gate. A Daily Brief has been assembled for today's "
        "at-risk students. Present it clearly and ask the counselor to choose one of:\n\n"
        "  • APPROVE_AND_LOG — accept the brief; referrals are written for all flagged students\n"
        "  • OVERRIDE_NO_ACTION — override: no action taken; your reason is logged to the audit trail\n"
        "  • REQUEST_MORE_CONTEXT <student_id> — request the student's 7-day signal history\n\n"
        "Never write a referral without APPROVE_AND_LOG. "
        "Every OVERRIDE_NO_ACTION must be logged regardless of reason."
    ),
)


# ── Workflow builder ───────────────────────────────────────────────────────────

def build_workflow(ctx: _PipelineCtx) -> Workflow:
    """
    Build an ADK Workflow graph for one day's SchoolPulse pipeline run.

    Nodes are bound to the shared context so state (sanitized records,
    trend reports, brief text) flows between FunctionNodes via the
    captured _PipelineCtx — keeping pipeline logic in Python and
    ADK's Workflow as the orchestration layer.

    Graph:
      START → privacy_guard → signal_and_memory → brief_and_judge → schoolpulse_hitl
    """
    pg_node = FunctionNode(func=_privacy_guard_fn(ctx), name="privacy_guard")
    sm_node = FunctionNode(func=_signal_memory_fn(ctx), name="signal_and_memory")
    bj_node = FunctionNode(func=_brief_judge_fn(ctx),   name="brief_and_judge")

    return Workflow(
        name="schoolpulse_daily_pipeline",
        description=(
            "SchoolPulse mental-health-first-responder pipeline: "
            "Privacy Guard → Signal Detector → Memory Keeper → "
            "Daily Brief + LLM-as-judge → HITL counselor gate."
        ),
        edges=[
            Edge(from_node=START,   to_node=pg_node),
            Edge(from_node=pg_node, to_node=sm_node),
            Edge(from_node=sm_node, to_node=bj_node),
            Edge(from_node=bj_node, to_node=schoolpulse_hitl),
        ],
    )


# ── Convenience runner ─────────────────────────────────────────────────────────

def run_one_day_via_adk(
    all_checkins: list,
    teacher_obs: list,
    registry: list,
    date: str = "2026-06-28",
    use_real_llm: bool = False,
) -> _PipelineCtx:
    """
    Run one day of the SchoolPulse pipeline via the ADK Workflow FunctionNodes.

    Builds the ADK Workflow graph, then executes each FunctionNode in edge
    order by calling the underlying closure directly (_func). The HITL
    LlmAgent is wired into the graph but skipped here — use build_workflow()
    + ADK Runner for an interactive counselor session.

    Returns the populated _PipelineCtx (brief, scorecard, trend_reports).
    """
    signal_llm = RealSignalLLM() if use_real_llm else FakeSignalLLM()
    orch_llm   = RealOrchestratorLLM() if use_real_llm else FakeOrchestratorLLM()

    orch = SchoolPulseOrchestrator(signal_llm=signal_llm, orchestrator_llm=orch_llm)
    orch.session_state = {
        "run_date": date,
        "student_id_registry": {
            r["fictional_name"]: r["student_id"]
            for r in registry if r.get("fictional_name")
        },
        "processed_students": [], "skipped_students": [],
        "trend_reports": [], "daily_brief": "", "judge_scorecard": {},
        "hitl_outcome": "pending", "referral_log": [], "audit_log": [],
    }
    orch._action_cache = {}
    orch._signal_cache = {}

    day_checkins = [c for c in all_checkins if c.get("date") == date]
    ctx = _PipelineCtx(
        orch=orch,
        run_date=date,
        day_checkins=day_checkins,
        teacher_obs=teacher_obs,
        registry=registry,
    )

    # Build the ADK Workflow graph, then execute FunctionNodes in edge order.
    # Each FunctionNode stores its closure in _func (ADK private attribute);
    # the zero-arg closures need no ctx.state binding — they capture ctx directly.
    workflow = build_workflow(ctx)
    for edge in workflow.edges:
        node = edge.to_node
        if hasattr(node, "_func"):          # FunctionNode (skip LlmAgent)
            node._func()

    return ctx
