# Priority 6: Polish & Release — Implementation Plan

> **Goal:** Finalize the integration for its first public HACS release as
> version `1.0.0`.
> Address remaining verification items, code quality,
> documentation, and HACS compliance.

## 1. Scope

### Phase A — Live verification (release-gated + optional)
Complete the remaining untested paths before tagging a release.

Release gate policy for `1.0.0`:
- **Required to release:** reconnect behavior, optimistic snap-back, grace-mode,
  intent queue coalescing, and auto clock sync.
- **Optional with documented exception:** ozone live verification items, because
  ozone is already mode-gated and can remain explicitly documented as limited.

### Phase B — Code polish & cleanup
Remove dead code, tighten typing, improve log messages, ensure consistency.

### Phase C — HACS compliance & packaging
Ensure the integration installs cleanly via HACS custom repository and
passes all HACS validation checks.

### Phase D — Version bump & tag
Bump version, tag release, update README status.

---

## 2. Pre-Release Verification Checklist

| Item | Status | Notes |
|------|--------|-------|
| Ozone switch live test (Manual mode) | ⬜ TODO | Toggle ozone via HA, confirm byte 13 state change on bus |
| Ozone mode switch via options flow | ⬜ TODO | Change Auto↔Manual in options, verify spa broadcast reflects new mode |
| Auto clock sync (drift-triggered) | ⬜ TODO | Advance spa clock >30s, confirm HA syncs after cooldown |
| Reconnect after bridge power-cycle | ⬜ TODO | Unplug EW11, verify graceful reconnect + entity availability behavior |
| Optimistic snap-back on failed write | ⬜ TODO | Disconnect bridge mid-write, verify entity reverts + warning log |
| Grace-mode availability (10s window) | ⬜ TODO | Brief disconnect (<10s), confirm entities stay available |
| Intent queue coalescing (rapid clicks) | ⬜ TODO | Rapid toggle switch, verify single command on bus |

Results should be documented in `docs/live_test_plan.md` or a new
`docs/release_verification.md` artifact.

### Exception handling (if any gate cannot be completed)

Any exception must be explicitly recorded in `docs/release_verification.md`
with all of the following:
- Affected checklist item(s)
- Why it is blocked (environment/hardware/defect)
- User impact assessment and mitigation
- Approval from maintainer (`@alexbde`)
- Follow-up issue link and target milestone

---

## 3. Code Polish Tasks

### 3.1 Remove dead code & unused imports

Audit all `.py` files for:
- Unused imports (run `ruff check --select F401`)
- Dead functions/methods that are no longer called
- Commented-out code blocks
- TODO/FIXME comments that are already resolved

### 3.2 Type annotations

- Add return type annotations to all public methods missing them
- Add `__all__` exports to modules with public API (`protocol.py`,
  `coordinator.py`, `adapters/base.py`)
- Ensure `mypy --strict` produces no new errors on core modules
  (`protocol.py`, `coordinator.py`, `entity.py`)

### 3.3 Docstrings

- Add module-level docstrings to all `.py` files in the integration
- Add class-level docstrings to all entity classes and the coordinator
- Ensure all public methods have at minimum a one-line docstring

### 3.4 Logging consistency

- Audit all `_LOGGER` calls for consistent format:
  `"Entity: action"` or `"Component: action"` prefix
- Ensure no PII (IP addresses) logged at INFO or below
- Verify all error paths log at WARNING or ERROR (not just debug)
- Confirm reconnect logs use appropriate levels (INFO on connect,
  WARNING on disconnect, DEBUG for retry attempts)

### 3.5 Constants review

- Verify all magic numbers in entity files reference named constants
  from `const.py` or `adapters/p25b85.py`
- Consolidate any remaining inline timing values (timeouts, cooldowns)
  into `const.py`

---

## 4. HACS Compliance & Packaging

### 4.1 Run HACS validation

```bash
# Pull and resolve digest once, then run pinned image for reproducible validation
docker pull ghcr.io/hacs/action:main
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/hacs/action:main
docker run --rm -v "$PWD":/github/workspace ghcr.io/hacs/action@sha256:<resolved_digest>
```

Prefer replacing `<resolved_digest>` with the exact digest captured for this
release cycle and recording it in `docs/release_verification.md`.

Expected checks:
- `manifest.json` has all required fields
- `hacs.json` is valid
- Directory structure matches `content_in_root: false`
- No `requirements` referencing unavailable packages
- Version string in `manifest.json` is valid semver

### 4.2 Manifest updates

| Field | Current | Target |
|-------|---------|--------|
| `version` | `0.1.0` | `1.0.0` |
| `iot_class` | `local_polling` | `local_push` (persistent TCP, no polling) |

The `iot_class` should reflect that we maintain a persistent TCP connection
and receive push updates from the spa broadcast stream. `local_push` is
more accurate than `local_polling` (the 60s stale-RX check is a health
monitor, not a data poll).

### 4.3 `hacs.json` review

Current content is minimal and correct. Optionally add:
```json
{
  "name": "Joyonway P25B85 Spa",
  "render_readme": true,
  "content_in_root": false,
  "homeassistant": "2024.1.0"
}
```

Adding `homeassistant` minimum version makes HACS show compatibility info.

### 4.4 Icons

Verify `icon.png` (256×256) and `icon@2x.png` (512×512) exist at the
correct paths for HACS brand display:
- `custom_components/joyonway_p25b85/icon.png`
- `custom_components/joyonway_p25b85/icon@2x.png`

### 4.5 Translations completeness

Verify all entity keys present in `strings.json` are also present in:
- `translations/en.json`
- `translations/de.json`
- `translations/fr.json`

Run a diff check or write a quick script to ensure parity.

