#!/usr/bin/env python3
"""Static regression guards for page-builder refactors.

This script does not start Qt or the app. It checks code structure only:
- main.py delegate methods keep calling the external page builders
- helper-map keys cover `h.<name>` usages in page modules
- critical UI state attributes remain assigned in builder modules
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui"
MAIN = GUI / "main.py"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_file(path: Path) -> ast.AST:
    return ast.parse(read_text(path), filename=str(path))


def get_func_def(module_ast: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in module_ast.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def app_method_def(main_ast: ast.AST, method_name: str) -> ast.FunctionDef | None:
    for node in main_ast.body:
        if isinstance(node, ast.ClassDef) and node.name == "App":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    return None


def dict_keys_from_returned_helper(fn: ast.FunctionDef) -> set[str]:
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = set()
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
            return keys
    return set()


def helper_fields_used(module_ast: ast.AST) -> set[str]:
    used = set()
    for node in ast.walk(module_ast):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "h":
                used.add(node.attr)
    return used


def self_attrs_assigned(module_ast: ast.AST) -> set[str]:
    attrs = set()
    for node in ast.walk(module_ast):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                ):
                    attrs.add(t.attr)
    return attrs


def metric_attr_literals(module_ast: ast.AST) -> set[str]:
    """Capture attribute names passed as string literals to _make_metric(..., '<attr>')."""
    out = set()
    for node in ast.walk(module_ast):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "_make_metric":
            continue
        if len(node.args) < 2:
            continue
        a1 = node.args[1]
        if isinstance(a1, ast.Constant) and isinstance(a1.value, str):
            out.add(a1.value)
    return out


def assert_delegate_call(method_fn: ast.FunctionDef, callee: str, helper: str) -> list[str]:
    errs: list[str] = []
    if len(method_fn.body) != 1 or not isinstance(method_fn.body[0], ast.Return):
        errs.append(f"{method_fn.name}: expected single-line return delegator")
        return errs
    ret = method_fn.body[0].value
    if not isinstance(ret, ast.Call):
        errs.append(f"{method_fn.name}: return is not a call")
        return errs
    fn = ret.func
    if not isinstance(fn, ast.Name) or fn.id != callee:
        errs.append(f"{method_fn.name}: expected call to {callee}()")
    if len(ret.args) < 2:
        errs.append(f"{method_fn.name}: expected args (self, {helper}())")
        return errs
    arg0 = ret.args[0]
    arg1 = ret.args[1]
    if not (isinstance(arg0, ast.Name) and arg0.id == "self"):
        errs.append(f"{method_fn.name}: first arg must be self")
    if not (
        isinstance(arg1, ast.Call)
        and isinstance(arg1.func, ast.Name)
        and arg1.func.id == helper
    ):
        errs.append(f"{method_fn.name}: second arg must be {helper}()")
    return errs


def main() -> int:
    errors: list[str] = []

    files = {
        "page_models.py": GUI / "page_models.py",
        "page_settings.py": GUI / "page_settings.py",
        "page_inspection.py": GUI / "page_inspection.py",
        "page_results.py": GUI / "page_results.py",
    }

    for path in [MAIN, *files.values()]:
        if not path.exists():
            errors.append(f"missing file: {path}")
    if errors:
        print("\n".join(errors))
        return 1

    main_ast = parse_file(MAIN)
    mod_asts = {name: parse_file(path) for name, path in files.items()}

    # 1) Delegation guards
    delegates = {
        "page_cams": ("build_inspection_page", "_inspection_page_helpers"),
        "page_docker": ("build_models_page", "_models_page_helpers"),
        "page_library": ("build_results_page", "_results_page_helpers"),
        "page_settings": ("build_settings_page", "_settings_page_helpers"),
    }
    for method_name, (callee, helper) in delegates.items():
        fn = app_method_def(main_ast, method_name)
        if fn is None:
            errors.append(f"App.{method_name} missing")
            continue
        errors.extend(assert_delegate_call(fn, callee, helper))

    # 2) Helper key coverage
    helper_to_module = {
        "_models_page_helpers": "page_models.py",
        "_settings_page_helpers": "page_settings.py",
        "_inspection_page_helpers": "page_inspection.py",
        "_results_page_helpers": "page_results.py",
    }
    for helper_name, module_name in helper_to_module.items():
        helper_fn = get_func_def(main_ast, helper_name)
        if helper_fn is None:
            errors.append(f"{helper_name} missing in main.py")
            continue
        helper_keys = dict_keys_from_returned_helper(helper_fn)
        used = helper_fields_used(mod_asts[module_name])
        missing = sorted(used - helper_keys)
        if missing:
            errors.append(f"{helper_name} missing keys for {module_name}: {missing}")

    # 3) Critical state attributes assigned inside builders
    must_assign = {
        "page_models.py": {"_workspace_export_workspace_combo"},
        "page_settings.py": {"_settings_tabs", "_inspection_camera_name_input"},
        "page_inspection.py": {
            "_camera_runtime_name",
            "_camera_runtime_endpoint",
            "_camera_runtime_status_badge",
            "_camera_runtime_detail",
            "_camera_runtime_model_value",
            "_camera_runtime_decision_value",
            "_camera_runtime_trigger_value",
            "_camera_runtime_gpio_value",
            "_camera_runtime_preview_value",
            "_camera_runtime_count_value",
            "_camera_runtime_last_trigger_value",
            "_camera_runtime_reload_btn",
            "_camera_runtime_trigger_btn",
            "cam_widget",
            "cf",
            "abc",
        },
        "page_results.py": {"media_hub_layout"},
    }
    for module_name, required in must_assign.items():
        assigned = self_attrs_assigned(mod_asts[module_name]) | metric_attr_literals(
            mod_asts[module_name]
        )
        miss = sorted(required - assigned)
        if miss:
            errors.append(f"{module_name} missing required self-attrs: {miss}")

    if errors:
        print("PAGE REFACTOR GUARDS: FAILED")
        for e in errors:
            print(f"- {e}")
        return 1

    print("PAGE REFACTOR GUARDS: OK")
    print("Delegation, helper-map coverage, and critical state attrs are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
