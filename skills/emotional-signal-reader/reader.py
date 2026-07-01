"""
emotional-signal-reader skill implementation.

Converts sanitised student check-in records into structured signal objects.
Three modality paths:
  A. Emoji  (junior)   — deterministic lookup table
  B. Text   (senior)   — LLM reasoning via Anthropic API
  C. Teacher note      — flag_level map + distress keyword scan
Merges when multiple modalities are present for the same record.

Hard rules enforced here (not just in docs):
  - signal_confidence never exceeds 0.95
  - Never sets recommended_priority (that belongs to Memory Keeper)
  - Never reaches into prior days (single-date scope only)
"""

import json
import os
import re
from typing import Optional


# ── Emoji lookup table (from references/emoji_affect_table.md) ───────────────
# Format: emoji -> (valence_delta, energy_delta)
# withdrawal_emojis: presence of any sets social_withdrawal_flag = True
# For 💔 and 🖤, flag only fires when combined with other negatives (checked at sequence level)

_EMOJI_TABLE: dict[str, tuple[float, float]] = {
    "😢": (-0.8, 0.2), "😭": (-0.9, 0.3), "😔": (-0.6, 0.3), "😞": (-0.6, 0.2),
    "😠": (-0.5, 0.7), "😡": (-0.7, 0.8), "😤": (-0.4, 0.6), "😰": (-0.7, 0.5),
    "😨": (-0.7, 0.4), "😱": (-0.8, 0.6), "🤒": (-0.4, 0.1), "🤢": (-0.5, 0.1),
    "😴": (-0.2, 0.0), "🥱": (-0.1, 0.1), "😑": (-0.3, 0.1),
    "😶": (-0.4, 0.2), "😶‍🌫️": (-0.5, 0.2), "🙈": (-0.3, 0.2),
    "💔": (-0.8, 0.2), "🖤": (-0.5, 0.2),
    "😊": (0.7, 0.6), "😄": (0.8, 0.8), "😁": (0.8, 0.9), "🥰": (0.9, 0.7),
    "😍": (0.8, 0.7), "🤩": (0.9, 0.9), "😎": (0.6, 0.7), "🙂": (0.4, 0.5),
    "😌": (0.5, 0.4), "🤗": (0.7, 0.7), "✨": (0.6, 0.7), "🌟": (0.7, 0.8),
    "❤️": (0.8, 0.6), "💪": (0.6, 0.9), "🎉": (0.8, 0.9),
    "😐": (0.0, 0.3), "🤔": (0.0, 0.5), "😅": (-0.1, 0.5),
    "😬": (-0.2, 0.4), "🙃": (-0.1, 0.4),
}
_ALWAYS_WITHDRAWAL = {"😶", "😶‍🌫️", "🙈"}
_CONDITIONAL_WITHDRAWAL = {"💔", "🖤"}  # only with 2+ other negatives in sequence
_NEGATIVE_EMOJIS = {e for e, (v, _) in _EMOJI_TABLE.items() if v < 0}

# ── Teacher note: flag_level → valence adjustment ────────────────────────────
_FLAG_LEVEL_ADJUSTMENT: dict[str, float] = {
    "none": 0.0,
    "watch": -0.2,
    "concern": -0.5,
}

# ── Distress keywords for teacher note NER scan ──────────────────────────────
_DISTRESS_KEYWORDS = [
    "crying", "cry", "cried", "cries",
    "withdrawn", "withdrawal", "withdrawing",
    "not eating", "hasn't been eating", "barely ate", "didn't eat",
    "aggressive", "aggression",
    "isolated", "isolation", "alone", "lonely",
    "distressed", "distress",
    "upset", "tearful", "sobbing",
    "doesn't want to come", "don't want to come",
    "refusing",
]


