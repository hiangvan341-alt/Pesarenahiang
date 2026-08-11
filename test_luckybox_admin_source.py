import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "modules/luckybox/routes.py").read_text(encoding="utf-8")
REPOSITORY = (ROOT / "modules/luckybox/repository.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "modules/luckybox/service.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates/admin_luckybox/index.html").read_text(encoding="utf-8")
SQL = (ROOT / "docs/update_luckybox_admin_v1_14_41_43.sql").read_text(encoding="utf-8")


def test_phase2b_version_and_python_parse():
    assert 'APP_VERSION = "V1.2.9"' in APP
    for relative in (
        "modules/luckybox/repository.py",
        "modules/luckybox/service.py",
        "modules/luckybox/routes.py",
    ):
        ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)


def test_admin_management_routes_are_protected():
    assert '@app.route("/admin/lucky-box",' in ROUTES
    assert 'endpoint="admin_luckybox"' in ROUTES
    assert ROUTES.count("@admin_required") >= 7
    assert "admin_luckybox_publish_rate" in ROUTES
    assert "admin_luckybox_sync_rewards" in ROUTES
    assert "admin_luckybox_clone_rate" in ROUTES


def test_admin_writes_use_server_side_rpcs():
    for rpc in (
        "save_lucky_box_config",
        "save_lucky_box_rate_version",
        "save_lucky_box_reward",
        "clone_lucky_box_rate_version",
        "sync_lucky_box_rewards",
        "publish_lucky_box_rate_version",
    ):
        assert f'"{rpc}"' in REPOSITORY
    assert '.table("lucky_box_rewards").update' not in REPOSITORY
    assert '.table("lucky_box_rate_versions").update' not in REPOSITORY


def test_sql_only_allows_draft_edits_and_audits_changes():
    assert SQL.count("if v_rate.status<>'draft'") >= 3
    assert "lucky_box_admin_audit_logs" in SQL
    assert "before_data,after_data" in SQL
    assert "save_rate_version" in SQL
    assert "save_reward" in SQL
    assert "save_box_config" in SQL


def test_publish_uses_same_validator_and_does_not_enable_box():
    publish = SQL.split("create or replace function public.publish_lucky_box_rate_version", 1)[1]
    assert "lucky_box_validate_rate_payload" in publish
    assert "set status='archived'" in publish
    assert "set status='active'" in publish
    assert "update public.lucky_boxes" not in publish


def test_sync_adds_new_shop_rewards_safely_disabled():
    sync = SQL.split("create or replace function public.sync_lucky_box_rewards", 1)[1]
    sync = sync.split("create or replace function public.publish_lucky_box_rate_version", 1)[0]
    assert "s.is_active=true and s.is_listed=true" in sync
    assert "0,false" in sync
    assert "on conflict(rate_version_id,reward_code)" in sync


def test_admin_template_has_core_controls():
    for text in (
        "Quản trị Lucky Box PES Arena",
        "Publish thành Active",
        "Đồng bộ Shop → Draft",
        "Nhật ký Admin",
        "Quản lý từng Reward",
    ):
        assert text in TEMPLATE
    assert "rate_validation.valid" in TEMPLATE
    assert "selected_rate_version.status == 'draft'" in TEMPLATE


def test_no_reward_stays_owner_controlled():
    assert "LUCKY_BOX_NO_REWARD_NOT_APPROVED" in SQL
    assert "no_reward_enabled" in TEMPLATE
    assert "Chúc bạn may mắn lần sau" in TEMPLATE


def test_service_validates_inputs_and_limits_preview():
    assert "MAX_PREVIEW_ITERATIONS = 10000" in SERVICE
    assert "_required_nonnegative_int" in SERVICE
    assert "_clean_reason" in SERVICE
    assert "_parse_datetime_local" in SERVICE

def test_admin_member_opening_history_is_inside_luckybox_admin():
    assert "def list_admin_openings" in REPOSITORY
    assert 'db.table("lucky_box_openings")' in REPOSITORY
    assert 'db.table("lucky_box_opening_rewards")' in REPOSITORY
    assert "Lịch sử mở Lucky Box của member" in TEMPLATE
    assert "member_openings" in TEMPLATE
    assert "luckybox_opening_detail" in TEMPLATE

