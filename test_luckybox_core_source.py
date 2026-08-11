import ast
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SQL = (ROOT / "docs/update_luckybox_core_v1_14_41_42.sql").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "modules/luckybox/routes.py").read_text(encoding="utf-8")


def test_app_version_and_module_registration():
    assert 'APP_VERSION = "V1.2.9"' in APP
    assert "from modules.luckybox import register_routes" in APP
    assert "_register_luckybox_routes" in APP


def test_luckybox_python_files_parse():
    for relative in (
        "modules/luckybox/__init__.py",
        "modules/luckybox/repository.py",
        "modules/luckybox/service.py",
        "modules/luckybox/routes.py",
        "modules/static_asset_service.py",
    ):
        ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)


def test_admin_preview_is_protected_and_non_mutating_rpc_exists():
    assert '@app.route("/admin/lucky-box/preview"' in ROUTES
    assert "@admin_required" in ROUTES
    preview_sql = SQL.split("create or replace function public.preview_lucky_box_rate_version", 1)[1]
    preview_sql = preview_sql.split("create or replace function public.publish_lucky_box_rate_version", 1)[0]
    assert "mutated_data',false" in preview_sql
    assert "insert into public.user_inventory" not in preview_sql
    assert "update public.users set zcoin_balance" not in preview_sql


def test_live_open_is_server_side_atomic_and_idempotent():
    live_sql = SQL.split("create or replace function public.open_lucky_box", 1)[1]
    assert "pg_advisory_xact_lock" in live_sql
    assert "where request_id=v_key" in live_sql
    assert "for update" in live_sql
    assert "insert into public.lucky_box_opening_rewards" in live_sql
    assert "for v_slot in 1..3 loop" in live_sql
    assert "update public.users set zcoin_balance" in live_sql
    assert "insert into public.user_notifications" in live_sql


def test_safe_seed_cannot_open_or_publish():
    assert "'lucky_box_pes_arena','Lucky Box PES Arena'" in SQL
    assert "'draft',0" in SQL
    assert "'pending'" in SQL
    assert "'no_reward','Chúc bạn may mắn lần sau','no_reward',false,0,0,false" in SQL
    assert "status='active'" in SQL  # supported by publish/open, but not seeded active
    seed_tail = SQL.split("-- Seed box", 1)[1]
    assert ") on conflict(code) do nothing;" in seed_tail
    assert "version_number,status,open_price_zcoin" in seed_tail


def test_asset_mapping_has_exact_18_rows_and_exclusives():
    with (ROOT / "docs/LUCKYBOX_ASSET_MAPPING_V1.14.41.42.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 18
    codes = {row["item_code"] for row in rows}
    assert "lucky_box_pes_arena" in codes
    assert "no_reward" in codes
    assert "lb_banner_cristiano_ronaldo" in codes
    assert "lb_banner_lionel_messi" in codes
    assert "lb_frame_ke_thong_tri_hoang_gia" in codes


def test_luckybox_asset_base_is_separate_from_shop_base():
    source = (ROOT / "modules/static_asset_service.py").read_text(encoding="utf-8")
    assert "LUCKYBOX_ASSET_BASE_URL" in source
    assert "SHOP_ASSET_BASE_URL" in source
    assert "def luckybox_asset_base_url" in source
    assert "def shop_asset_base_url" in source
