"""
generate_synthetic_data.py
Mental Health First Responder Agent — Phase 3 Dataset Generation

Produces three files in ./data/:
  - student_registry.json       : 20 students, IDs, age groups, fictional names, arc labels
  - synthetic_checkins.json     : 20 students × 7 days of raw check-in records
  - teacher_observations.json   : ~18 teacher notes across 7 days (2-3/day)

Design constraints (from SPEC.md Section 7):
  - 14 routine students   : stable or improving signals over 7 days
  -  4 elevated students  : declining trend, 1-2 low days, no urgent trigger
  -  2 urgent students    : 3+ consecutive low days OR single-day pattern break

Temporal spine: June 22–28, 2026 (7 days). Pipeline "today" = June 29, 2026.
"""

import json
import random
from pathlib import Path
from copy import deepcopy

random.seed(42)  # reproducible

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

DATES = [
    "2026-06-22",
    "2026-06-23",
    "2026-06-24",
    "2026-06-25",
    "2026-06-26",
    "2026-06-27",
    "2026-06-28",
]

# ---------------------------------------------------------------------------
# STEP 1 — STUDENT REGISTRY DESIGN
# Arc labels are metadata for us (and the judge). NOT surfaced to the agent.
# Names are fictional — present in teacher notes for Privacy Guard to sanitize.
# ---------------------------------------------------------------------------

REGISTRY_DESIGN = [
    # ── URGENT (2) ───────────────────────────────────────────────────────────
    # S_004: Senior. 6-day stable baseline (+0.5), then single-day pattern break
    #        on day 7. Mechanism: delta_from_baseline > 0.4 → urgent.
    {
        "student_id": "S_004",
        "fictional_name": "Rohan Mehta",
        "age_group": "senior",
        "age": 15,
        "arc": "urgent_pattern_break",
        "arc_note": "Stable baseline then single-day crash on day 7 with distress keyword"
    },
    # S_017: Junior. 4 consecutive low-mood days (days 4-7). Emoji signals stay
    #        below -0.3 valence for 4 days → consecutive_low_days >= 3 → urgent.
    {
        "student_id": "S_017",
        "fictional_name": "Lily Chen",
        "age_group": "junior",
        "age": 8,
        "arc": "urgent_consecutive_low",
        "arc_note": "4 consecutive low-mood days; escalates to urgent on day 6"
    },

    # ── ELEVATED (4) ─────────────────────────────────────────────────────────
    # S_003: Senior. Gradual declining trend over 7 days. Never hits urgent
    #        threshold but consistently negative enough to surface as elevated.
    {
        "student_id": "S_003",
        "fictional_name": "Aisha Johnson",
        "age_group": "senior",
        "age": 14,
        "arc": "elevated_declining",
        "arc_note": "Slow decline: starts near 0, ends at -0.4 on day 7"
    },
    # S_009: Junior. Two consecutive low days (days 6-7) after otherwise routine
    #        week. Elevated watch — not enough for urgent.
    {
        "student_id": "S_009",
        "fictional_name": "Marcus Rivera",
        "age_group": "junior",
        "age": 7,
        "arc": "elevated_late_dip",
        "arc_note": "Two consecutive low days at end of window; elevated watch"
    },
    # S_012: Senior. Moderate but persistent low energy + mild withdrawal signals.
    #        Valence hovers around -0.2 to -0.35 but never 3 consecutive days below -0.3.
    {
        "student_id": "S_012",
        "fictional_name": "Priya Nair",
        "age_group": "senior",
        "age": 16,
        "arc": "elevated_persistent_low_energy",
        "arc_note": "Low energy + mild withdrawal; valence borderline but not urgent"
    },
    # S_018: Junior. Inconsistent — mostly routine but days 5 and 7 are low.
    #        Non-consecutive, so no urgent trigger, but declining trajectory warrants
    #        elevated watch.
    {
        "student_id": "S_018",
        "fictional_name": "Zara Okonkwo",
        "age_group": "junior",
        "age": 9,
        "arc": "elevated_inconsistent",
        "arc_note": "Non-consecutive low days; declining trajectory, no urgent threshold"
    },

    # ── ROUTINE (14) ─────────────────────────────────────────────────────────
    {"student_id": "S_001", "fictional_name": "Ethan Park",    "age_group": "junior", "age": 6,  "arc": "routine_stable",    "arc_note": "Consistently positive; small daily variance"},
    {"student_id": "S_002", "fictional_name": "Sofia Martins", "age_group": "senior", "age": 13, "arc": "routine_improving", "arc_note": "Started mildly low day 1, improved steadily"},
    {"student_id": "S_005", "fictional_name": "James Oduya",   "age_group": "junior", "age": 10, "arc": "routine_stable",    "arc_note": "Strong positive signals throughout"},
    {"student_id": "S_006", "fictional_name": "Mei Lin",       "age_group": "senior", "age": 17, "arc": "routine_stable",    "arc_note": "High valence, engaged; model routine case"},
    {"student_id": "S_007", "fictional_name": "Carlos Reyes",  "age_group": "junior", "age": 7,  "arc": "routine_stable",    "arc_note": "Positive with one neutral day"},
    {"student_id": "S_008", "fictional_name": "Amara Diallo",  "age_group": "senior", "age": 15, "arc": "routine_stable",    "arc_note": "Steady positive, minimal variance"},
    {"student_id": "S_010", "fictional_name": "Noah Fitzgerald","age_group": "junior", "age": 9,  "arc": "routine_improving", "arc_note": "Neutral start, improving by day 4"},
    {"student_id": "S_011", "fictional_name": "Kezia Adeyemi", "age_group": "junior", "age": 8,  "arc": "routine_stable",    "arc_note": "7-day valence all above +0.4; false-positive test case"},
    {"student_id": "S_013", "fictional_name": "Liam Svensson", "age_group": "senior", "age": 12, "arc": "routine_stable",    "arc_note": "Positive, moderate energy"},
    {"student_id": "S_014", "fictional_name": "Yuna Tanaka",   "age_group": "junior", "age": 6,  "arc": "routine_stable",    "arc_note": "Cheerful; small dip day 3, recovers day 4"},
    {"student_id": "S_015", "fictional_name": "Omar Hassan",   "age_group": "senior", "age": 11, "arc": "routine_stable",    "arc_note": "Stable, moderately positive"},
    {"student_id": "S_016", "fictional_name": "Isabelle Dupont","age_group": "senior", "age": 14, "arc": "routine_stable",   "arc_note": "Positive signals, no flags"},
    {"student_id": "S_019", "fictional_name": "Ben Okafor",    "age_group": "senior", "age": 16, "arc": "routine_improving", "arc_note": "Mildly anxious day 1 text, then improves"},
    {"student_id": "S_020", "fictional_name": "Hana Kobayashi","age_group": "junior", "age": 10, "arc": "routine_stable",    "arc_note": "High energy, consistently positive"},
]

