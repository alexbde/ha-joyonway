# Review: 1.2.0-rc1 Implementation vs. docs/plan.md

Comparing the actual diff `1.1.0..1.2.0-rc1` (4 commits, 25 files changed, +1720/−883 lines) against the plan.

## Summary Verdict

The implementation is **faithful to the plan**. All major design decisions were followed. There are two intentional deviations (improvements over the plan) and two gaps worth noting.

## Plan Item Checklist

| # | Plan Item | Status | Notes |
|---|-----------|--------|-------|
| 1 | `SIGNATURE_MODEL_MAP` constant | ✅ Done | [config_flow.py:41-45](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L41-L45) — exact match to plan |
| 2 | `_detect_model` returns `str \| ""  \| None` tri-state | ✅ Done | [config_flow.py:65-107](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L65-L107) |
| 3 | `_model_schema()` shared helper | ✅ Done | [config_flow.py:48-62](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L48-L62) — reused by all 3 steps |
| 4 | Two-step config flow (user → model confirm) | ✅ Done | [config_flow.py:121-166](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L121-L166) |
| 5 | Reconfigure flow via `async_update_reload_and_abort` | ✅ Done | [config_flow.py:195-215](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L195-L215) |
| 6 | No changes to `__init__.py` | ✅ Done | Zero diff |
| 7 | SelectSelector imports | ✅ Done | [config_flow.py:16-20](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L16-L20) |
| 8 | Dynamic dropdown from `ADAPTERS` registry | ✅ Done | `sorted(ADAPTERS.keys())` at line 50 |
| 9 | Adapter rename `p25b85.py` → `p25.py` | ✅ Done | File exists as `adapters/p25.py` |
| 10 | Adapter rename `p23b32.py` → `p23.py` | ✅ Done | File exists as `adapters/p23.py` |
| 11 | `P25BaseAdapter` with `_context_byte` sentinel | ✅ Done | [p25.py:179-587](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py#L179-L587) — uses `ClassVar[int]`, resolved at call time |
| 12 | `P25B85Adapter(P25BaseAdapter)` (~10 lines) | ✅ Done | [p25.py:589-597](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py#L589-L597) — 9 lines, model + context + light |
| 13 | `P25B37Adapter(P25BaseAdapter)` (~15 lines) | ✅ Done | [p25.py:600-612](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py#L600-L612) — 13 lines, discrete ON/OFF light |
| 14 | `tail_byte` parameter on `_build_button_command` | ✅ Done | [p25.py:356](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py#L356) — `tail_byte: int = 0x00` |
| 15 | `build_light_command` abstract on base | ✅ Done | [p25.py:386-388](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py#L386-L388) — raises `NotImplementedError` |
| 16 | ADAPTERS registry includes P25B37 | ✅ Done | [\_\_init\_\_.py:12](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/__init__.py#L12) |
| 17 | Translation updates (all 4 files) | ✅ Done | `model_confirm`, `model_confirm_manual`, `reconfigure` steps present in all |
| 18 | `reconfigure_successful` abort reason | ✅ Done | Present in all translation files |
| 19 | Test: `_detect_model` P23B32 | ✅ Done | [test_config_flow.py:158-176](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L158-L176) |
| 20 | Test: `_detect_model` unknown signature | ✅ Done | [test_config_flow.py:179-197](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L179-L197) — uses `0x09` |
| 21 | Test: two-step flow success | ✅ Done | [test_config_flow.py:93-130](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L93-L130) |
| 22 | Test: model confirm creates entry | ✅ Done | [test_config_flow.py:200-220](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L200-L220) |
| 23 | Test: unknown sig → model_confirm form | ✅ Done | [test_config_flow.py:223-246](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L223-L246) |
| 24 | Test: reconfigure shows current model | ✅ Done | [test_config_flow.py:272-285](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L272-L285) |
| 25 | Test: reconfigure updates model | ✅ Done | [test_config_flow.py:288-310](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py#L288-L310) |
| 26 | Test: P25B37 context, light ON/OFF, jets | ✅ Done | [test_p25b37_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p25b37_adapter.py) — 5 tests |
| 27 | `P23BaseAdapter` extraction | ✅ Done | [p23.py:153](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py) — not in plan but consistent pattern |
| 28 | All tests pass (171) | ✅ Done | `171 passed in 0.92s` |

## Intentional Deviations (Improvements)

### 1. Split into two model-confirm steps instead of one with `description_placeholders`

> [!NOTE]
> The plan specified a single `async_step_model_confirm` that uses `{auto_detect_note}` placeholder to dynamically switch between "✓ Auto-detected" and "⚠️ Could not identify" text.

The implementation uses **two separate steps**: `async_step_model_confirm` (auto-detected, with `{model}` placeholder) and `async_step_model_confirm_manual` (unknown signature, dedicated warning text). This is **better** because:
- Each step has its own translation key, enabling translators to provide distinct copy for each scenario.
- The HA frontend renders the `{model}` placeholder only when relevant, avoiding empty or confusing substitutions.
- The separation is clean and self-documenting.

### 2. URL placeholders instead of hardcoded links

> [!NOTE]
> The plan hardcoded the GitHub README and issues URLs directly in translation strings.

The implementation uses `{readme_url}` and `{issues_url}` `description_placeholders`, with the actual URLs injected from code. This is **better** because URLs can be updated in one place without touching translation files.

## Gaps and Omissions

### 1. Missing `selector` block in translations

> [!WARNING]
> The plan specified a `"selector": { "model": { "options": { "P25B85": "Joyonway P25B85", ... }}}` block in all translation files and `strings.json`. The implementation does **not** include this block in any file.

The `_model_schema()` helper also omits the `translation_key="model"` parameter from `SelectSelectorConfig` (plan line 86 specified it). This means the dropdown shows raw model names like `P23B32` instead of user-friendly labels like `Joyonway P23B32`. The raw names are still perfectly functional but less polished.

**Impact**: Low — the raw model names are short and recognizable. But if you want the "Joyonway P25B85" display labels, both the `translation_key` parameter and the `selector` block need to be added.

### 2. Missing `P25B37` in translation `selector.model.options`

> [!WARNING]
> The plan's "Translation Update" section for the P25B37 work says to add `"P25B37": "Joyonway P25B37"` to `selector.model.options` in all translation files. Since the entire `selector` block is missing (gap #1), this is implicitly absent too.

This is a subset of gap #1 — once the `selector` block is added, P25B37 should be included.

### 3. P23 base class extraction (bonus, not in plan)

The P23 adapter was also refactored into `P23BaseAdapter` + `P23B32Adapter(P23BaseAdapter)` in [p23.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py). This isn't in the plan but is a sensible consistency measure preparing for future P23 variants. No issue here.

### 4. Extra commit: Light ON status fix (#60)

Commit `cb9f157` fixes P25B85 light ON status decoding for color indices. This is a bugfix not mentioned in the plan — it's a natural discovery during development and is properly scoped.

## Test Coverage Assessment

| Area | Tests Present | Sufficient? |
|------|:------------:|:-----------:|
| `_detect_model` (P25B85) | ✅ | ✅ |
| `_detect_model` (P23B32) | ✅ | ✅ |
| `_detect_model` (unknown sig → `""`) | ✅ | ✅ |
| `_detect_model` (connection failure) | ✅ | ✅ |
| `_detect_model` (empty stream) | ✅ | ✅ |
| Config flow → model_confirm transition | ✅ | ✅ |
| Config flow → model_confirm_manual transition | ✅ | ✅ |
| model_confirm creates entry | ✅ | ✅ |
| model_confirm_manual creates entry | ✅ | ✅ |
| Reconfigure shows form | ✅ | ✅ |
| Reconfigure updates + aborts | ✅ | ✅ |
| P25B37 context byte | ✅ | ✅ |
| P25B37 light ON (tail=0x81) | ✅ | ✅ |
| P25B37 light OFF (tail=0x80) | ✅ | ✅ |
| P25B37 jets context | ✅ | ✅ |
| P25B37 parse_status | ✅ | ✅ |
| P25B37 in ADAPTERS registry | ✅ | ✅ |

## Conclusion

The implementation is a clean, faithful execution of the plan with two smart deviations that improve on the original design. The only actionable gap is the missing `selector` translation block (and its `translation_key` reference in `_model_schema`), which affects display polish but not functionality. Everything else — config flow, reconfigure flow, adapter refactoring, P25B37 support, test coverage — is exactly as specified or better.
