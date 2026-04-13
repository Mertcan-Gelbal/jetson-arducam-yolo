# Page Refactor Guards

This document is a quick reference for checking fragile points after page extraction from `main.py`.

## Goal

- Prevent silent regressions while moving page setup from `main.py` into builder modules (`gui/page_*.py`)
- Verify behavior at code level without launching the application

## Critical Delegations

In `gui/main.py`, the following methods must remain delegation entry points:

- `page_cams()` -> `build_inspection_page(self, _inspection_page_helpers())`
- `page_docker()` -> `build_models_page(self, _models_page_helpers())`
- `page_library()` -> `build_results_page(self, _results_page_helpers())`
- `page_settings()` -> `build_settings_page(self, _settings_page_helpers())`

## Fragile State Fields

These fields are assigned on `self` inside builders and are required for runtime flow continuity.

- `Inspection` (`gui/page_inspection.py`)
  - `_camera_runtime_status_badge`
  - `_camera_runtime_detail`
  - `_camera_runtime_model_value`
  - `_camera_runtime_decision_value`
  - `_camera_runtime_trigger_value`
  - `_camera_runtime_gpio_value`
  - `_camera_runtime_preview_value`
  - `_camera_runtime_count_value`
  - `_camera_runtime_last_trigger_value`
  - `_camera_runtime_reload_btn`
  - `_camera_runtime_trigger_btn`
  - `cam_widget`, `cf`, `abc`
- `Results` (`gui/page_results.py`)
  - `media_hub_layout`
- `Models` (`gui/page_models.py`)
  - `_workspace_export_workspace_combo`
- `Settings` (`gui/page_settings.py`)
  - `_settings_tabs`
  - `_inspection_camera_name_input`

## Automated Guard Script

Script: `scripts/check_page_refactor_guards.py`

Checks:

- Delegation signatures and call forms
- Coverage between helper-map keys and `h.<name>` usage
- Assignment of critical `self` fields inside builders

Run:

```bash
python3 scripts/check_page_refactor_guards.py
```

Additional syntax validation:

```bash
python3 -m compileall gui/main.py gui/page_models.py gui/page_settings.py gui/page_inspection.py gui/page_results.py runtime
```
