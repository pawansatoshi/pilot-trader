# Project status

| Stage | Status |
|---|---|
| 1 Risk/execution foundation | complete, preserved |
| 2 AI review / outcome logging | complete, preserved |
| **2.5 Binance Demo validation** | **BLOCKED / NOT RUN.** No Demo credentials or network access in the development sandbox. All 24 items are NOT RUN. Nothing is claimed as PASS. Tools: `tools/testnet_validate.py`, `tools/demo_scenarios.py`; blank report: `docs/testnet_report.TEMPLATE.json` |
| 3A Autonomous workflow / backend | implemented, tested with fake exchanges only (see `docs/STAGE3A.md`) |
| 3B Terminal UI | implemented (`docs/STAGE3B.md`). Python + JS tests with fake data sources; never opened in a real browser; Binance WebSocket never connected |
| 4 Advanced AI layer | not started |
| 5 to 8 | not started |

Real trading is NOT unlocked: it needs `ALLOW_REAL=1`, Binance keys on the worker, a typed REAL confirmation, and (by project decision) a finished Stage 2.5.
Everything marked "tested" in this repository was tested against fake exchanges. None of it is exchange validation.