def _parse_emoji_signal(
    student_id: str, date: str, emoji_sequence: str
) -> dict:
    """Deterministic emoji path. Returns partial signal dict."""
    if not emoji_sequence:
        return _empty_signal(student_id, date, "emoji")

    # Extract individual emojis (handles multi-codepoint sequences like 😶‍🌫️)
    import unicodedata  # noqa: PLC0415
    emojis: list[str] = []
    chars = list(emoji_sequence)
    i = 0
    while i < len(chars):
        # Check for multi-codepoint emoji (ZWJ sequences)
        j = i + 1
        # Accumulate variation selectors and ZWJ sequences
        combined = chars[i]
        while j < len(chars) and (
            chars[j] == "‍"  # ZWJ
            or unicodedata.category(chars[j]) in ("Mn", "Cf")  # combining/format
            or (j + 1 < len(chars) and chars[j] == "️")   # variation selector
        ):
            combined += chars[j]
            j += 1
        emojis.append(combined)
        i = j

    if not emojis:
        return _empty_signal(student_id, date, "emoji")

    valences: list[float] = []
    energies: list[float] = []
    withdrawal = False
    negative_count = 0

    for emoji in emojis:
        v, e = _EMOJI_TABLE.get(emoji, (0.0, 0.3))  # neutral defaults for unknowns
        valences.append(v)
        energies.append(e)
        if emoji in _ALWAYS_WITHDRAWAL:
            withdrawal = True
        if v < 0:
            negative_count += 1

    # Conditional withdrawal emojis fire only with 2+ other negatives
    for emoji in emojis:
        if emoji in _CONDITIONAL_WITHDRAWAL and negative_count >= 2:
            withdrawal = True

    avg_valence = sum(valences) / len(valences)
    avg_energy = sum(energies) / len(energies)
    confidence = 1.0 if len(emojis) >= 2 else 0.6

    return {
        "student_id": student_id,
        "date": date,
        "emotional_valence": round(avg_valence, 4),
        "energy_level": round(avg_energy, 4),
        "social_withdrawal_flag": withdrawal,
        "distress_keywords_detected": [],
        "signal_confidence": min(confidence, 0.95),
        "raw_input_type": "emoji",
    }


def _parse_text_signal(
    student_id: str,
    date: str,
    response: str,
    client=None,
) -> dict:
    """
    LLM-reasoning path for senior free-text responses.
    Calls Anthropic API. Returns partial signal dict.
    """
    if not response or not response.strip():
        return _empty_signal(student_id, date, "text")

    words = response.split()
    word_count = len(words)
    if word_count >= 15:
        confidence = 0.9
    elif word_count >= 5:
        confidence = 0.6
    else:
        confidence = 0.3

    try:
        if client is None:
            import anthropic  # noqa: PLC0415
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        prompt = f"""You are analyzing a student's check-in response for emotional signals.

Student response: "{response}"

Extract the following as valid JSON (and nothing else — no markdown, no explanation):
{{
  "emotional_valence": <float -1.0 to +1.0; -1.0 = very distressed, +1.0 = very positive>,
  "energy_level": <float 0.0 to 1.0; 0.0 = exhausted/passive, 1.0 = energised/engaged>,
  "social_withdrawal_flag": <true if text contains isolation language: "nobody", "alone", "haven't talked", "don't want to", "disappear", "avoiding", "keeping to myself">,
  "distress_keywords_detected": <list of exact phrases from the text that signal distress or withdrawal>
}}

Rules:
- Be conservative: only flag social_withdrawal_flag if explicitly present
- distress_keywords_detected contains literal phrases from the text, not paraphrases
- Do not invent content not in the response"""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        parsed = json.loads(raw)
        valence = float(parsed.get("emotional_valence", 0.0))
        energy = float(parsed.get("energy_level", 0.3))
        withdrawal = bool(parsed.get("social_withdrawal_flag", False))
        keywords = [str(k) for k in parsed.get("distress_keywords_detected", [])]

    except Exception:
        # Log-worthy failure; return low-confidence neutral signal rather than crashing
        return {
            "student_id": student_id,
            "date": date,
            "emotional_valence": 0.0,
            "energy_level": 0.3,
            "social_withdrawal_flag": False,
            "distress_keywords_detected": [],
            "signal_confidence": 0.0,
            "raw_input_type": "text",
        }

    return {
        "student_id": student_id,
        "date": date,
        "emotional_valence": round(max(-1.0, min(1.0, valence)), 4),
        "energy_level": round(max(0.0, min(1.0, energy)), 4),
        "social_withdrawal_flag": withdrawal,
        "distress_keywords_detected": keywords,
        "signal_confidence": min(confidence, 0.95),
        "raw_input_type": "text",
    }


