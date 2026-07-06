"""
LLM interface layer for SchoolPulse.

Option B (see DECISIONS.md): two LLM seams are wrapped by thin interfaces so
integration tests run deterministically without credentials.

FakeSignalLLM  — mimics Anthropic client .messages.create() used by reader.py.
                 Keyed by response text; keyword heuristic for unknown responses.

FakeOrchestratorLLM — deterministic recommended_action + rubric-based judge.

RealSignalLLM / RealOrchestratorLLM — Gemini 2.5 Flash Lite wrappers for demo runs.
  Requires: pip install google-genai   and   GOOGLE_API_KEY env var.
  Phase 4 reader.py was originally prototyped with claude-haiku-4-5-20251001
  (Anthropic). Phase 5 corrects the Real* demo path to Gemini 2.5 Flash Lite per
  SPEC.md. Fake implementations are unchanged.
"""

import datetime
import json
import logging
import os
import re
import time
from pathlib import Path


# ── FakeSignalLLM ─────────────────────────────────────────────────────────────

# Pre-computed signal values for arc-critical senior students.
# Key: exact response text from synthetic_checkins.json.
# Value: {"emotional_valence", "energy_level", "social_withdrawal_flag",
#          "distress_keywords_detected"}
_SIGNAL_FIXTURES: dict[str, dict] = {
    # ── S_004 Rohan Mehta (urgent_pattern_break) ────────────────────────────
    "Feeling pretty good today. Had a fun time at football practice.": {
        "emotional_valence": 0.60, "energy_level": 0.65,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Pretty okay. A bit tired from studying but nothing major.": {
        "emotional_valence": 0.20, "energy_level": 0.45,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Good day overall. Lunch with friends was nice.": {
        "emotional_valence": 0.50, "energy_level": 0.60,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Feeling alright. A bit distracted in class but fine.": {
        "emotional_valence": 0.30, "energy_level": 0.50,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Okay I guess. Nothing exciting. Same as usual.": {
        "emotional_valence": 0.10, "energy_level": 0.40,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Kind of tired. Not much to say today.": {
        "emotional_valence": -0.10, "energy_level": 0.30,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "I don't really see the point of being here. I feel like nobody would notice if I just disappeared.": {
        "emotional_valence": -0.80, "energy_level": 0.20,
        "social_withdrawal_flag": True,
        "distress_keywords_detected": ["nobody would notice", "disappeared"],
    },

    # ── S_003 Aisha Johnson (elevated_declining) ────────────────────────────
    # withdrawal=False on day 7 intentionally: prevents "urgent" via the
    # sig_withdrawal AND consecutive_low >= 2 rule; keeps priority = elevated.
    "I'm doing okay, just a little tired.": {
        "emotional_valence": -0.10, "energy_level": 0.45,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Feeling a bit flat today. Not sure why.": {
        "emotional_valence": -0.10, "energy_level": 0.40,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Kind of meh. Hard to focus in class.": {
        "emotional_valence": -0.15, "energy_level": 0.40,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Not great. Things at home have been a bit tense.": {
        "emotional_valence": -0.20, "energy_level": 0.35,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Pretty low today. Don't really want to talk to anyone.": {
        "emotional_valence": -0.25, "energy_level": 0.30,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Feeling pretty down. Didn't really eat much at lunch.": {
        "emotional_valence": -0.35, "energy_level": 0.25,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Really struggling today. I've been avoiding my friends. It feels easier that way.": {
        "emotional_valence": -0.45, "energy_level": 0.20,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },

    # ── S_012 Priya Nair (elevated_late_dip) ────────────────────────────────
    # Days 1-5: neutral/mild texts — _keyword_analyze covers them (valence ~ -0.10 to -0.25,
    #   all above LOW_VALENCE_BOUNDARY -0.3 so no consecutive_low accumulates)
    # Days 6-7: clearly sad texts → explicit fixtures at -0.40 (below -0.3 threshold)
    #   consecutive_low_days=2 on Day 7 → elevated
    #   delta from baseline (~-0.12) = 0.28 < PATTERN_BREAK_DELTA 0.4 → no pattern break
    "Feeling pretty sad today. Not sure why, just really down.": {
        "emotional_valence": -0.40, "energy_level": 0.25,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
    "Still feeling low. Hard to get motivated and things feel heavy.": {
        "emotional_valence": -0.40, "energy_level": 0.25,
        "social_withdrawal_flag": False, "distress_keywords_detected": [],
    },
}

# Keyword lists for the heuristic fallback (covers all other senior students).
_STRONG_DISTRESS = [
    "nobody would notice", "wouldn't notice", "disappear",
    "no reason to be here", "no point of being here",
    "don't want to exist", "nobody cares about me",
]
_WITHDRAWAL_PHRASES = [
    "avoiding", "keeping to myself", "spending a lot of time alone",
    "don't want to talk to anyone", "don't really want to talk",
    "don't want to see anyone", "isolating",
]
_STRONG_NEG = ["struggling", "really down", "really low", "very down", "hopeless"]
_MOD_NEG = [
    "not great", "pretty down", "feeling down", "flat", "meh",
    "hard to focus", "pretty low",
]
_MILD_NEG = [
    "tired", "anxious", "low on energy", "not much to say",
    "going through the motions", "not motivated", "a bit stressed",
    "a bit worried", "not sure about anything",
]
_POSITIVE = [
    "good", "great", "happy", "positive", "excited", "fun",
    "better", "well", "nice", "best", "enjoying",
]


def _keyword_analyze(response: str) -> dict:
    """Rule-based text analysis for unknown response texts."""
    lower = response.lower()

    for kw in _STRONG_DISTRESS:
        if kw in lower:
            return {
                "emotional_valence": -0.85,
                "energy_level": 0.20,
                "social_withdrawal_flag": True,
                "distress_keywords_detected": [kw],
            }

    withdrawal = any(kw in lower for kw in _WITHDRAWAL_PHRASES)
    kw_found = [kw for kw in _WITHDRAWAL_PHRASES if kw in lower]

    if any(kw in lower for kw in _STRONG_NEG):
        valence, energy = -0.45, 0.25
    elif any(kw in lower for kw in _MOD_NEG):
        valence, energy = -0.25, 0.35
    elif any(kw in lower for kw in _MILD_NEG):
        valence, energy = -0.15, 0.40
    elif any(kw in lower for kw in _POSITIVE):
        valence, energy = 0.55, 0.65
    else:
        valence, energy = 0.20, 0.50

    return {
        "emotional_valence": valence,
        "energy_level": energy,
        "social_withdrawal_flag": withdrawal,
        "distress_keywords_detected": kw_found,
    }


class _FakeContent:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, payload: dict):
        self.content = [_FakeContent(json.dumps(payload))]


class _FakeMessagesNamespace:
    """Mimics anthropic.Anthropic().messages so reader.py can call .create()."""

    def create(self, model: str, max_tokens: int, messages: list, **_) -> _FakeMessage:
        prompt = messages[0]["content"] if messages else ""
        m = re.search(r'Student response: "(.+?)"(?:\n|$)', prompt, re.DOTALL)
        response_text = m.group(1).strip() if m else ""

        payload = _SIGNAL_FIXTURES.get(response_text) or _keyword_analyze(response_text)
        return _FakeMessage(payload)


class FakeSignalLLM:
    """
    Deterministic LLM stub for the emotional-signal-reader text path.
    Mimics the Anthropic client interface expected by reader._parse_text_signal().
    """

    def __init__(self):
        self.messages = _FakeMessagesNamespace()

    def batch_parse(self, records: list[dict]) -> dict:
        """Deterministic batch parse — calls per-record fixture/heuristic, no API."""
        result = {}
        for rec in records:
            sid = rec["student_id"]
            response = rec.get("response", "")
            payload = _SIGNAL_FIXTURES.get(response) or _keyword_analyze(response)
            words = response.split()
            wc = len(words)
            confidence = 0.9 if wc >= 15 else 0.6 if wc >= 5 else 0.3
            result[sid] = {
                "student_id": sid,
                "date": rec.get("date", ""),
                "emotional_valence": payload["emotional_valence"],
                "energy_level": payload["energy_level"],
                "social_withdrawal_flag": payload["social_withdrawal_flag"],
                "distress_keywords_detected": payload["distress_keywords_detected"],
                "signal_confidence": min(confidence, 0.95),
                "raw_input_type": "text",
            }
        return result


# ── API call logger ───────────────────────────────────────────────────────────
# Each kernel start creates a timestamped file: api_calls_YYYYMMDD_HHMMSS.log
# so successive runs are preserved side by side for comparison.
# propagate=False keeps all output out of the notebook entirely.
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_api_logger = logging.getLogger("schoolpulse.api")
if not _api_logger.handlers:
    _api_logger.propagate = False
    # Kaggle: fixed filename so judges see a clean api_calls.log in the output tab.
    # Local: versioned filename so successive runs are preserved side by side.
    _on_kaggle = bool(os.environ.get("KAGGLE_KERNEL_RUN_TYPE"))
    _log_name = "api_calls.log" if _on_kaggle else f"api_calls_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    _fh = logging.FileHandler(_LOG_DIR / _log_name, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    _api_logger.addHandler(_fh)
_api_logger.setLevel(logging.INFO)


def _generate_with_retry(client, model: str, contents: str, max_retries: int = 4, caller: str = "", context: str = ""):
    """Wrap generate_content with automatic retry on 429 RESOURCE_EXHAUSTED."""
    _api_logger.info("CALL  %-22s  model=%s  chars=%d%s", caller, model, len(contents), f"  {context}" if context else "")
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=model, contents=contents)
            _api_logger.info("OK    %-22s  attempt=%d", caller, attempt + 1)
            return response
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                m = re.search(r"retry[^0-9]*(\d+)", msg, re.IGNORECASE)
                wait = int(m.group(1)) + 2 if m else 30
                _api_logger.warning("429   %-22s  attempt=%d  wait=%ds", caller, attempt + 1, wait)
                if attempt < max_retries - 1:
                    print(f"    [rate limit] waiting {wait}s (retry {attempt + 1}/{max_retries - 1})...")
                    time.sleep(wait)
                    continue
            _api_logger.error("ERR   %-22s  %s", caller, msg[:120])
            raise
    raise RuntimeError(f"Rate limit: max retries ({max_retries}) exceeded")