# ---------------------------------------------------------------------------
# STEP 2 — VALENCE TRAJECTORIES (7 days, one float per day)
# These are the ground-truth valence values used to generate emoji/text inputs.
# Positive = happy/engaged. Negative = distressed. Threshold -0.3 = low day.
# ---------------------------------------------------------------------------

TRAJECTORIES = {
    # URGENT
    "S_004": [0.55, 0.50, 0.48, 0.52, 0.50, 0.45, -0.65],   # stable → crash day 7
    "S_017": [0.20, 0.10, -0.10, -0.40, -0.55, -0.65, -0.70], # slow descent → 4 consec low

    # ELEVATED
    "S_003": [0.10, 0.05, -0.05, -0.15, -0.25, -0.30, -0.40], # gradual decline
    "S_009": [0.40, 0.35, 0.30, 0.25, 0.20, -0.35, -0.40],    # dip days 6-7
    "S_012": [0.00, -0.15, -0.10, -0.20, -0.25, -0.20, -0.35],# persistent borderline low
    "S_018": [0.30, 0.25, 0.20, 0.15, -0.35, 0.10, -0.40],    # non-consecutive low days 5,7

    # ROUTINE
    "S_001": [0.70, 0.65, 0.72, 0.68, 0.75, 0.70, 0.68],
    "S_002": [-0.10, 0.05, 0.15, 0.25, 0.35, 0.40, 0.45],     # improving
    "S_005": [0.80, 0.75, 0.82, 0.78, 0.80, 0.75, 0.80],
    "S_006": [0.60, 0.65, 0.55, 0.60, 0.70, 0.65, 0.60],
    "S_007": [0.55, 0.60, 0.40, 0.55, 0.58, 0.62, 0.55],      # one neutral dip day 3
    "S_008": [0.50, 0.55, 0.52, 0.48, 0.53, 0.50, 0.55],
    "S_010": [0.10, 0.15, 0.25, 0.40, 0.45, 0.50, 0.55],      # improving
    "S_011": [0.60, 0.55, 0.70, 0.62, 0.80, 0.58, 0.65],      # all above +0.4 (false-pos test)
    "S_013": [0.45, 0.40, 0.50, 0.45, 0.48, 0.42, 0.46],
    "S_014": [0.65, 0.70, 0.35, 0.60, 0.65, 0.68, 0.70],      # dip day 3, recovers
    "S_015": [0.35, 0.38, 0.42, 0.40, 0.38, 0.42, 0.40],
    "S_016": [0.55, 0.50, 0.58, 0.52, 0.56, 0.54, 0.58],
    "S_019": [-0.05, 0.10, 0.25, 0.35, 0.40, 0.42, 0.45],     # anxious then improves
    "S_020": [0.75, 0.80, 0.78, 0.82, 0.75, 0.80, 0.78],
}

