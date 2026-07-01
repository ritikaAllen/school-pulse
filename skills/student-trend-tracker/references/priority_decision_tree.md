# Priority Decision Tree

Used by `student-trend-tracker` in Step 7 to determine `recommended_priority`.
Rules are evaluated top-to-bottom. First match wins.

---

```
START
  │
  ├─ consecutive_low_days >= 3?
  │     YES → recommended_priority = URGENT
  │            trend_direction = crisis_watch
  │            STOP
  │
  ├─ pattern_break_detected = true AND emotional_valence < -0.3?
  │     YES → recommended_priority = URGENT
  │            trend_direction = crisis_watch
  │            STOP
  │
  ├─ social_withdrawal_flag = true AND consecutive_low_days >= 2?
  │     YES → recommended_priority = URGENT
  │            trend_direction = crisis_watch
  │            STOP
  │
  ├─ consecutive_low_days == 2?
  │     YES → recommended_priority = ELEVATED
  │            trend_direction = declining (if monotonic) or stable
  │            STOP
  │
  ├─ pattern_break_detected = true AND emotional_valence >= -0.3?
  │     YES → recommended_priority = ELEVATED
  │            trend_direction = declining
  │            STOP
  │
  ├─ trend_direction == declining AND delta_from_baseline > 0.2?
  │     YES → recommended_priority = ELEVATED
  │            STOP
  │
  └─ ALL OTHER CASES
        → recommended_priority = ROUTINE
           trend_direction = stable | improving
           STOP
```

---

## Key thresholds at a glance

| Threshold | Value | Used in |
|-----------|-------|---------|
| Low valence boundary | < -0.3 | consecutive_low_days counter |
| Pattern break delta | > 0.4 drop from baseline | pattern_break_detected |
| Crisis watch trigger | 3+ consecutive low days | trend_direction, priority |
| Elevated watch trigger | 2 consecutive low days | priority |
| Declining trend delta | > 0.2 drop from baseline (without break) | priority |

---

## Edge cases

**No baseline yet (< 3 days history):**
- `pattern_break_detected` is always `false`
- `delta_from_baseline` is `0.0`
- Priority is still computable from `consecutive_low_days` alone

**Exactly 3 days of history:**
- Baseline is valid; pattern break detection is active
- Trend direction computed from all 3 days

**Student returns after absence (gap in dates):**
- Do not carry forward `consecutive_low_days` across a gap > 1 day
- Reset `consecutive_low_days = 0` on the first entry after a gap
- Log the gap date range for the Orchestrator's awareness