class _GeminiContent:
    def __init__(self, text: str):
        self.text = text


class _GeminiMessage:
    def __init__(self, text: str):
        self.content = [_GeminiContent(text)]


class _GeminiMessagesAdapter:
    """
    Adapts the Gemini SDK to the Anthropic .messages.create() interface
    expected by reader.py — so reader.py needs no modification for the demo path.
    """

    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    def create(self, model: str, max_tokens: int, messages: list, **kwargs) -> _GeminiMessage:
        prompt = messages[0]["content"] if messages else ""
        response = _generate_with_retry(self._client, self._model, prompt, caller="signal_reader")
        return _GeminiMessage(response.text)


class RealSignalLLM:
    """
    Gemini 2.5 Flash Lite wrapper for the emotional-signal-reader text path.

    Exposes an Anthropic-compatible .messages.create() interface via
    _GeminiMessagesAdapter so reader.py works without modification.
    Requires:  pip install google-genai   +   GOOGLE_API_KEY env var.
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-3.1-flash-lite"):
        try:
            from google import genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "google-genai package required for RealSignalLLM: pip install google-genai"
            ) from exc
        client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self._client = client
        self._model = model
        self.messages = _GeminiMessagesAdapter(client, model)

    def batch_parse(self, records: list[dict]) -> dict:
        """
        One LLM call for all senior text responses.
        Returns {student_id: signal_dict}.
        """
        if not records:
            return {}
        entries = [{"student_id": r["student_id"], "response": r.get("response", "")} for r in records]
        prompt = (
            "Analyze each student's check-in response for emotional signals.\n\n"
            f"Students: {json.dumps(entries)}\n\n"
            "For each student return a JSON object with:\n"
            "  emotional_valence: float -1.0 to +1.0 (-1=very distressed, +1=very positive)\n"
            "  energy_level: float 0.0 to 1.0 (0=exhausted, 1=energised)\n"
            "  social_withdrawal_flag: bool (true ONLY if isolation language is explicit)\n"
            "  distress_keywords_detected: list of exact phrases from the text\n\n"
            "Rules: be conservative; only flag withdrawal if explicitly stated; "
            "distress_keywords_detected contains literal phrases only.\n\n"
            "Return ONLY a JSON array where each element has: "
            "student_id, emotional_valence, energy_level, social_withdrawal_flag, distress_keywords_detected"
        )
        ctx = f"count={len(records)}  ids={[r['student_id'] for r in records]}"
        response = _generate_with_retry(self._client, self._model, prompt, caller="signal_batch", context=ctx)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        result = {}
        for item in json.loads(raw):
            sid = item["student_id"]
            rec = next((r for r in records if r["student_id"] == sid), {})
            words = rec.get("response", "").split()
            wc = len(words)
            confidence = 0.9 if wc >= 15 else 0.6 if wc >= 5 else 0.3
            result[sid] = {
                "student_id": sid,
                "date": rec.get("date", ""),
                "emotional_valence": round(max(-1.0, min(1.0, float(item.get("emotional_valence", 0.0)))), 4),
                "energy_level": round(max(0.0, min(1.0, float(item.get("energy_level", 0.3)))), 4),
                "social_withdrawal_flag": bool(item.get("social_withdrawal_flag", False)),
                "distress_keywords_detected": [str(k) for k in item.get("distress_keywords_detected", [])],
                "signal_confidence": min(confidence, 0.95),
                "raw_input_type": "text",
            }
        return result


# ── FakeOrchestratorLLM ───────────────────────────────────────────────────────

class FakeOrchestratorLLM:
    """
    Deterministic stubs for Orchestrator's own LLM calls:
      - generate_recommended_action()
      - judge_brief()
    """

    def generate_recommended_action(self, student_id: str, report: dict) -> str:
        if report.get("pattern_break_detected"):
            return "Direct counselor check-in today. Sudden significant drop from baseline detected."
        n = report.get("consecutive_low_days", 0)
        return f"One-on-one conversation. {n} consecutive low-mood day(s) recorded. Consider parent notification."

    def judge_brief(self, judge_input: dict) -> dict:
        """
        Rule-based rubric evaluation:
          - signal_coverage:     all expected urgent/elevated students mentioned in brief
          - escalation_accuracy: urgent students appear in URGENT section
          - pii_free_output:     no known student names in brief text
          - counselor_action_clarity: recommended_action present for each urgent student
          - false_positive_rate: routine students absent from urgent/elevated sections
        """
        brief = judge_input.get("daily_brief", "")
        expected_urgent = judge_input.get("expected_urgent_students", [])
        expected_elevated = judge_input.get("expected_elevated_students", [])
        expected_routine = judge_input.get("expected_routine_students", [])
        known_names = judge_input.get("known_names", [])

        # signal_coverage (0.25): all elevated+urgent students mentioned
        all_flagged = expected_urgent + expected_elevated
        mentioned_count = sum(1 for sid in all_flagged if sid in brief)
        if not all_flagged:
            signal_coverage = 2
        elif mentioned_count == len(all_flagged):
            signal_coverage = 2
        elif mentioned_count > 0:
            signal_coverage = 1
        else:
            signal_coverage = 0

        # escalation_accuracy (0.30): urgent students in URGENT section
        urgent_section = _extract_section(brief, "URGENT")
        escalation_score = 2
        for sid in expected_urgent:
            if sid not in urgent_section:
                escalation_score = 0
                break

        # pii_free_output (0.20): no real names in brief
        pii_score = 2
        for name in known_names:
            for part in name.split():
                if len(part) > 2 and re.search(r"\b" + re.escape(part) + r"\b", brief, re.IGNORECASE):
                    pii_score = 0
                    break
            if pii_score == 0:
                break

        # counselor_action_clarity (0.15): action text present for urgent students
        action_score = 2 if ("Recommend:" in brief or "recommend" in brief.lower()
                             or "Direct counselor" in brief
                             or "One-on-one" in brief) else 1

        # false_positive_rate (0.10): routine students not in urgent/elevated sections
        non_routine_section = urgent_section + _extract_section(brief, "ELEVATED")
        fp_score = 2
        for sid in expected_routine:
            if sid in non_routine_section:
                fp_score = 0
                break

        weights = {
            "signal_coverage": 0.25,
            "escalation_accuracy": 0.30,
            "pii_free_output": 0.20,
            "counselor_action_clarity": 0.15,
            "false_positive_rate": 0.10,
        }
        scores = {
            "signal_coverage": signal_coverage,
            "escalation_accuracy": escalation_score,
            "pii_free_output": pii_score,
            "counselor_action_clarity": action_score,
            "false_positive_rate": fp_score,
        }
        weighted = sum((scores[k] / 2) * weights[k] for k in weights)
        passing = weighted >= 0.75

        failure_reason = None
        if not passing:
            reasons = []
            if pii_score == 0:
                reasons.append("PII leak detected in brief")
            if signal_coverage < 2:
                reasons.append("Not all flagged students mentioned in brief")
            if escalation_score < 2:
                reasons.append("Urgent students not in URGENT section")
            failure_reason = "; ".join(reasons) or "Quality threshold not met"

        return {
            "signal_coverage": signal_coverage,
            "escalation_accuracy": escalation_score,
            "pii_free_output": pii_score,
            "counselor_action_clarity": action_score,
            "false_positive_rate": fp_score,
            "weighted_score": round(weighted, 4),
            "pass": passing,
            "failure_reason": failure_reason,
        }


def _extract_section(brief: str, section_name: str) -> str:
    """Return text between SECTION_NAME header and the next all-caps section (or end)."""
    pattern = rf"{section_name}[^\n]*\n(.*?)(?=\n[A-Z]{{3,}}|\Z)"
    m = re.search(pattern, brief, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ""


class RealOrchestratorLLM:
    """
    Gemini 2.5 Flash Lite wrapper for orchestrator LLM calls.

    Implements generate_recommended_action() and judge_brief() using the
    google-genai SDK. Requires:  pip install google-genai  +  GOOGLE_API_KEY.
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-3.1-flash-lite"):
        try:
            from google import genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "google-genai package required for RealOrchestratorLLM: pip install google-genai"
            ) from exc
        self._client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self._model = model

    def generate_recommended_action(self, student_id: str, report: dict) -> str:
        prompt = (
            f"Student {student_id} trend report: {json.dumps(report)}\n\n"
            "Write one specific, actionable counselor recommendation (1–2 sentences, "
            "no student name, no PII)."
        )
        ctx = f"student={student_id}  priority={report.get('recommended_priority', '?')}  pattern_break={report.get('pattern_break_detected', False)}"
        response = _generate_with_retry(self._client, self._model, prompt, caller="recommended_action", context=ctx)
        return response.text.strip()

    def judge_brief(self, judge_input: dict) -> dict:
        prompt = (
            "You are an LLM-as-judge evaluating a school counselor's daily brief.\n\n"
            f"Daily Brief:\n{judge_input['daily_brief']}\n\n"
            f"Expected urgent students: {judge_input['expected_urgent_students']}\n"
            f"Expected elevated students: {judge_input['expected_elevated_students']}\n"
            f"Expected routine students: {judge_input['expected_routine_students']}\n\n"
            "Score the brief on these criteria (0–2 each):\n"
            "  signal_coverage (0.25): all elevated/urgent students mentioned?\n"
            "  escalation_accuracy (0.30): urgent students correctly in URGENT section?\n"
            "  pii_free_output (0.20): zero real student names? (binary: 0 or 2)\n"
            "  counselor_action_clarity (0.15): recommended actions specific and actionable?\n"
            "  false_positive_rate (0.10): routine students absent from urgent/elevated?\n\n"
            "Compute weighted_score as a float in [0.0, 1.0]: "
            "sum((score / 2.0) * weight) for each criterion using the weights above.\n"
            "Return ONLY valid JSON with keys: signal_coverage, escalation_accuracy, "
            "pii_free_output, counselor_action_clarity, false_positive_rate, "
            "weighted_score (float 0.0–1.0), pass (bool, true if weighted_score >= 0.75), "
            "failure_reason (null or string)."
        )
        ctx = f"urgent={judge_input.get('expected_urgent_students', [])}  elevated={judge_input.get('expected_elevated_students', [])}"
        response = _generate_with_retry(self._client, self._model, prompt, caller="judge_brief", context=ctx)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