# ---------------------------------------------------------------------------
# EMOJI PALETTE  (mapped to approximate valence buckets)
# valence >=  0.5  → very positive
# valence  0.1–0.49 → mildly positive
# valence -0.1–0.09 → neutral
# valence -0.29–-0.11 → mild negative
# valence <= -0.3  → clearly negative / distress
# ---------------------------------------------------------------------------

EMOJI_MAP = {
    "very_positive":   ["😊😄🌟", "😄🌈😁", "😃🌟😊", "🥰😄✨", "😁🌟🎉"],
    "mild_positive":   ["🙂😌", "😊🙂", "😌🙂😊", "🙂🌤️", "😊😌"],
    "neutral":         ["😐🙂", "😑😐", "😶😐", "🙄😐", "😐"],
    "mild_negative":   ["😕😔", "😔🌧️", "😞😕", "😔😴", "😕😶"],
    "very_negative":   ["😢😴😠", "😭😔😶", "😢😠😴", "😶😢😠", "😴😢😔"],
}

def valence_to_emoji_bucket(v: float) -> str:
    if v >= 0.5:
        return "very_positive"
    elif v >= 0.1:
        return "mild_positive"
    elif v >= -0.1:
        return "neutral"
    elif v >= -0.3:
        return "mild_negative"
    else:
        return "very_negative"

def get_emoji(valence: float) -> str:
    bucket = valence_to_emoji_bucket(valence)
    return random.choice(EMOJI_MAP[bucket])

# ---------------------------------------------------------------------------
# SENIOR TEXT RESPONSES
# Keyed by (arc, day_index 0-6). Day 7 (index 6) for S_004 is the crisis day.
# ---------------------------------------------------------------------------

