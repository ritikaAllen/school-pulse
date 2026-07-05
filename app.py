"""
SchoolPulse — FastAPI deployment entry point

Exposes the ADK Workflow pipeline as an HTTP API so the service can run
on Cloud Run, a VM, or any container platform.

Endpoints:
  GET  /health          — liveness probe
  POST /run             — run one day's pipeline (Fake LLMs, no API key needed)
  POST /run?real_llm=1  — run with real Gemini (requires GOOGLE_API_KEY)

Example:
  curl -X POST http://localhost:8080/run \
       -H "Content-Type: application/json" \
       -d '{"date": "2026-06-28"}'
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from agents.adk_workflow import run_one_day_via_adk
from agents.orchestrator import DEMO_DATES

# ── Load synthetic dataset once at startup ─────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data" / "synthetic"

def _load(filename: str) -> list:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)

CHECKINS    = _load("synthetic_checkins.json")
TEACHER_OBS = _load("teacher_observations.json")
REGISTRY    = _load("student_registry.json")

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SchoolPulse",
    description=(
        "Mental-health-first-responder pipeline. "
        "Processes student check-in data through Privacy Guard → "
        "Signal Detector → Memory Keeper → Daily Brief → LLM-as-judge → HITL gate."
    ),
    version="1.0.0",
)


class RunRequest(BaseModel):
    date: str = "2026-06-28"


class RunResponse(BaseModel):
    date: str
    sanitized_students: int
    trend_reports: int
    urgent_count: int
    elevated_count: int
    judge_passed: bool
    judge_score: float
    daily_brief: str


@app.get("/health")
def health():
    return {"status": "ok", "demo_dates": DEMO_DATES}


@app.post("/run", response_model=RunResponse)
def run_pipeline(
    body: RunRequest,
    real_llm: bool = Query(default=False, description="Use real Gemini LLM (requires GOOGLE_API_KEY)"),
):
    if body.date not in DEMO_DATES:
        raise HTTPException(
            status_code=400,
            detail=f"Date {body.date!r} not in dataset. Available: {DEMO_DATES}",
        )

    ctx = run_one_day_via_adk(
        all_checkins=CHECKINS,
        teacher_obs=TEACHER_OBS,
        registry=REGISTRY,
        date=body.date,
        use_real_llm=real_llm,
    )

    urgent   = [r for r in ctx.trend_reports if r.get("recommended_priority") == "urgent"]
    elevated = [r for r in ctx.trend_reports if r.get("recommended_priority") == "elevated"]
    score    = ctx.judge_scorecard.get("weighted_score", ctx.judge_scorecard.get("score", 0.0))
    passed   = ctx.judge_scorecard.get("pass", ctx.judge_scorecard.get("overall_pass", False))

    return RunResponse(
        date=body.date,
        sanitized_students=len(ctx.sanitized_records),
        trend_reports=len(ctx.trend_reports),
        urgent_count=len(urgent),
        elevated_count=len(elevated),
        judge_passed=bool(passed),
        judge_score=float(score),
        daily_brief=ctx.daily_brief,
    )
