# Emoji → Affect Lookup Table

Used by `emotional-signal-reader` for junior (ages 6–10) check-in parsing.
Each entry maps an emoji to a `(valence_delta, energy_delta)` pair.

Format: `emoji | valence_delta | energy_delta | notes`

Valence range: -1.0 (very negative) to +1.0 (very positive)  
Energy range: 0.0 (exhausted) to 1.0 (high energy)

---

## Negative affect

| Emoji | Valence | Energy | Notes |
|-------|---------|--------|-------|
| 😢 | -0.8 | 0.2 | Crying — strong sadness |
| 😭 | -0.9 | 0.3 | Sobbing — extreme sadness |
| 😔 | -0.6 | 0.3 | Pensive / sad |
| 😞 | -0.6 | 0.2 | Disappointed |
| 😠 | -0.5 | 0.7 | Angry — negative but high energy |
| 😡 | -0.7 | 0.8 | Very angry |
| 😤 | -0.4 | 0.6 | Frustrated |
| 😰 | -0.7 | 0.5 | Anxious / stressed |
| 😨 | -0.7 | 0.4 | Fearful |
| 😱 | -0.8 | 0.6 | Terrified |
| 🤒 | -0.4 | 0.1 | Sick / unwell |
| 🤢 | -0.5 | 0.1 | Nauseous |
| 😴 | -0.2 | 0.0 | Sleepy / exhausted |
| 🥱 | -0.1 | 0.1 | Bored / tired |
| 😑 | -0.3 | 0.1 | Expressionless / flat |
| 😶 | -0.4 | 0.2 | Silent / withdrawn (sets social_withdrawal_flag) |
| 😶‍🌫️ | -0.5 | 0.2 | Dissociated / foggy (sets social_withdrawal_flag) |
| 🙈 | -0.3 | 0.2 | Hiding / avoidant (sets social_withdrawal_flag) |
| 💔 | -0.8 | 0.2 | Heartbroken |
| 🖤 | -0.5 | 0.2 | Grief / loss |

---

## Positive affect

| Emoji | Valence | Energy | Notes |
|-------|---------|--------|-------|
| 😊 | +0.7 | 0.6 | Happy / content |
| 😄 | +0.8 | 0.8 | Very happy / excited |
| 😁 | +0.8 | 0.9 | Beaming |
| 🥰 | +0.9 | 0.7 | Loved / warm |
| 😍 | +0.8 | 0.7 | Delighted |
| 🤩 | +0.9 | 0.9 | Excited / thrilled |
| 😎 | +0.6 | 0.7 | Confident / cool |
| 🙂 | +0.4 | 0.5 | Mildly positive |
| 😌 | +0.5 | 0.4 | Relieved / calm |
| 🤗 | +0.7 | 0.7 | Warm / huggy |
| ✨ | +0.6 | 0.7 | Sparkling / positive energy |
| 🌟 | +0.7 | 0.8 | Bright / great day |
| ❤️ | +0.8 | 0.6 | Love / strong positive |
| 💪 | +0.6 | 0.9 | Strong / energised |
| 🎉 | +0.8 | 0.9 | Celebratory |

---

## Neutral / ambiguous

| Emoji | Valence | Energy | Notes |
|-------|---------|--------|-------|
| 😐 | 0.0 | 0.3 | Neutral — no strong signal |
| 🤔 | 0.0 | 0.5 | Thinking / uncertain |
| 😅 | -0.1 | 0.5 | Nervous laughter — slight negative |
| 😬 | -0.2 | 0.4 | Awkward / uncomfortable |
| 🙃 | -0.1 | 0.4 | Sarcastic / ambivalent |

---

## Unknown emoji handling

If an emoji does not appear in this table:
- Assign `valence_delta = 0.0`, `energy_delta = 0.3` (neutral defaults)
- Log the unknown emoji for table expansion
- Do not raise an error — continue processing remaining sequence

---

## Social withdrawal flag triggers

Set `social_withdrawal_flag = true` if ANY of the following appear in the sequence:

`😶` `😶‍🌫️` `🙈` `💔` (combined with other negative emojis) `🖤` (combined with 2+ negative emojis)

Single occurrence of `💔` or `🖤` alone does NOT set the flag — requires corroborating negative context.