SENIOR_TEXTS = {
    # S_004 — stable days 1-6, pattern break day 7
    "S_004": [
        "Feeling pretty good today. Had a fun time at football practice.",
        "Pretty okay. A bit tired from studying but nothing major.",
        "Good day overall. Lunch with friends was nice.",
        "Feeling alright. A bit distracted in class but fine.",
        "Okay I guess. Nothing exciting. Same as usual.",
        "Kind of tired. Not much to say today.",
        "I don't really see the point of being here. I feel like nobody would notice if I just disappeared.",  # day 7: distress
    ],
    # S_003 — gradual decline
    "S_003": [
        "I'm doing okay, just a little tired.",
        "Feeling a bit flat today. Not sure why.",
        "Kind of meh. Hard to focus in class.",
        "Not great. Things at home have been a bit tense.",
        "Pretty low today. Don't really want to talk to anyone.",
        "Feeling pretty down. Didn't really eat much at lunch.",
        "Really struggling today. I've been avoiding my friends. It feels easier that way.",
    ],
    # S_012 — persistent low energy + mild withdrawal
    "S_012": [
        "Tired. Just kind of going through the motions.",
        "Okay I guess. Hard to get motivated to do anything.",
        "A bit better today but still pretty low on energy.",
        "Don't really feel like talking to people. Just keeping to myself.",
        "Still really tired. Didn't sleep well again.",
        "Feeling a bit better than yesterday but still not great.",
        "I've been spending a lot of time alone. Just easier that way.",
    ],
    # S_002 — improving arc
    "S_002": [
        "Feeling kind of anxious today, not sure about anything.",
        "A bit better. Had a good chat with my friend.",
        "Things are slowly getting better I think.",
        "Feeling more like myself today.",
        "Had a really good day! Finished my project early.",
        "Feeling pretty happy. Things are going well.",
        "Best week in a while honestly. Feeling positive.",
    ],
    # S_006 — stable positive
    "S_006": [
        "Great day! Got my essay back with a good grade.",
        "Really good. Excited about the school trip next week.",
        "Pretty solid day, nothing major but all good.",
        "Good. Had lunch with my whole friend group.",
        "Excellent! Passed my driving test theory exam.",
        "Good day overall. Feeling calm and ready for exams.",
        "Really positive. Feeling confident about everything.",
    ],
    # S_008 — stable
    "S_008": [
        "Doing well today. Feeling rested.",
        "Pretty normal day. All good.",
        "Good. Caught up with my cousin after school.",
        "Doing fine. A bit tired but okay.",
        "Pretty solid. Good lunch, good classes.",
        "Feeling good. Looking forward to the weekend.",
        "Really good. Had a fun group project today.",
    ],
    # S_013 — stable moderate
    "S_013": [
        "Okay day. Nothing special.",
        "Pretty good actually. Had fun in PE.",
        "Solid day. Finished my homework early.",
        "Alright. A bit bored in some classes.",
        "Pretty good. Feeling calm.",
        "Decent day overall.",
        "Good enough. Ready for the weekend.",
    ],
    # S_015 — stable mild positive
    "S_015": [
        "Feeling alright today.",
        "Pretty good. Made a new friend in art class.",
        "Okay day. Nothing bad happened.",
        "Decent. Feeling a bit tired but managing.",
        "Pretty solid day.",
        "Good. Looking forward to the holidays.",
        "Alright. All good.",
    ],
    # S_016 — stable
    "S_016": [
        "Good day! Really enjoyed French class.",
        "Pretty good. Hung out with my friend group.",
        "Solid. Nothing to complain about.",
        "Good. Feeling happy and relaxed.",
        "Really nice day. Sun was out at lunch.",
        "Pretty great. Drama rehearsal was fun.",
        "Good week overall. Feeling positive.",
    ],
    # S_019 — anxious then improving
    "S_019": [
        "Feeling kind of anxious about my exams. Not sure I'm ready.",
        "Still a bit worried but I've started studying more.",
        "Getting better. Studied with a friend which helped.",
        "Feeling more prepared now. Less anxious.",
        "Doing well. Got some good feedback on my practice exam.",
        "Feeling confident. I think I've got this.",
        "Really good. Feeling prepared and calm about everything.",
    ],
}

def get_senior_text(student_id: str, day_idx: int) -> str:
    if student_id in SENIOR_TEXTS:
        return SENIOR_TEXTS[student_id][day_idx]
    # Fallback for any routine senior not explicitly scripted
    valence = TRAJECTORIES[student_id][day_idx]
    if valence >= 0.4:
        return random.choice([
            "Really good day today. Feeling positive.",
            "Things are going well. Happy with how this week is going.",
            "Great day! Feeling energised and engaged.",
        ])
    elif valence >= 0.0:
        return random.choice([
            "Pretty okay day overall. Nothing to worry about.",
            "Decent day. Feeling alright.",
            "Normal day. All fine.",
        ])
    else:
        return random.choice([
            "Feeling a bit down today. Tired.",
            "Not my best day but managing.",
            "Kind of low energy. Hope tomorrow is better.",
        ])

# ---------------------------------------------------------------------------
# STEP 3 — GENERATE synthetic_checkins.json
# Format: list of raw check_in records matching SPEC.md Section 4.1 input schema
# Note: teacher_observation is NOT embedded here — it comes via MCP separately.
# ---------------------------------------------------------------------------

