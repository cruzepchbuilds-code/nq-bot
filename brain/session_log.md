# CruzCapital NQ Bot — Live Session Log

_Log every live trading session. One entry per trading day._

---

## Template (copy for each session)

```
## [DATE] — [Day of Week]

### Pre-Session
- Account balance: $
- Drawdown from peak: %
- Consecutive losses this week:
- Eval mode: Yes / No
- STRONG/WEAK month:
- Expected OR range (based on VIX/ATR):

### Setup Quality Checklist
- [ ] Gap direction clear (> 20pts): Yes / No — Direction:
- [ ] Monday skip rule: N/A / Applied
- [ ] OR size in 55-130pt range: Yes / No — Size:
- [ ] Volume confirmation on breakout: Yes / No
- [ ] Signal score >= 60: Yes / No — Score:
- [ ] Entry time before 11:15: Yes / No — Time:

### Trade Log
| Time | Dir | Entry | Stop | Target | Exit | P&L | Score | Notes |
|------|-----|-------|------|--------|------|-----|-------|-------|
|      |     |       |      |        |      |     |       |       |

### Post-Session
- Session P&L: $
- Account balance: $
- New drawdown: %
- Consecutive losses:
- Consecutive wins:

### Notes
- What the market did:
- Why trade triggered:
- What I learned:
- Anything to review in brain/insights.md:
```

---

## Completed Sessions

<!-- Add completed sessions below this line -->

---

## Stats Summary (update weekly)

| Week | Trades | WR | P&L | Max DD | Notes |
|------|--------|----|-----|--------|-------|
|      |        |    |     |        |       |

---

## Quick Reference: Red Flags (from brain/insights.md)

| Condition | WR | Action |
|-----------|-----|--------|
| Entry time 10:30-11:00 | ~12% | SKIP — wait for next day |
| September trades | 25% | Size to min (1 contract only) |
| 4+ consecutive losses | 32% | Already handled by risk rules |
| Gap 40-60pt | 32.5% | Extra caution, confirm volume |
| Trades after 10:30 | 12.5% | LAST_ENTRY_TIME may need tightening |

## Quick Reference: Green Lights

| Condition | WR | Note |
|-----------|-----|------|
| Entry 9:45-10:00 | 45% | Best window — maximize conviction |
| October / November | 52% | Historically strongest months |
| Score 60-69 | 51.7% | Lower scores can still win (already 1c) |
| After 2-3 losses | 46-50% | Mean-reversion edge — trust the system |