def _parse_teacher_note_signal(
    student_id: str,
    date: str,
    note: str,
    flag_level: str,
) -> dict:
    """
    Teacher note path: flag_level → valence adjustment + distress keyword scan.
    Returns partial signal dict.
    """
    valence_adj = _FLAG_LEVEL_ADJUSTMENT.get(flag_level.lower() if flag_level else "none", 0.0)

    keywords_found: list[str] = []
    withdrawal = False
    note_lower = note.lower() if note else ""

    for kw in _DISTRESS_KEYWORDS:
        if kw in note_lower:
            keywords_found.append(kw)
            if kw in ("withdrawn", "withdrawal", "isolated", "isolation", "alone"):
                withdrawal = True

    # The valence_adj is the direct output; energy is moderately low if concern flagged
    energy = 0.4 if flag_level == "concern" else 0.5 if flag_level == "watch" else 0.6

    return {
        "student_id": student_id,
        "date": date,
        "emotional_valence": round(max(-1.0, min(1.0, valence_adj)), 4),
        "energy_level": round(energy, 4),
        "social_withdrawal_flag": withdrawal,
        "distress_keywords_detected": keywords_found,
        "signal_confidence": min(0.7, 0.95),  # teacher notes are subjective; cap at 0.7
        "raw_input_type": "teacher_note",
    }


def _merge_signals(signals: list[dict]) -> dict:
    """
    Merge multiple modality signals for the same student+date.
    Weights: emoji 0.4 / text 0.5 / teacher_note 0.1.
    """
    if len(signals) == 1:
        merged = dict(signals[0])
        merged["raw_input_type"] = "merged"
        return merged

    weights = {"emoji": 0.4, "text": 0.5, "teacher_note": 0.1}
    total_weight = 0.0
    weighted_valence = 0.0
    weighted_energy = 0.0
    withdrawal = False
    all_keywords: list[str] = []
    confidences: list[float] = []
    student_id = signals[0]["student_id"]
    date = signals[0]["date"]

    for sig in signals:
        mod = sig["raw_input_type"]
        w = weights.get(mod, 0.33)
        total_weight += w
        weighted_valence += sig["emotional_valence"] * w
        weighted_energy += sig["energy_level"] * w
        if sig["social_withdrawal_flag"]:
            withdrawal = True
        for kw in sig["distress_keywords_detected"]:
            if kw not in all_keywords:
                all_keywords.append(kw)
        confidences.append(sig["signal_confidence"])

    if total_weight == 0:
        total_weight = 1.0

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "student_id": student_id,
        "date": date,
        "emotional_valence": round(weighted_valence / total_weight, 4),
        "energy_level": round(weighted_energy / total_weight, 4),
        "social_withdrawal_flag": withdrawal,
        "distress_keywords_detected": all_keywords,
        "signal_confidence": round(min(avg_confidence, 0.95), 4),
        "raw_input_type": "merged",
    }


def _empty_signal(student_id: str, date: str, raw_type: str) -> dict:
    return {
        "student_id": student_id,
        "date": date,
        "emotional_valence": 0.0,
        "energy_level": 0.3,
        "social_withdrawal_flag": False,
        "distress_keywords_detected": [],
        "signal_confidence": 0.0,
        "raw_input_type": raw_type,
    }


def read_signal(record: dict, llm_client=None) -> dict:
    """
    Main entry point. Convert a sanitised check-in record into a signal object.

    Args:
        record:     Sanitised check-in record (must have sanitisation_manifest
                    with boundary_checks_passed=True — enforced by the agent).
        llm_client: Optional pre-built Anthropic client (for testing/injection).

    Returns:
        signal dict conforming to the emotional-signal-reader output schema.
    """
    student_id: str = record.get("student_id", "")
    date: str = record.get("date", "")
    age_group: str = record.get("age_group", "")

    signals_to_merge: list[dict] = []

    # ── A. Emoji path (junior) ────────────────────────────────────────────────
    ji = record.get("junior_input")
    if ji and ji.get("emoji_sequence"):
        signals_to_merge.append(
            _parse_emoji_signal(student_id, date, ji["emoji_sequence"])
        )

    # ── B. Text path (senior) ─────────────────────────────────────────────────
    si = record.get("senior_input")
    if si and si.get("response"):
        signals_to_merge.append(
            _parse_text_signal(student_id, date, si["response"], client=llm_client)
        )

    # ── C. Teacher note path (any age) ───────────────────────────────────────
    to = record.get("teacher_observation")
    if to and (to.get("note") or to.get("flag_level")):
        signals_to_merge.append(
            _parse_teacher_note_signal(
                student_id,
                date,
                to.get("note") or "",
                to.get("flag_level") or "none",
            )
        )

    if not signals_to_merge:
        sig = _empty_signal(student_id, date, "text" if age_group == "senior" else "emoji")
        return sig

    if len(signals_to_merge) == 1:
        return signals_to_merge[0]

    return _merge_signals(signals_to_merge)