PROMPT_TEXT = "How are you feeling today and why?"

def build_checkins():
    checkins = []
    for student in REGISTRY_DESIGN:
        sid = student["student_id"]
        ag  = student["age_group"]
        for day_idx, date in enumerate(DATES):
            valence = TRAJECTORIES[sid][day_idx]
            record = {
                "student_id": sid,
                "student_name": student["fictional_name"],  # raw PII — Privacy Guard will strip
                "age_group": ag,
                "date": date,
            }
            if ag == "junior":
                record["junior_input"] = {
                    "emoji_sequence": get_emoji(valence)
                }
            else:
                record["senior_input"] = {
                    "prompt": PROMPT_TEXT,
                    "response": get_senior_text(sid, day_idx)
                }
            checkins.append(record)
    return checkins

# ---------------------------------------------------------------------------
# STEP 4 — GENERATE teacher_observations.json
# ~18 notes across 7 days (average 2-3/day).
# Some contain real fictional names (PII guard test cases).
# Designed to reinforce or add signal for the flagged students.
# ---------------------------------------------------------------------------

TEACHER_OBS_DESIGN = [
    # Day 1 (June 22)
    {
        "date": "2026-06-22",
        "student_id": "S_017",
        "teacher_id": "T_01",
        "note": "Lily seemed quieter than usual during morning circle. Didn't participate in group activity.",
        "flag_level": "watch"
    },
    {
        "date": "2026-06-22",
        "student_id": "S_003",
        "teacher_id": "T_02",
        "note": "Aisha appeared a bit withdrawn during group work today. Said she was tired.",
        "flag_level": "watch"
    },
    # Day 2 (June 23)
    {
        "date": "2026-06-23",
        "student_id": "S_009",
        "teacher_id": "T_01",
        "note": "Marcus was energetic and engaged today. No concerns.",
        "flag_level": "none"
    },
    {
        "date": "2026-06-23",
        "student_id": "S_012",
        "teacher_id": "T_03",
        "note": "Priya has been sitting alone at lunch for the past few days. Worth monitoring.",
        "flag_level": "watch"
    },
    # Day 3 (June 24)
    {
        "date": "2026-06-24",
        "student_id": "S_017",
        "teacher_id": "T_01",
        "note": "Lily was very quiet again. Did not speak during reading time. Seems down.",
        "flag_level": "watch"
    },
    {
        "date": "2026-06-24",
        "student_id": "S_004",
        "teacher_id": "T_02",
        "note": "Rohan participated well in class discussion. All seems fine.",
        "flag_level": "none"
    },
    # Day 4 (June 25)
    {
        "date": "2026-06-25",
        "student_id": "S_003",
        "teacher_id": "T_03",
        "note": "Aisha barely ate at lunch today. Seemed very distracted and didn't make eye contact.",
        "flag_level": "concern"
    },
    {
        "date": "2026-06-25",
        "student_id": "S_011",
        "teacher_id": "T_01",
        "note": "Kezia had a great day — really engaged in the science project.",
        "flag_level": "none"
    },
    # Day 5 (June 26)
    {
        "date": "2026-06-26",
        "student_id": "S_017",
        "teacher_id": "T_01",
        "note": "Lily has been crying at the start of class. Wouldn't say why. Flagging for counsellor attention.",
        "flag_level": "concern"
    },
    {
        "date": "2026-06-26",
        "student_id": "S_018",
        "teacher_id": "T_02",
        "note": "Zara seemed upset today. Said her friend group has been leaving her out.",
        "flag_level": "watch"
    },
    {
        "date": "2026-06-26",
        "student_id": "S_012",
        "teacher_id": "T_03",
        "note": "Priya has not been participating. Seems low energy. Her friend mentioned she hasn't been eating much.",
        "flag_level": "concern"
    },
    # Day 6 (June 27)
    {
        "date": "2026-06-27",
        "student_id": "S_009",
        "teacher_id": "T_01",
        "note": "Marcus seemed really down today. Out of character for him. Crying briefly during quiet reading.",
        "flag_level": "concern"
    },
    {
        "date": "2026-06-27",
        "student_id": "S_004",
        "teacher_id": "T_02",
        "note": "Rohan seemed a bit quieter than usual but nothing alarming.",
        "flag_level": "none"
    },
    {
        "date": "2026-06-27",
        "student_id": "S_003",
        "teacher_id": "T_03",
        "note": "Aisha left class early saying she felt unwell. This is the third time this week.",
        "flag_level": "concern"
    },
    # Day 7 (June 28) — crisis day for S_004
    {
        "date": "2026-06-28",
        "student_id": "S_004",
        "teacher_id": "T_02",
        "note": "Rohan sat alone all of lunch and did not speak to anyone. He seemed visibly distressed. Very out of character — he is normally very social. Flagging as urgent concern.",
        "flag_level": "concern"
    },
    {
        "date": "2026-06-28",
        "student_id": "S_017",
        "teacher_id": "T_01",
        "note": "Lily refused to come in from the playground this morning. Has been crying consistently. Maya, her usual friend, mentioned Lily told her she doesn't want to come to school anymore.",
        "flag_level": "concern"
    },
    {
        "date": "2026-06-28",
        "student_id": "S_009",
        "teacher_id": "T_01",
        "note": "Marcus is still very down. Second day in a row. Recommend counsellor check-in.",
        "flag_level": "concern"
    },
    {
        "date": "2026-06-28",
        "student_id": "S_018",
        "teacher_id": "T_03",
        "note": "Zara was tearful again today. Social exclusion from friend group ongoing.",
        "flag_level": "watch"
    },
]

