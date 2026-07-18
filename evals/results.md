# StayCovered eval results

Generated 2026-07-18 · 10 synthetic renewal cases · all numbers from
REAL API runs (cached in `examples/outputs/`, tool traces included). Decision accuracy is scored on
the model's RAW decision, before the code policy layer can rescue it. Budget note: the expensive
teacher arm runs as a 3-case SPOT-CHECK of the quality ceiling rather than the full suite —
sampling the ceiling instead of paying for it everywhere. Screening (exemptions, hours math,
deadlines, evidence) is deterministic code and identical across arms.

| Arm | Model | Decision acc. | Must-mention coverage | Citation groundedness | Referral acc. | Tool discipline | FK grade (target ≤ 7) | Total cost |
|---|---|---|---|---|---|---|---|---|
| worker | claude-haiku-4-5-20251001 | 9/10 (90%) | 30/30 (100%) | 34/35 (97%) | 9/10 | 10/10 | 5.1 | $0.1324 |
| worker+skill | claude-haiku-4-5-20251001 | 9/10 (90%) | 29/30 (97%) | 34/34 (100%) | 9/10 | 10/10 | 5.1 | $0.1632 |
| teacher+skill (spot-check, 2 cases) | claude-fable-5 | 2/2 (100%) | 6/6 (100%) | 12/12 (100%) | 2/2 | 2/2 | 4.7 | $0.4704 |

**Reading the table:** "worker" is Haiku 4.5 with tools; "worker+skill" adds the skill pack
authored by Claude Fable 5 (`skills/renewal-prep.md`); "teacher+skill" is Fable 5 itself, as the
quality ceiling. The FK grade targets ≤ 7 (member letters for a general-literacy audience).
Prices: Haiku 4.5 $1/$5 per 1M tokens; Fable 5 $10/$50 per 1M tokens (list, July 2026).