---

## 5. README & Documentation Updates

### 5.1 README changes for release

- Change status badge from "Pre-release / testing" to "Stable" (`1.0.0`)
- Remove "❌ Ozone control not yet live-tested" once verified
- Add changelog section or link to GitHub releases page
- Verify all entity tables match current implementation
- Add "Upgrading" section (for users going from 0.1.0 → 1.0.0, if any
  breaking changes exist — currently none expected)

### 5.2 CHANGELOG.md (new file)

Create `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/)
format:

```markdown
# Changelog

## [1.0.0] — YYYY-MM-DD

### Added
- Persistent TCP connection with automatic reconnect
- Optimistic UI state for all writable entities
- Intent queue for command serialization and coalescing
- Grace-mode entity availability (10s tolerance for brief disconnects)
- Heat and filter schedule time entities (read + write)
- Schedule slot enable/disable switches
- Clock sync button with optional auto-sync
- Ozone switch (Manual mode)
- Fan entity for jets (off/low/high preset modes)
- German and French translations
- Diagnostic entities (bridge connectivity, spa clock)

### Changed
- All commands now built dynamically via CRC-32 (no replay tables)
- Improved status detection (standby, pre/post-heat circulation)
- Schedule writes split into state-mode and time-mode flags

### Fixed
- Schedule slot 2 time writes when slot 2 is disabled
- Optimistic state snap-back only after broadcast confirmation
- Light toggle-lock prevents double-toggle reversion

## [0.1.0] — Initial development release

### Added
- Basic entity scaffolding and config flow
- RS485 protocol framing and CRC implementation
- Temperature, heater, light, pump sensors
```

---

## 6. Version Bump & Release Steps

### Step 1: Final test run

```bash
source .venv/bin/activate
ruff check .
mypy --strict custom_components/joyonway_p25b85/protocol.py custom_components/joyonway_p25b85/coordinator.py custom_components/joyonway_p25b85/entity.py
pytest -q
# Expect: 0 failed
```

### Step 2: Bump version

| File | Field | Old | New |
|------|-------|-----|-----|
| `custom_components/joyonway_p25b85/manifest.json` | `version` | `0.1.0` | `1.0.0` |
| `pyproject.toml` | `version` | (if present) | `1.0.0` |

### Step 3: Commit & tag

Use plain semver tag name without a leading `v`: `1.0.0`.

```bash
git add custom_components/joyonway_p25b85/manifest.json
git add pyproject.toml
git add hacs.json
git add README.md
git add CHANGELOG.md
git add docs/plan.md
[ -f docs/release_verification.md ] && git add docs/release_verification.md
git add -p custom_components/joyonway_p25b85
git commit -m "release: 1.0.0

- Persistent TCP connection + optimistic UI
- Intent queue for command serialization
- Schedule slot 2 fix (state/time flag split)
- Full CRC-32 dynamic command generation
- Heat/filter schedule entities
- Clock sync + auto-sync option
- Ozone manual control
- German + French translations"

git tag -a 1.0.0 -m "1.0.0 — First public release"
git push origin main --tags
```

### Step 4: GitHub release

Create a GitHub release from the `1.0.0` tag with the changelog content.
This makes the release visible to HACS users who add the custom repository.

### Step 5: Post-release

- Verify HACS can discover and install the release
- Post update to the [community thread](https://community.home-assistant.io/t/joyonway-spa-control/582344/)
- Update `docs/plan.md` Phase 20 status to ✅ Done

---

## 7. Files Modified

| File | Change |
|------|--------|
| `custom_components/joyonway_p25b85/manifest.json` | Version bump to `1.0.0` + iot_class |
| `hacs.json` | Add homeassistant minimum version |
| `README.md` | Status update, remove untested notes, add changelog link |
| `CHANGELOG.md` | New file |
| `pyproject.toml` | Version bump (if versioned) |
| `docs/plan.md` | Mark Phase 20 complete |
| Various `.py` files | Dead code removal, typing, docstrings, logging |

---

## 8. Risk Assessment

- **Risk: Breaking change on iot_class update.** Changing from
  `local_polling` to `local_push` has no runtime effect — it's metadata
  only. No user impact.
- **Risk: Version bump confusion.** Users on 0.1.0 (if any) will see an
  update to 1.0.0. Since entity IDs and config schema are unchanged,
  upgrade is expected to be seamless.
- **Risk: Ozone live test failure.** If ozone does not work as expected,
  the switch can be kept behind the options-flow gate (Manual mode only)
  with a note in README and a documented exception. For `1.0.0`, this is
  release-optional while mode-gated and clearly documented.

---

## 9. Estimated Effort

| Task | Estimate |
|------|----------|
| Phase A: Live verification (7 items) | 45 min |
| Phase B: Code polish (5 subtasks) | 60 min |
| Phase C: HACS compliance | 20 min |
| Phase D: Version bump + tag + release | 15 min |
| Documentation (README, CHANGELOG) | 20 min |
| **Total** | **~2.5 hours** |

---

## 10. Definition of Done

- [ ] All **required** Phase A verification items pass
- [ ] Any optional/blocked item has a documented exception entry with
      maintainer approval
- [ ] `ruff check`, `mypy --strict` (core modules), and `pytest` pass cleanly
- [ ] HACS validation passes
- [ ] `manifest.json` version = `1.0.0`, iot_class = `local_push`
- [ ] `CHANGELOG.md` exists with 1.0.0 entry and final release date
- [ ] README status updated, no stale "untested" notes (unless genuinely still untested)
- [ ] Git tag `1.0.0` pushed
- [ ] GitHub release created
- [ ] HACS custom repo install verified on a clean HA instance (or dev container)

