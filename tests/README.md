# Test Suite

This repository now uses `pytest` as the default test runner.

## Test tiers

- `tests/test_p25b85_adapter.py`: protocol + adapter unit tests that run without Home Assistant runtime.
- `tests/test_frame_protocol.py`: frame parser regression tests for analysis tooling.
- `tests/test_fan_entity_runtime.py`: optional HA-runtime regression checks for fan features and power actions.
- `tests/test_entities_runtime.py`: optional HA-runtime tests for sensor, binary sensor, switch, fan, and climate entity logic.

## Quick start

```zsh
cd /Users/alex/IdeaProjects/alexbde/ha-joyonway-p25b85
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[test]"
pytest -q
```

## Optional Home Assistant runtime tests

When you add Home Assistant integration tests (entity lifecycle, services, config flow), install:

```zsh
python -m pip install -e ".[ha-test]"
```

If your local Python is not compatible with current Home Assistant wheels, create a second venv with a supported Python interpreter and run tests there.

`test_fan_entity_runtime.py` auto-skips when `homeassistant` is not installed.