# ---------------------------------------------------------------------------
# STEP 5 — STUDENT REGISTRY (output version, arc labels preserved as metadata)
# ---------------------------------------------------------------------------

def build_registry():
    registry = []
    for s in REGISTRY_DESIGN:
        registry.append({
            "student_id": s["student_id"],
            "fictional_name": s["fictional_name"],
            "age_group": s["age_group"],
            "age": s["age"],
            "_arc_label": s["arc"],          # metadata only — underscore prefix signals non-agent field
            "_arc_note": s["arc_note"],
        })
    return registry

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    registry = build_registry()
    checkins = build_checkins()
    teacher_obs = TEACHER_OBS_DESIGN

    # Write files
    registry_path = OUTPUT_DIR / "student_registry.json"
    checkins_path = OUTPUT_DIR / "synthetic_checkins.json"
    obs_path      = OUTPUT_DIR / "teacher_observations.json"

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    with open(checkins_path, "w", encoding="utf-8") as f:
        json.dump(checkins, f, indent=2, ensure_ascii=False)

    with open(obs_path, "w", encoding="utf-8") as f:
        json.dump(teacher_obs, f, indent=2, ensure_ascii=False)

    # Summary report
    print("=" * 62)
    print("  Mental Health First Responder Agent — Dataset Generated")
    print("=" * 62)
    print(f"\n  student_registry.json   → {len(registry)} students")
    print(f"  synthetic_checkins.json → {len(checkins)} records "
          f"({len(REGISTRY_DESIGN)} students × {len(DATES)} days)")
    print(f"  teacher_observations.json → {len(teacher_obs)} notes")

    print("\n  Distribution:")
    arc_counts = {}
    for s in REGISTRY_DESIGN:
        bucket = s["arc"].split("_")[0]
        arc_counts[bucket] = arc_counts.get(bucket, 0) + 1
    for bucket, count in sorted(arc_counts.items()):
        print(f"    {bucket:10s} : {count} students")

    print("\n  Temporal spine: 2026-06-22 → 2026-06-28 (7 days)")
    print("  Pipeline 'today': 2026-06-29")

    print("\n  Key test cases:")
    print("    S_004  urgent   — pattern break on day 7 (senior, distress keyword)")
    print("    S_017  urgent   — 4 consecutive low days (junior)")
    print("    S_003  elevated — gradual 7-day decline (senior)")
    print("    S_009  elevated — 2 consecutive low days at end (junior)")
    print("    S_012  elevated — persistent low energy + withdrawal (senior)")
    print("    S_018  elevated — non-consecutive lows, declining trajectory (junior)")
    print("    S_011  routine  — false-positive test: all 7 days above +0.4")
    print("\n  Files written to:", OUTPUT_DIR.resolve())
    print("=" * 62)

if __name__ == "__main__":
    main()
