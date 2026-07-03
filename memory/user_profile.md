---
name: user-profile
description: Cruz — prop trader building NQ/ES automated strategies for funded account trading
metadata:
  type: user
---

# Cruz — User Profile

## Role
Prop trader running a systematic intraday trading operation. Currently trading NQ futures on a Lucid Trading 50K Direct funded account. Building toward a portfolio of uncorrelated strategies stacked on the same account.

## Goals
- Short term: validate ES ORB and NQ VWAP on sim before going live
- Long term: stack multiple complementary edges on one account — strategies that promote each other (different instruments, different time windows, uncorrelated drawdowns)
- The more edges that can run simultaneously without interfering, the better

## Approach
- Wants data-driven decisions — runs backtests before deploying anything
- Prefers honest negative results over false positives (was receptive when new entry research showed no improvement)
- Thinks in terms of OOS walk-forward validation, profit factor, and Monte Carlo stress tests
- Separates strategy accounts for clean live performance tracking

## Infrastructure
- Windows VPS (Contabo, 144.126.140.221) running NinjaTrader 8 24/7 — VirtIO driver issue pending fix
- Mac for research/Python backtesting
- NinjaTrader 8 with Tradovate connection (Simulation mode for Lucid Direct funded)
- Three accounts: Lucid funded (NQ ORB live), Sim101 (ES ORB), Sim (NQ VWAP)

## Communication style
- Casual, direct — no fluff
- Wants charts for visual results
- Iterates quickly — multiple research cycles in one session
