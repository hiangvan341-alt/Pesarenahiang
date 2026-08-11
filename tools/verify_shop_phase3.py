"""Kiểm tra tĩnh Shop/Kho đồ trước khi đóng gói hoặc deploy."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from jinja2 import Environment
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def check_python() -> None:
    failures = []
    for path in sorted(ROOT.rglob("*.py")):
        if ".git" in path.parts:
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except Exception as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        raise RuntimeError("Python compile failed:\n" + "\n".join(failures))


def check_templates() -> None:
    env = Environment()
    failures = []
    templates = sorted((ROOT / "templates").rglob("*.html"))
    for path in templates:
        try:
            env.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        raise RuntimeError("Jinja parse failed:\n" + "\n".join(failures))
    print(f"Jinja: {len(templates)}/{len(templates)} PASS")


def route_rows():
    rows = []
    python_files = [ROOT / "app.py", *(ROOT / "modules").rglob("*.py")]
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                func = decorator.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name != "route":
                    continue
                try:
                    rule = ast.literal_eval(decorator.args[0])
                except Exception:
                    continue
                methods = ("GET",)
                endpoint = node.name
                for keyword in decorator.keywords:
                    if keyword.arg == "methods":
                        methods = tuple(sorted(ast.literal_eval(keyword.value)))
                    elif keyword.arg == "endpoint":
                        endpoint = ast.literal_eval(keyword.value)
                rows.append((rule, methods, endpoint, path.relative_to(ROOT), node.lineno))
    return rows


def check_routes() -> None:
    seen = {}
    duplicates = []
    rows = route_rows()
    for row in rows:
        key = (row[0], row[1])
        if key in seen:
            duplicates.append((seen[key], row))
        seen[key] = row
    if duplicates:
        raise RuntimeError(f"Duplicate routes: {duplicates}")
    required = {
        "/shop",
        "/shop/purchase/<item_code>",
        "/inventory",
        "/inventory/equip/<inventory_id>",
        "/inventory/unequip/<slot>",
        "/admin/shop",
        "/admin/shop/items/<item_id>/update",
        "/admin/shop/grant",
    }
    actual = {row[0] for row in rows}
    missing = required - actual
    if missing:
        raise RuntimeError(f"Missing Shop routes: {sorted(missing)}")
    print(f"Routes: {len(rows)} total, 0 duplicate, Shop endpoints PASS")


def check_catalog_and_assets() -> None:
    sql = (ROOT / "docs/update_shop_inventory_phase3_v1_14_40.sql").read_text(encoding="utf-8")
    block = sql.split("insert into public.shop_items", 1)[1].split("on conflict (code)", 1)[0]
    codes = re.findall(r"\n\('([^']+)'", block)
    if len(codes) != 25 or len(set(codes)) != 25:
        raise RuntimeError(f"Catalog expected 25 unique items, got {len(codes)}/{len(set(codes))}")
    asset_root = ROOT / "static/shop/items"
    primary = {path.stem for path in asset_root.glob("*.webp") if not path.stem.endswith("_96")}
    if set(codes) != primary:
        raise RuntimeError(f"Catalog/asset mismatch. Missing={sorted(set(codes)-primary)} extra={sorted(primary-set(codes))}")
    dimensions = {}
    for path in asset_root.glob("*.webp"):
        with Image.open(path) as image:
            dimensions[image.size] = dimensions.get(image.size, 0) + 1
    expected_dimensions = {(512, 512): 19, (1600, 400): 6, (96, 96): 5}
    if dimensions != expected_dimensions:
        raise RuntimeError(f"Unexpected asset dimensions: {dimensions}")
    if "shop_reward_coupon_not_listed" not in sql:
        raise RuntimeError("Reward coupon listing guard is missing")
    print("Catalog: 25/25 PASS; assets: 19 icons + 6 banners + 5 badge thumbnails PASS")


def main() -> int:
    checks = (check_python, check_templates, check_routes, check_catalog_and_assets)
    for check in checks:
        check()
    print("Shop Phase 3 static verification: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"VERIFY FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
