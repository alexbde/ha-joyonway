# Live Tests

This folder contains **manual, hardware-dependent** test runners.

These scripts are intentionally not named `test_*.py`, so they are not picked
up by `pytest` in normal CI/unit test runs.

Naming convention: use `livetest_*.py` for runnable scripts in this folder.

## Schedule Matrix Runner

`livetest_schedule_ui_matrix.py`

- Validates schedule behavior as triggered from HA UI:
  - state toggles (slot enable/disable)
  - single-field time edits
- Covers all 4 slot-enable combinations for both heat and filter schedules
- Includes retries, convergence waits, and robust restore
- Requires a running spa and RS485 bridge access

Run:

```zsh
cd /Users/alex/IdeaProjects/alexbde/ha-joyonway-p25b85
source .venv/bin/activate
python tests/live/livetest_schedule_ui_matrix.py
```



