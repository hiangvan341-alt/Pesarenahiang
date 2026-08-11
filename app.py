import csv
import json
import hashlib
import io
import os
import random
import secrets
import string
import time
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError
from flask import (
    Flask,
    jsonify,
    flash,
    g,
    has_request_context,
    make_response,
    send_file,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from supabase import create_client

from modules.quick_match.service import build_candidate_sort_key, quick_match_priority_group
from modules.cache_utils import (
    cache_get, cache_set, cache_delete, ttl_cache_get, ttl_cache_set, ttl_cache_delete,
)
from modules.datetime_utils import (
    now_dt, now_iso, future_iso, aware_utc, seconds_until, parse_dt, format_vn_datetime,
)
from modules.rp_formula import (
    BASE_WIN_POINTS, PLACEMENT_MATCHES, PLACEMENT_WIN_MULTIPLIER,
    MIN_RANK_ADJUSTED_WIN_POINTS, MAX_RANK_ADJUSTED_WIN_POINTS,
    MAX_POSITIVE_POINTS_PER_MATCH, WIN_STREAK_BONUSES, HOST_WIN_FACTOR,
    RP_FORMULA_VERSION, RP_RANDOM_SEED_NAMESPACE, formula_summary,
)
from modules.rp_engine import (
    calculate_deltas as calculate_ranked_deltas, validate_deltas as validate_ranked_deltas,
)
from modules.admin_match_service import parse_score, score_changed
from modules.admin_ranking_rebuild import build_replay_plan
from modules.system_feature_service import post_login_endpoint, dashboard_is_enabled
from modules.session_runtime_service import (
    IDLE_TIMEOUT_SECONDS, PROTECTED_ROOM_STATUSES, idle_decision, room_blocks_idle_logout, client_config as session_client_config,
)
from modules.static_asset_service import asset_url, asset_base_url, shop_asset_base_url, luckybox_asset_base_url
from modules.profile import equipment_service as profile_equipment_service
from modules.win_streaks import (
    WIN_STREAK_TITLES, WIN_STREAK_EVENT_PREFIX, get_win_streak_title,
    get_win_streak_badge, build_win_streak_event, encode_win_streak_room_note,
    parse_win_streak_room_note,
)


load_dotenv()

APP_NAME = "PES Arena – Bản Lĩnh Sân Cỏ"
APP_VERSION = "V1.2.9"
DEFAULT_POINTS = 1000
DEVICE_COOKIE_NAME = "rankzone_device_id"
COOLDOWN_MINUTES = 3
ONLINE_TIMEOUT_SECONDS = 60
CHAT_COOLDOWN_SECONDS = 5
CHAT_MAX_LENGTH = 200
DISPUTE_EVIDENCE_BUCKET = "dispute-evidence"
DISPUTE_EVIDENCE_MAX_BYTES = 4 * 1024 * 1024
DISPUTE_EVIDENCE_MAX_SIDE = 1600
DISPUTE_EVIDENCE_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

ACHIEVEMENT_DEFINITIONS = [
    {"code": "first_match", "icon": "⚽", "name": "Bước chân đầu tiên", "description": "Hoàn thành trận đấu đầu tiên.", "metric": "total_matches", "threshold": 1, "priority": 10},
    {"code": "warrior_20", "icon": "🛡️", "name": "Chiến binh sân cỏ", "description": "Hoàn thành 20 trận đấu.", "metric": "total_matches", "threshold": 20, "priority": 20},
    {"code": "winner_10", "icon": "🏅", "name": "Kẻ chinh phục", "description": "Giành 10 chiến thắng.", "metric": "wins", "threshold": 10, "priority": 30},
    {"code": "goals_50", "icon": "🎯", "name": "Sát thủ vòng cấm", "description": "Ghi tổng cộng 50 bàn thắng.", "metric": "goals_for", "threshold": 50, "priority": 40},
    {"code": "hot_streak_5", "icon": "🔥", "name": "Chuỗi lửa", "description": "Thắng liên tiếp 5 trận.", "metric": "streak", "threshold": 5, "priority": 50},
    {"code": "top_one", "icon": "👑", "name": "Đỉnh bảng", "description": "Từng giữ vị trí số 1 BXH sau ít nhất 5 trận.", "metric": "position", "threshold": 1, "priority": 60},
]
ACHIEVEMENT_BY_CODE = {item["code"]: item for item in ACHIEVEMENT_DEFINITIONS}
ADMIN_LEVELS = {"owner", "admin"}
ACCOUNT_STATUSES = {"pending", "approved", "rejected", "banned"}
REMATCH_HOST_READY_NOTE = "__rematch_host_ready__"
REMATCH_GUEST_READY_NOTE = "__rematch_guest_ready__"
REMATCH_HOST_DECLINED_NOTE = "__rematch_host_declined__"
REMATCH_GUEST_DECLINED_NOTE = "__rematch_guest_declined__"
REMATCH_EXPIRED_NOTE = "__rematch_expired__"

DISPUTE_REASON_OPTIONS = {
    "wrong_score": "Sai tỷ số",
    "wrong_winner": "Sai người thắng",
    "interrupted": "Trận bị gián đoạn",
    "unilateral_entry": "Kết quả nhập không đúng thỏa thuận",
    "other": "Lý do khác",
    "timeout": "Hết thời gian xác nhận",
    "legacy": "Tranh chấp từ phiên bản cũ",
}
DISPUTE_PENDING_STATUSES = {"pending", "processing"}

# Khóa toàn cục ngắn hạn dùng khi Admin phát lại lịch sử BXH.
# Lưu trong Supabase để có hiệu lực trên nhiều Serverless Function/instance.
RANKING_REBUILD_LOCK_KEY = "admin_ranking_rebuild_lock"
RANKING_REBUILD_LOCK_SECONDS = 5 * 60

INVITE_TIMEOUT_SECONDS = 60
ROOM_READY_TIMEOUT_SECONDS = 30 * 60
RESULT_CONFIRM_TIMEOUT_SECONDS = 60
REMATCH_TIMEOUT_SECONDS = 60
ROOM_EMPTY_INACTIVITY_TIMEOUT_SECONDS = 30 * 60
ROOM_MATCH_INACTIVITY_TIMEOUT_SECONDS = 4 * 60 * 60
ROOM_ABANDON_PENALTY = 20
ROOM_TIMEOUT_PENALTY_RANGE = (22, 25)

RANK_K_FACTOR = 32
RANK_SCALE = 400
TEAM_OVR_BASE = 79
TEAM_OVR_WEIGHT = 20

# Cấu hình công thức: modules/rp_formula.py; logic tính: modules/rp_engine.py

# Rank/Tier difficulty system (V1.8.1)
SMART_RANDOM_CORRECT_WEIGHT = 0.70
SMART_RANDOM_STRONGER_WEIGHT = 0.15
SMART_RANDOM_WEAKER_WEIGHT = 0.15

# Danh hiệu chuỗi thắng đã tách sang modules/win_streaks.py



DEFAULT_RANKS = [
    {"min": 0, "max": 499, "name": "Gà", "short_name": "Gà", "abbr": "G", "code": "CHICKEN", "icon": "🐔", "slug": "ga"},
    {"min": 500, "max": 699, "name": "Non", "short_name": "Non", "abbr": "N", "code": "NOVICE", "icon": "🌱", "slug": "non"},
    {"min": 700, "max": 899, "name": "Báo Thủ", "short_name": "Báo", "abbr": "BT", "code": "LIABILITY", "icon": "⚠️", "slug": "bao-thu"},
    {"min": 900, "max": 1099, "name": "Mới Tập Chơi", "short_name": "Mới Chơi", "abbr": "MTC", "code": "BEGINNER", "icon": "🎮", "slug": "moi-tap-choi"},
    {"min": 1100, "max": 1399, "name": "Bán Chuyên", "short_name": "B.Chuyên", "abbr": "BC", "code": "SEMI_PRO", "icon": "⚔️", "slug": "ban-chuyen"},
    {"min": 1400, "max": 1699, "name": "Chuyên Nghiệp", "short_name": "C.Nghiệp", "abbr": "CN", "code": "PROFESSIONAL", "icon": "🎯", "slug": "chuyen-nghiep"},
    {"min": 1700, "max": 1999, "name": "Đẳng Cấp", "short_name": "Đ.Cấp", "abbr": "ĐC", "code": "CLASS", "icon": "💎", "slug": "dang-cap"},
    {"min": 2000, "max": 2349, "name": "Siêu Sao", "short_name": "S.Sao", "abbr": "SS", "code": "SUPERSTAR", "icon": "🌟", "slug": "sieu-sao"},
    {"min": 2350, "max": 2699, "name": "Huyền Thoại", "short_name": "H.Thoại", "abbr": "HT", "code": "LEGEND", "icon": "🏆", "slug": "huyen-thoai"},
    {"min": 2700, "max": None, "name": "GOAT", "short_name": "GOAT", "abbr": "GOAT", "code": "GOAT", "icon": "👑", "slug": "goat"},
]

MATCH_STATUS_LABELS = {
    "confirmed": "Đã xác nhận",
    "cancelled": "Đã hủy",
    "disputed": "Đang tranh chấp",
    "playing": "Đang thi đấu",
    "waiting_confirm": "Chờ xác nhận",
    "waiting_ready": "Chờ Chủ Phòng Quay",
    "waiting_result_confirm": "Chờ xác nhận kết quả",
}

ACTIVITY_PRIORITY = {
    "ready": 0,
    "in_room": 1,
    "waiting_confirm": 2,
    "playing": 3,
}


APP_ENV = (os.getenv("APP_ENV") or os.getenv("VERCEL_ENV") or "production").strip().lower()

# Production/Preview bắt buộc phải có secret riêng trong biến môi trường.
# Chỉ môi trường test/development mới được tạo secret tạm thời cho phiên chạy cục bộ.
_flask_secret_key = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
if not _flask_secret_key:
    if APP_ENV in {"test", "testing", "development"}:
        _flask_secret_key = secrets.token_hex(32)
    else:
        raise RuntimeError(
            "Thiếu FLASK_SECRET_KEY. Hãy khai báo secret dài, ngẫu nhiên trong biến môi trường Vercel trước khi chạy app."
        )

app = Flask(__name__)
app.secret_key = _flask_secret_key
app.permanent_session_lifetime = timedelta(days=30)
del _flask_secret_key

_STATIC_FINGERPRINT_CACHE = {}

def static_asset(filename):
    """Return a static URL fingerprinted from the file content.

    CSS/JS cache busting no longer depends on manually bumping APP_VERSION.
    A changed file gets a new URL; an unchanged file keeps the same URL.
    """
    clean_name = str(filename or "").lstrip("/")
    file_path = Path(app.static_folder) / clean_name
    try:
        stat = file_path.stat()
        cache_key = (clean_name, stat.st_mtime_ns, stat.st_size)
        fingerprint = _STATIC_FINGERPRINT_CACHE.get(cache_key)
        if fingerprint is None:
            fingerprint = hashlib.sha256(file_path.read_bytes()).hexdigest()[:12]
            _STATIC_FINGERPRINT_CACHE.clear()
            _STATIC_FINGERPRINT_CACHE[cache_key] = fingerprint
    except OSError:
        fingerprint = APP_VERSION
    return f"{url_for('static', filename=clean_name)}?v={fingerprint}"

app.jinja_env.globals["asset_url"] = asset_url
app.jinja_env.globals["static_asset"] = static_asset
app.jinja_env.globals["asset_base_url"] = asset_base_url
app.jinja_env.globals["shop_asset_base_url"] = shop_asset_base_url
app.jinja_env.globals["luckybox_asset_base_url"] = luckybox_asset_base_url

PES_ARENA_TEST_MODE = (os.getenv("PES_ARENA_TEST_MODE") or "false").strip().lower() in {"1", "true", "yes", "on"}
ALLOW_SIMPLE_TEST_PASSWORDS = (os.getenv("ALLOW_SIMPLE_TEST_PASSWORDS") or "false").strip().lower() in {"1", "true", "yes", "on"}
DATABASE_SAFETY_TOKEN = (os.getenv("DATABASE_SAFETY_TOKEN") or "").strip()

def is_test_mode():
    return APP_ENV in {"test", "testing", "development", "preview"} and PES_ARENA_TEST_MODE and DATABASE_SAFETY_TOKEN == "PES_ARENA_TEST_DATABASE"


def simple_test_passwords_enabled():
    """Only allow one-character passwords in an explicitly isolated test environment."""
    return is_test_mode() and ALLOW_SIMPLE_TEST_PASSWORDS


def minimum_password_length():
    return 1 if simple_test_passwords_enabled() else 6


def validate_new_password(password: str):
    minimum = minimum_password_length()
    if len(password or "") < minimum:
        return False, f"Mật khẩu mới phải có ít nhất {minimum} ký tự."
    return True, ""

supabase_url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
supabase_key = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY")
    or ""
).strip()

db = create_client(supabase_url, supabase_key) if supabase_url and supabase_key else None


# Cache đã tách sang modules/cache_utils.py

def execute_query(query, label="Supabase", attempts=4, delay=0.25):
    """Retry short-lived Vercel/Supabase network failures before returning 500."""
    last_error = None

    for attempt in range(max(1, attempts)):
        try:
            return query.execute()
        except Exception as exc:
            last_error = exc
            message = f"{type(exc).__name__}: {exc}".lower()

            transient = any(token in message for token in (
                "connecterror",
                "connection",
                "server disconnected",
                "remoteprotocolerror",
                "timeout",
                "temporarily",
                "device or resource busy",
                "resource busy",
                "errno 16",
                "eagain",
            ))

            if not transient or attempt >= max(1, attempts) - 1:
                print(f"{label} failed after {attempt + 1} attempt(s): {exc}")
                raise

            # Backoff ngắn: 0.25s, 0.5s, 0.75s...
            time.sleep(delay * (attempt + 1))

    raise last_error



_admin_checked = False


# =========================
# Basic helpers
# =========================
# Tiện ích thời gian đã tách sang modules/datetime_utils.py

def _normalize_storage_public_url(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("publicUrl") or value.get("public_url") or value.get("signedURL") or value.get("signed_url")
    return str(value or "")


def prepare_dispute_evidence_bytes(file_storage):
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None

    raw = file_storage.read(DISPUTE_EVIDENCE_MAX_BYTES + 1)
    if len(raw) > DISPUTE_EVIDENCE_MAX_BYTES:
        raise ValueError("Ảnh bằng chứng không được vượt quá 4 MB.")
    if not raw:
        raise ValueError("File ảnh bằng chứng đang trống.")

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            image_format = (probe.format or "").upper()
            width, height = probe.size
            probe.verify()
        if image_format not in DISPUTE_EVIDENCE_ALLOWED_FORMATS:
            raise ValueError("Bằng chứng chỉ chấp nhận ảnh JPG, PNG hoặc WEBP.")
        if width < 100 or height < 100:
            raise ValueError("Ảnh bằng chứng quá nhỏ. Vui lòng chọn ảnh từ 100×100 pixel trở lên.")
        if width * height > 30_000_000:
            raise ValueError("Ảnh bằng chứng có độ phân giải quá lớn.")

        with Image.open(io.BytesIO(raw)) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            source.thumbnail(
                (DISPUTE_EVIDENCE_MAX_SIDE, DISPUTE_EVIDENCE_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            output = io.BytesIO()
            source.save(output, format="WEBP", quality=86, method=6)
            return output.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError("File bằng chứng không phải ảnh hợp lệ hoặc đã bị lỗi.")


def upload_dispute_evidence(match_id, user_id, evidence_bytes):
    require_db()
    object_path = f"{match_id}/{user_id}/{uuid.uuid4().hex}.webp"
    bucket = db.storage.from_(DISPUTE_EVIDENCE_BUCKET)
    bucket.upload(
        object_path,
        evidence_bytes,
        {
            "content-type": "image/webp",
            "cache-control": "3600",
            "upsert": "false",
        },
    )
    return object_path


def remove_dispute_evidence_object(object_path):
    if not object_path or db is None:
        return
    try:
        db.storage.from_(DISPUTE_EVIDENCE_BUCKET).remove([object_path])
    except Exception as exc:
        print(f"remove_dispute_evidence_object warning: {exc}")


def get_dispute_evidence_signed_url(object_path, expires_in=3600):
    if not object_path or db is None:
        return None
    try:
        response = db.storage.from_(DISPUTE_EVIDENCE_BUCKET).create_signed_url(
            object_path,
            max(60, int(expires_in)),
        )
        return _normalize_storage_public_url(response)
    except Exception as exc:
        print(f"get_dispute_evidence_signed_url warning: {exc}")
        return None


def achievement_progress(player, definition, position=None):
    metric = definition.get("metric")
    threshold = max(1, int(definition.get("threshold", 1) or 1))
    if metric == "position":
        current = 1 if position == 1 and calculated_total_matches(player) >= 5 else 0
    else:
        current = max(0, int(player.get(metric, 0) or 0))
    return current, threshold, min(100, round((current / threshold) * 100))


def eligible_achievement_codes(player, position=None):
    eligible = []
    for definition in ACHIEVEMENT_DEFINITIONS:
        current, threshold, _ = achievement_progress(player, definition, position)
        if current >= threshold:
            eligible.append(definition["code"])
    return eligible


def list_user_achievement_map():
    cached = cache_get("_rz_user_achievement_map")
    if cached is not None:
        return cached
    shared = ttl_cache_get("achievement_map")
    if shared is not None:
        return cache_set("_rz_user_achievement_map", shared)
    mapped = {}
    try:
        result = execute_query(
            db.table("user_achievements").select("user_id,achievement_code,unlocked_at"),
            "list_user_achievements",
            attempts=2,
        )
        for row in result.data or []:
            mapped.setdefault(str(row.get("user_id")), {})[row.get("achievement_code")] = row
    except Exception as exc:
        print(f"list_user_achievement_map warning: {exc}")
    ttl_cache_set("achievement_map", mapped, 30)
    return cache_set("_rz_user_achievement_map", mapped)


def decorate_player_achievements(player, position=None, achievement_map=None):
    if not player:
        return player
    achievement_map = achievement_map if achievement_map is not None else list_user_achievement_map()
    saved = achievement_map.get(str(player.get("id")), {})
    achievements = []
    for definition in ACHIEVEMENT_DEFINITIONS:
        current, threshold, progress = achievement_progress(player, definition, position)
        unlocked = definition["code"] in saved or current >= threshold
        item = dict(definition)
        item.update({
            "unlocked": unlocked,
            "unlocked_at": (saved.get(definition["code"]) or {}).get("unlocked_at"),
            "current": current,
            "progress": progress,
        })
        achievements.append(item)
    unlocked_items = sorted(
        [item for item in achievements if item.get("unlocked")],
        key=lambda item: int(item.get("priority", 0)),
        reverse=True,
    )
    player["achievements"] = achievements
    player["unlocked_achievements"] = unlocked_items
    player["achievement_count"] = len(unlocked_items)
    player["featured_achievement"] = unlocked_items[0] if unlocked_items else None
    return player


def sync_achievements_for_users(user_ids, notify=True):
    user_ids = [str(user_id) for user_id in dict.fromkeys(user_ids or []) if user_id]
    if not user_ids or db is None:
        return []
    try:
        result = execute_query(
            db.table("users").select("*").eq("role", "player"),
            "achievement_fresh_players",
            attempts=2,
        )
        players = [dict(item) for item in (result.data or [])]
        players.sort(key=_player_ranking_sort_key)
        positions = {str(item.get("id")): index for index, item in enumerate(players, 1)}
        by_id = {str(item.get("id")): item for item in players}

        existing_result = execute_query(
            db.table("user_achievements").select("user_id,achievement_code"),
            "achievement_existing",
            attempts=2,
        )
        existing = {(str(row.get("user_id")), row.get("achievement_code")) for row in (existing_result.data or [])}
        newly_unlocked = []
        for user_id in user_ids:
            player = by_id.get(user_id)
            if not player:
                continue
            for code in eligible_achievement_codes(player, positions.get(user_id)):
                if (user_id, code) in existing:
                    continue
                try:
                    execute_query(
                        db.table("user_achievements").insert({
                            "user_id": user_id,
                            "achievement_code": code,
                            "unlocked_at": now_iso(),
                        }),
                        "achievement_unlock",
                        attempts=2,
                    )
                    existing.add((user_id, code))
                    newly_unlocked.append((user_id, code))
                    if notify:
                        definition = ACHIEVEMENT_BY_CODE.get(code, {})
                        create_user_notification(
                            user_id,
                            f"{definition.get('icon', '🏅')} Huy hiệu mới",
                            f"Bạn đã mở khóa huy hiệu {definition.get('name', code)}.",
                            f"/profile/{user_id}",
                            "achievement",
                        )
                except Exception as exc:
                    if "duplicate" not in str(exc).lower():
                        print(f"achievement_unlock warning: {exc}")
        if has_request_context():
            setattr(g, "_rz_user_achievement_map", None)
        return newly_unlocked
    except Exception as exc:
        print(f"sync_achievements_for_users warning: {exc}")
        return []


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def is_admin_user(user) -> bool:
    return bool(
        user and (
            user.get("role") == "admin"
            or user.get("admin_level") in ADMIN_LEVELS
        )
    )


def is_owner_user(user) -> bool:
    return bool(
        user and (
            user.get("admin_level") == "owner"
        )
    )


ADMIN_PERMISSION_GROUPS = {
    "users": ["users_view", "users_approve", "users_edit", "users_delete", "password_reset", "accounts_import"],
    "matches": ["matches_view", "matches_confirm", "matches_cancel", "matches_delete"],
    "operations": ["rooms_manage", "invites_manage", "announcements_manage"],
    "system": ["system_features_manage", "chat_manage", "friendly_manage", "registration_codes_manage", "admin_logs_view"],
    "rp": ["rp_view", "rp_simulate", "rp_backup_restore", "daily_rank_limits_manage"],
    "economy": ["zcoin_view", "zcoin_manage"],
    "permissions": ["permissions_manage"],
}
ADMIN_PERMISSION_LABELS = {
    "users_view":"Xem người dùng", "users_approve":"Duyệt tài khoản", "users_edit":"Sửa tài khoản",
    "users_delete":"Xóa tài khoản", "password_reset":"Xử lý quên mật khẩu", "accounts_import":"Import CSV",
    "matches_view":"Xem trận",
    "matches_confirm":"Xác nhận trận", "matches_cancel":"Hủy trận", "matches_delete":"Xóa trận",
    "rooms_manage":"Quản lý phòng", "invites_manage":"Quản lý lời mời",
    "announcements_manage":"Quản lý thông báo", "system_features_manage":"Bật/tắt tính năng hệ thống", "chat_manage":"Quản lý Chat", "friendly_manage":"Quản lý Giao hữu",
    "registration_codes_manage":"Quản lý mã đăng ký", "admin_logs_view":"Xem nhật ký Admin",
    "rp_view":"Xem công thức RP", "rp_simulate":"Tính thử RP",
    "rp_backup_restore":"Backup/Khôi phục RP", "daily_rank_limits_manage":"Bật/tắt giới hạn Rank ngày",
    "zcoin_view":"Xem ví và giao dịch Zcoin", "zcoin_manage":"Cộng/trừ Zcoin",
    "permissions_manage":"Cấp/thu hồi quyền Admin",
}
LEGACY_ADMIN_PERMISSION_FIELDS = {
    "create_test_account": "admin_can_create_test_account",
    "import_accounts_csv": "admin_can_import_accounts_csv",
    "accounts_import": "admin_can_import_accounts_csv",
}
SYSTEM_FEATURE_DEFAULTS = {
    "dashboard_enabled": False,
    "public_ranking_enabled": True,
    "friendly_enabled": True, "rank_standard_enabled": True, "friendly_random3_enabled": True, "lobby_chat_enabled": True, "room_chat_enabled": True,
    "registration_codes_enabled": True, "announcements_enabled": True, "quick_match_enabled": True,
    "repeat_opponent_rp_enabled": True,
}

def _admin_permissions(user):
    raw = (user or {}).get("admin_permissions") or {}
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except Exception: raw = {}
    return raw if isinstance(raw, dict) else {}


def has_admin_permission(user, permission_code: str) -> bool:
    if is_owner_user(user): return True
    if not is_admin_user(user): return False
    permissions = _admin_permissions(user)
    if permission_code in permissions: return permissions.get(permission_code) is True
    legacy = LEGACY_ADMIN_PERMISSION_FIELDS.get(permission_code)
    return bool(legacy and user.get(legacy) is True)


def get_system_features():
    request_key = "_system_features_cached"
    cached = cache_get(request_key)
    if isinstance(cached, dict):
        return dict(cached)

    cached = ttl_cache_get("system_features")
    if isinstance(cached, dict):
        return cache_set(request_key, dict(cached))

    features = dict(SYSTEM_FEATURE_DEFAULTS)
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", "admin_system_features").limit(1),
            "get_system_features", attempts=2,
        )
        row = (result.data or [{}])[0]
        raw = row.get("setting_value")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            features.update({key: bool(value) for key, value in raw.items() if key in features})
    except Exception as exc:
        print(f"get_system_features warning: {exc}")

    ttl_cache_set("system_features", dict(features), 45)
    return cache_set(request_key, dict(features))


def system_feature_enabled(key: str) -> bool:
    return bool(get_system_features().get(key, SYSTEM_FEATURE_DEFAULTS.get(key, False)))


ROOM_STYLE_SETTING_KEY = "room_visual_style"
ROOM_STYLE_DEFAULT = "champions-night"
ROOM_STYLE_OPTIONS = {
    "champions-night": "Broadcast Pro",
    "frozen-tech": "Hex Arena",
    "ember-rivalry": "Glass Deck",
    "royal-gold": "Tunnel Match",
    "mono-tactical": "Tactical Board",
}


def get_room_visual_style():
    request_key = "_room_visual_style_cached"
    cached = cache_get(request_key)
    if cached in ROOM_STYLE_OPTIONS:
        return cached
    cached = ttl_cache_get("room_visual_style")
    if cached in ROOM_STYLE_OPTIONS:
        return cache_set(request_key, cached)
    style = ROOM_STYLE_DEFAULT
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", ROOM_STYLE_SETTING_KEY).limit(1),
            "get_room_visual_style", attempts=2,
        )
        raw = ((result.data or [{}])[0]).get("setting_value")
        if isinstance(raw, dict):
            raw = raw.get("style")
        if raw in ROOM_STYLE_OPTIONS:
            style = raw
    except Exception as exc:
        print(f"get_room_visual_style warning: {exc}")
    ttl_cache_set("room_visual_style", style, 45)
    return cache_set(request_key, style)


QUICK_MATCH_SETTING_KEY = "quick_match_config"
QUICK_MATCH_COLOR_DEFAULT = "blue"
QUICK_MATCH_COLOR_VALUES = {"blue", "green"}

def get_quick_match_config():
    request_key = "_quick_match_config_cached"
    cached = cache_get(request_key)
    if isinstance(cached, dict):
        return dict(cached)

    cached = ttl_cache_get("quick_match_config")
    if isinstance(cached, dict):
        return cache_set(request_key, dict(cached))

    config = {"color": QUICK_MATCH_COLOR_DEFAULT}
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", QUICK_MATCH_SETTING_KEY).limit(1),
            "get_quick_match_config", attempts=2,
        )
        raw = ((result.data or [{}])[0]).get("setting_value")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict) and raw.get("color") in QUICK_MATCH_COLOR_VALUES:
            config["color"] = raw["color"]
    except Exception as exc:
        print(f"get_quick_match_config warning: {exc}")

    ttl_cache_set("quick_match_config", dict(config), 60)
    return cache_set(request_key, dict(config))


REPEAT_OPPONENT_CONFIG_SETTING_KEY = "repeat_opponent_rp_config"
REPEAT_OPPONENT_WINNER_FACTOR_DEFAULTS = [100, 60, 30, 0]
REPEAT_OPPONENT_LOSER_FACTOR_DEFAULTS = [100, 70, 40, 10]

def get_repeat_opponent_rp_config():
    request_key = "_repeat_opponent_rp_config_cached"
    cached = cache_get(request_key)
    if isinstance(cached, dict):
        return {key: list(value) if isinstance(value, list) else value for key, value in cached.items()}

    cached = ttl_cache_get("repeat_opponent_rp_config")
    if isinstance(cached, dict):
        copied = {key: list(value) if isinstance(value, list) else value for key, value in cached.items()}
        return cache_set(request_key, copied)

    config = {
        "winner_factors": list(REPEAT_OPPONENT_WINNER_FACTOR_DEFAULTS),
        "loser_factors": list(REPEAT_OPPONENT_LOSER_FACTOR_DEFAULTS),
    }
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value").eq(
                "setting_key", REPEAT_OPPONENT_CONFIG_SETTING_KEY
            ).limit(1),
            "get_repeat_opponent_rp_config", attempts=2,
        )
        raw = ((result.data or [{}])[0]).get("setting_value")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            for key in ("winner_factors", "loser_factors"):
                values = raw.get(key)
                if isinstance(values, list) and len(values) == 4:
                    normalized = [max(0, min(100, int(value))) for value in values]
                    if all(normalized[index] >= normalized[index + 1] for index in range(3)):
                        config[key] = normalized
    except Exception as exc:
        print(f"get_repeat_opponent_rp_config warning: {exc}")

    ttl_cache_set("repeat_opponent_rp_config", {
        "winner_factors": list(config["winner_factors"]),
        "loser_factors": list(config["loser_factors"]),
    }, 60)
    return cache_set(request_key, config)


MAINTENANCE_SETTING_KEY = "server_maintenance_config"
VN_TIMEZONE = timezone(timedelta(hours=7))
_maintenance_cache = {"value": None, "expires_at": 0.0}


def _maintenance_default_config():
    return {
        "manual_closed": False,
        "close_at": "",
        "open_at": "",
        "message": "Hệ thống đang được bảo trì. Vui lòng quay lại sau.",
        "updated_at": "",
    }


def _parse_maintenance_time(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VN_TIMEZONE)
        return parsed.astimezone(VN_TIMEZONE)
    except (TypeError, ValueError):
        return None


def _normalize_maintenance_input(value):
    parsed = _parse_maintenance_time(value)
    return parsed.isoformat(timespec="minutes") if parsed else ""


def get_maintenance_config(force=False):
    now_ts = time.time()
    if not force and _maintenance_cache.get("value") is not None and now_ts < _maintenance_cache.get("expires_at", 0):
        return dict(_maintenance_cache["value"])

    config = _maintenance_default_config()
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", MAINTENANCE_SETTING_KEY).limit(1),
            "get_server_maintenance_config",
            attempts=2,
        )
        row = (result.data or [{}])[0]
        raw = row.get("setting_value")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            for key in config:
                if key in raw:
                    config[key] = raw[key]
    except Exception as exc:
        app.logger.warning("Maintenance config load failed: %s", exc)

    config["manual_closed"] = bool(config.get("manual_closed"))
    _maintenance_cache["value"] = dict(config)
    _maintenance_cache["expires_at"] = now_ts + 15
    return config


def get_maintenance_status(config=None):
    config = dict(config or get_maintenance_config())
    now = datetime.now(VN_TIMEZONE)
    close_at = _parse_maintenance_time(config.get("close_at"))
    open_at = _parse_maintenance_time(config.get("open_at"))

    closed = bool(config.get("manual_closed"))
    # Lịch đóng có thể bật máy chủ tự động, lịch mở có thể mở lại kể cả khi
    # công tắc đóng thủ công đang bật. Mốc thời gian đến sau có quyền ưu tiên.
    transitions = []
    if close_at:
        transitions.append((close_at, True, "close"))
    if open_at:
        transitions.append((open_at, False, "open"))
    for when, state, _kind in sorted(transitions, key=lambda item: item[0]):
        if now >= when:
            closed = state

    future = [(when, state, kind) for when, state, kind in transitions if when > now]
    next_transition = min(future, key=lambda item: item[0]) if future else None
    countdown = None
    if next_transition:
        seconds = max(0, int((next_transition[0] - now).total_seconds()))
        if seconds <= 30 * 60:
            countdown = {
                "kind": next_transition[2],
                "target_iso": next_transition[0].isoformat(),
                "seconds": seconds,
                "label": "Máy chủ sẽ đóng để bảo trì" if next_transition[2] == "close" else "Máy chủ sẽ mở trở lại",
            }

    return {
        "closed": closed,
        "message": str(config.get("message") or _maintenance_default_config()["message"]),
        "close_at": close_at.isoformat() if close_at else "",
        "open_at": open_at.isoformat() if open_at else "",
        "close_at_input": close_at.strftime("%Y-%m-%dT%H:%M") if close_at else "",
        "open_at_input": open_at.strftime("%Y-%m-%dT%H:%M") if open_at else "",
        "countdown": countdown,
    }


def _current_session_is_admin():
    if not session.get("user_id"):
        return False
    try:
        return is_admin_user(current_user())
    except Exception:
        return False


def normalize_invite_code(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "")


def generate_invite_code_value(length: int = 10) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


RANK_RANGE_SETTING_KEY = "rank_ranges"
_rank_range_cache = {"value": None, "expires_at": 0.0}


def _validate_rank_ranges(raw_ranges):
    """Validate the 10 rank definitions stored in system_settings."""
    if isinstance(raw_ranges, dict):
        raw_ranges = raw_ranges.get("ranks") or raw_ranges.get("value") or raw_ranges
    if not isinstance(raw_ranges, list) or len(raw_ranges) != 10:
        raise ValueError("Cấu hình khoảng điểm Rank phải có đúng 10 Rank.")

    normalized = []
    previous_max = -1
    required_text_fields = ("name", "short_name", "abbr", "code", "icon", "slug")
    for index, item in enumerate(raw_ranges):
        if not isinstance(item, dict):
            raise ValueError(f"Rank {index + 1} không đúng định dạng.")
        row = dict(item)
        minimum = int(row.get("min"))
        maximum_raw = row.get("max")
        maximum = None if maximum_raw in (None, "", "null") else int(maximum_raw)
        if index == 0 and minimum != 0:
            raise ValueError("Rank đầu tiên phải bắt đầu từ 0 RP.")
        if index > 0 and minimum != previous_max + 1:
            raise ValueError(f"Rank {index + 1} phải bắt đầu từ {previous_max + 1} RP.")
        if index < 9 and maximum is None:
            raise ValueError(f"Rank {index + 1} phải có điểm kết thúc.")
        if maximum is not None and maximum < minimum:
            raise ValueError(f"Khoảng điểm Rank {index + 1} không hợp lệ.")
        if index == 9 and maximum is not None:
            raise ValueError("Rank cuối cùng phải để max = null.")
        for field in required_text_fields:
            row[field] = str(row.get(field) or "").strip()
            if not row[field]:
                raise ValueError(f"Rank {index + 1} thiếu trường {field}.")
        row["min"] = minimum
        row["max"] = maximum
        normalized.append(row)
        previous_max = maximum if maximum is not None else previous_max
    return normalized


def load_rank_ranges(force=False):
    """Always load active Rank ranges from Supabase system_settings."""
    now = time.time()
    if not force and _rank_range_cache["value"] is not None and now < _rank_range_cache["expires_at"]:
        return _rank_range_cache["value"]
    if db is None:
        raise RuntimeError("Chưa cấu hình kết nối Supabase để đọc khoảng điểm Rank.")

    result = execute_query(
        db.table("system_settings").select("setting_value").eq("setting_key", RANK_RANGE_SETTING_KEY).limit(1),
        "load_rank_ranges",
        attempts=3,
    )
    if not result.data:
        # Tự tạo cấu hình lần đầu để không cần chạy hoặc lưu file SQL trên GitHub.
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": RANK_RANGE_SETTING_KEY,
                "setting_value": DEFAULT_RANKS,
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "seed_rank_ranges",
            attempts=3,
        )
        configured = _validate_rank_ranges(DEFAULT_RANKS)
    else:
        stored = result.data[0].get("setting_value")
        if isinstance(stored, str):
            stored = json.loads(stored)
        configured = _validate_rank_ranges(stored)

    _rank_range_cache.update({"value": configured, "expires_at": now + 30})
    return configured


def get_rank_ranges():
    return load_rank_ranges()


def get_rank_info(points: int):
    ranks = load_rank_ranges()
    safe=max(0,int(points or 0)); selected=ranks[0]
    for rank in ranks:
        if safe>=rank["min"]: selected=rank
    result=dict(selected); nxt=next((r for r in ranks if r["min"]>safe),None)
    result["points"]=safe; result["next_rank"]=nxt
    result["points_to_next"]=max(0,nxt["min"]-safe) if nxt else 0
    if nxt:
        span=max(1,nxt["min"]-selected["min"]); result["progress"]=max(0,min(100,round(((safe-selected["min"])/span)*100)))
    else: result["progress"]=100
    return result

def is_goat_player(player, position=None):
    """GOAT is the official level 10 rank (2700+ RP)."""
    return bool(player) and get_rank_info(player.get("rank_points", 0)).get("code") == "GOAT"


def get_player_rank_info(player, position=None):
    return get_rank_info(player.get("rank_points", 0) if player else 0)

def get_rank_name(points:int)->str: return get_rank_info(points)["name"]
def get_rank_display(points:int)->str:
    r=get_rank_info(points); return f'{r["icon"]} {r["name"]}'


def get_team_power_score(team_name):
    """Đọc power_score của CLB trực tiếp từ bảng teams trên Supabase."""
    info = get_db_team_info(team_name) if team_name else None
    if info and info.get("power_score") is not None:
        try:
            return float(info.get("power_score"))
        except (TypeError, ValueError):
            pass
    return 73.33


def get_tier_strength(tier):
    """Return numeric club strength: D=1 ... S+=7."""
    values = {"D": 1, "C": 2, "B": 3, "A": 4, "A+": 5, "S": 6, "S+": 7}
    return values.get(str(tier or "").strip().upper(), 1)


def get_match_difficulty(player, opponent, player_tier, opponent_tier):
    """Combined rank gap and club compensation.

    Positive values mean the player's real matchup is harder; negative values
    mean the player has the easier matchup.
    """
    rank_gap = get_rank_level(opponent.get("rank_points", 0)) - get_rank_level(player.get("rank_points", 0))
    club_compensation = get_tier_strength(player_tier) - get_tier_strength(opponent_tier)
    return rank_gap - club_compensation


def get_difficulty_factor(difficulty, won):
    """Return the requested win/loss coefficient for one player."""
    difficulty = int(difficulty or 0)
    if difficulty >= 3:
        return 1.20 if won else 0.80
    if difficulty >= 1:
        return 1.10 if won else 0.90
    if difficulty <= -3:
        return 0.80 if won else 1.20
    if difficulty <= -1:
        return 0.90 if won else 1.10
    return 1.00


def _match_affects_streak(match):
    details = (match or {}).get("rp_details") or {}
    if not isinstance(details, dict):
        return True
    repeat = details.get("repeat_opponent") or {}
    return not (isinstance(repeat, dict) and repeat.get("streak_eligible") is False)


def get_current_loss_streak(user_id):
    """Đếm số trận thua liên tiếp gần nhất từ lịch sử đã xác nhận."""
    if not user_id or db is None:
        return 0
    try:
        result = execute_query(
            db.table("matches")
            .select("player1_id,player2_id,score1,score2,status,created_at,rp_details")
            .or_(f"player1_id.eq.{user_id},player2_id.eq.{user_id}")
            .eq("status", "confirmed")
            .order("created_at", desc=True)
            .limit(30),
            f"get_loss_streak:{user_id}",
            attempts=2,
        )
    except Exception as exc:
        print(f"get_current_loss_streak warning user={user_id}: {type(exc).__name__}: {exc}")
        return 0

    streak = 0
    for match in result.data or []:
        if not _match_affects_streak(match):
            continue
        score1 = _safe_int(match.get("score1"), -1)
        score2 = _safe_int(match.get("score2"), -1)
        if score1 < 0 or score2 < 0 or score1 == score2:
            break
        is_player1 = str(match.get("player1_id")) == str(user_id)
        lost = (is_player1 and score1 < score2) or ((not is_player1) and score2 < score1)
        if not lost:
            break
        streak += 1
    return streak


def get_loss_recovery_win_step(user_id):
    """Trả 1/2 nếu người chơi đang ở trận thắng phục hồi sau >=5 trận thua."""
    if not user_id or db is None:
        return 0
    try:
        result = execute_query(
            db.table("matches")
            .select("player1_id,player2_id,score1,score2,status,created_at,rp_details")
            .or_(f"player1_id.eq.{user_id},player2_id.eq.{user_id}")
            .eq("status", "confirmed")
            .order("created_at", desc=True)
            .limit(30),
            f"get_loss_recovery:{user_id}", attempts=2,
        )
    except Exception as exc:
        print(f"get_loss_recovery warning user={user_id}: {type(exc).__name__}: {exc}")
        return 0
    outcomes = []
    for match in result.data or []:
        if not _match_affects_streak(match):
            continue
        s1, s2 = _safe_int(match.get("score1"), -1), _safe_int(match.get("score2"), -1)
        if s1 < 0 or s2 < 0 or s1 == s2:
            break
        is_p1 = str(match.get("player1_id")) == str(user_id)
        won = (is_p1 and s1 > s2) or ((not is_p1) and s2 > s1)
        outcomes.append("win" if won else "loss")
    recent_wins = 0
    for outcome in outcomes:
        if outcome != "win": break
        recent_wins += 1
    if recent_wins not in (0, 1):
        return 0
    prior_losses = 0
    for outcome in outcomes[recent_wins:]:
        if outcome != "loss": break
        prior_losses += 1
    return recent_wins + 1 if prior_losses >= 5 else 0


def calculate_deltas(player_a, player_b, score_a: int, score_b: int, team_a=None, team_b=None,
                     team_overall_a=None, team_overall_b=None, team_tier_a=None, team_tier_b=None,
                     rng=None):
    """Lớp tương thích: route cũ gọi như trước, công thức nằm trong rp_engine."""
    player_a_for_rp = dict(player_a or {})
    player_b_for_rp = dict(player_b or {})
    player_a_for_rp["loss_streak"] = get_current_loss_streak(player_a_for_rp.get("id"))
    player_b_for_rp["loss_streak"] = get_current_loss_streak(player_b_for_rp.get("id"))
    player_a_for_rp["loss_recovery_win_step"] = get_loss_recovery_win_step(player_a_for_rp.get("id"))
    player_b_for_rp["loss_recovery_win_step"] = get_loss_recovery_win_step(player_b_for_rp.get("id"))
    return calculate_ranked_deltas(
        player_a_for_rp, player_b_for_rp, score_a, score_b, get_rank_level=get_rank_level,
        team_a=team_a, team_b=team_b, team_overall_a=team_overall_a,
        team_overall_b=team_overall_b, team_tier_a=team_tier_a, team_tier_b=team_tier_b,
        rng=rng,
    )

TEAM_LOGO_BUCKET = "team-logos"
LEAGUE_LOGO_FOLDER = "league-logos"
LEAGUE_LOGO_FILES = {
    "africa": "africa.png",
    "bundesliga": "bundesliga.png",
    "europe": "europe.png",
    "laliga ea sports": "laliga-ea-sports.png",
    "la liga ea sports": "laliga-ea-sports.png",
    "laliga": "laliga-ea-sports.png",
    "ligue 1": "ligue-1.png",
    "ligue1": "ligue-1.png",
    "premier league": "premier-league.png",
    "serie a": "serie-a.png",
    "serie bkt": "serie-bkt.png",
    "sky bet championship": "sky-bet-championship.png",
    "championship": "sky-bet-championship.png",
    "south america": "south-america.png",
    "super lig": "super-lig.png",
    "süper lig": "super-lig.png",
}

def get_league_logo_url(league_name):
    """Tạo URL public tới team-logos/league-logos trên Supabase Storage."""
    import unicodedata
    raw = str(league_name or "").strip()
    if not raw or not supabase_url:
        return ""
    key = " ".join(raw.lower().replace("-", " ").replace("_", " ").split())
    key_ascii = "".join(ch for ch in unicodedata.normalize("NFKD", key) if not unicodedata.combining(ch))
    filename = LEAGUE_LOGO_FILES.get(key) or LEAGUE_LOGO_FILES.get(key_ascii)
    if not filename:
        for alias, candidate in LEAGUE_LOGO_FILES.items():
            if alias in key or alias in key_ascii:
                filename = candidate
                break
    if not filename:
        return ""
    from urllib.parse import quote
    object_path = f"{LEAGUE_LOGO_FOLDER}/{filename}"
    return f"{supabase_url}/storage/v1/object/public/{TEAM_LOGO_BUCKET}/{quote(object_path, safe='/')}"

SMART_RANDOM_MODE = "Smart Rank"


CLUB_TIER_RANGES = {
    "S+": (80.50, 81.60),
    "S": (79.50, 80.49),
    "A+": (78.50, 79.49),
    "A": (77.50, 78.49),
    "B": (76.00, 77.49),
    "C": (74.50, 75.99),
    "D": (73.33, 74.49),
}
CLUB_TIER_ORDER = ["S+", "S", "A+", "A", "B", "C", "D"]

# Tỷ lệ Tier CLB theo từng Rank (khóa là level 0..9 trong code).
# Tổng tỷ lệ của mỗi Rank luôn bằng 100.
RANK_CLUB_TIER_WEIGHTS = {
    0: {"S+": 100},
    1: {"S+": 100},
    2: {"S+": 100},
    3: {"S+": 75, "S": 25},
    4: {"S+": 10, "S": 45, "A+": 45},
    5: {"S+": 5, "S": 20, "A+": 50, "A": 25},
    6: {"S": 5, "A+": 15, "A": 45, "B": 35},
    7: {"A+": 5, "A": 10, "B": 50, "C": 35},
    8: {"B": 10, "C": 55, "D": 35},
    9: {"B": 15, "C": 25, "D": 60},
}


def power_score_to_tier(power_score):
    """Classify one club into S+..D using power_score only."""
    try:
        score = float(power_score)
    except (TypeError, ValueError):
        return "D"
    for tier in CLUB_TIER_ORDER:
        minimum, maximum = CLUB_TIER_RANGES[tier]
        if minimum <= score <= maximum:
            return tier
    if score > CLUB_TIER_RANGES["S+"][1]:
        return "S+"
    return "D"


def _normalize_team_row(row):
    """Chuẩn hóa một dòng CLB lấy trực tiếp từ bảng teams trên Supabase."""
    if not row:
        return None
    name = row.get("team") or row.get("display")
    if not name:
        return None
    try:
        overall = int(row.get("overall") or 0)
    except (TypeError, ValueError):
        return None
    if overall <= 0:
        return None
    return {
        "id": row.get("id"),
        "display": str(name),
        "team": str(name),
        "league": row.get("league") or "",
        "overall": overall,
        "tier": str(row.get("tier") or power_score_to_tier(row.get("power_score"))).strip().upper(),
        "logo_file": row.get("logo_file") or "",
        "logo_url": row.get("logo_url") or "",
        "defence": row.get("defence"),
        "midfield": row.get("midfield"),
        "attack": row.get("attack"),
        "speed": row.get("speed"),
        "strength": row.get("strength"),
        "total_stats": row.get("total_stats"),
        "power_score": row.get("power_score"),
    }


_TEAM_CACHE = {"loaded_at": 0.0, "rows": [], "by_name": {}, "pools": {}}
_TEAM_CACHE_TTL_SECONDS = 30
TEAM_COUNT = 0


def _load_teams_from_supabase(force=False):
    """Chỉ đọc CLB từ Supabase; không còn CSV hoặc teams_data.py dự phòng."""
    global TEAM_COUNT
    now = time.monotonic()
    if not force and _TEAM_CACHE["rows"] and now - _TEAM_CACHE["loaded_at"] < _TEAM_CACHE_TTL_SECONDS:
        return _TEAM_CACHE["rows"]
    if db is None:
        raise RuntimeError("Chưa cấu hình kết nối Supabase để đọc bảng teams.")
    result = execute_query(
        db.table("teams")
        .select("id,league,team,overall,defence,midfield,attack,speed,strength,total_stats,power_score,tier,logo_file,logo_url,is_active")
        .eq("is_active", True),
        "load_teams_from_supabase",
        attempts=3,
    )
    rows = []
    by_name = {}
    pools = {}
    for raw in result.data or []:
        team = _normalize_team_row(raw)
        if not team:
            continue
        rows.append(team)
        by_name[team["team"].casefold()] = team
        pools.setdefault(team["overall"], []).append(team)
    if not rows:
        raise RuntimeError("Bảng teams trên Supabase không có CLB hoạt động.")
    _TEAM_CACHE.update({"loaded_at": now, "rows": rows, "by_name": by_name, "pools": pools})
    TEAM_COUNT = len(rows)
    return rows


def get_random_team_pools():
    """Trả nhóm CLB theo overall, chỉ từ Supabase."""
    _load_teams_from_supabase()
    return _TEAM_CACHE["pools"]


def get_db_team_info(team_name):
    """Tìm CLB theo tên trong dữ liệu Supabase đã cache ngắn hạn."""
    if not team_name:
        return None
    try:
        _load_teams_from_supabase()
        return _TEAM_CACHE["by_name"].get(str(team_name).casefold())
    except Exception as exc:
        print(f"get_db_team_info error: {exc}")
        return None


def get_team_info(team_name):
    return get_db_team_info(team_name)


def get_team_overall(team_name):
    info = get_db_team_info(team_name)
    try:
        return int(info.get("overall")) if info else 0
    except (TypeError, ValueError):
        return 0


def get_team_tier(team_name):
    info = get_db_team_info(team_name)
    return str(info.get("tier") or "") if info else ""


SMART_RANDOM_MODE = "Smart Tier Random"
RECENT_TEAM_EXCLUSION_COUNT = 5
HOST_XP_FACTOR = 0.95
MATCH_MODE_RANKED = "ranked"
MATCH_MODE_FRIENDLY = "friendly"
FRIENDLY_RANDOM3_MODE = "random3_pick1"
FRIENDLY_RANDOM3_NOTE_PREFIX = "FRIENDLY_RANDOM3:"

def build_friendly_random3_state(host_player, guest_player):
    """Chia 3 CLB mỗi bên; tránh đội trong 5 trận gần nhất với đúng đối thủ."""
    if not host_player or not guest_player:
        raise ValueError("Không tải được thông tin Rank của hai người chơi.")

    all_teams = _all_random_teams()
    if len(all_teams) < 6:
        raise ValueError("Cần ít nhất 6 CLB để dùng Random 3 chọn 1.")

    picked_names = []
    selected_history = {
        "host": _recent_pair_team_names(host_player.get("id"), guest_player.get("id")),
        "guest": _recent_pair_team_names(guest_player.get("id"), host_player.get("id")),
    }

    def pack(team):
        return {
            "name": team.get("display"),
            "overall": int(team.get("overall") or 0),
            "total_stats": int(team.get("total_stats") or 0),
            "tier": str(team.get("tier") or "").strip().upper(),
            "logo": team.get("logo_url") or "",
            "league": team.get("league") or "",
        }

    def pick_three(player, side):
        options = []
        opponent_id = guest_player.get("id") if side == "host" else host_player.get("id")
        for _ in range(3):
            # picked_names là danh sách cấm cứng để 6 lựa chọn của hai bên
            # không bao giờ trùng nhau. Lịch sử đối đầu là danh sách cấm mềm:
            # hệ thống ưu tiên tránh, nhưng có thể nới khi pool Tier đã cạn.
            team, _, _, _ = _pick_rank_team(
                player,
                all_teams,
                extra_excluded=picked_names,
                opponent_id=opponent_id,
                include_pair_history=True,
            )
            name = team.get("display")
            picked_names.append(name)
            options.append(pack(team))
        return options

    host_level = get_rank_level(host_player.get("rank_points", 0))
    guest_level = get_rank_level(guest_player.get("rank_points", 0))
    rank_ranges = load_rank_ranges()

    host_options = pick_three(host_player, "host")
    guest_options = pick_three(guest_player, "guest")
    all_options = host_options + guest_options
    normalized_names = [_normalize_team_name(item.get("name")) for item in all_options]
    if len(all_options) != 6 or len(set(normalized_names)) != 6:
        raise ValueError("Không thể tạo 6 CLB khác nhau cho Random 3 chọn 1. Vui lòng thử lại.")

    return {
        "mode": FRIENDLY_RANDOM3_MODE,
        "distribution": "rank_weighted",
        "host_rank": rank_ranges[host_level]["name"],
        "guest_rank": rank_ranges[guest_level]["name"],
        "host_rank_points": int(host_player.get("rank_points") or 0),
        "guest_rank_points": int(guest_player.get("rank_points") or 0),
        "host_tier_weights": get_rank_tier_weights(host_level),
        "guest_tier_weights": get_rank_tier_weights(guest_level),
        "host_options": host_options,
        "guest_options": guest_options,
        "host_choice": None,
        "guest_choice": None,
    }

def encode_friendly_random3_state(state):
    return FRIENDLY_RANDOM3_NOTE_PREFIX + json.dumps(state, ensure_ascii=False, separators=(",", ":"))

def decode_friendly_random3_state(note):
    text = str(note or "")
    if not text.startswith(FRIENDLY_RANDOM3_NOTE_PREFIX):
        return None
    try:
        data = json.loads(text[len(FRIENDLY_RANDOM3_NOTE_PREFIX):])
        return data if data.get("mode") == FRIENDLY_RANDOM3_MODE else None
    except Exception:
        return None


def get_rank_level(points: int) -> int:
    """Return rank level from 0 (lowest) to 9 (highest)."""
    safe_points = max(0, int(points or 0))
    level = 0
    for index, rank in enumerate(load_rank_ranges()):
        if safe_points >= rank["min"]:
            level = index
    return level


RANK_TIER_SETTING_KEY = "rank_club_tier_weights"
_rank_tier_config_cache = {"value": None, "expires_at": 0.0}


def _validate_rank_tier_weights(raw_weights):
    """Validate imported 1..10 Rank mapping and convert it to internal 0..9 levels."""
    if not isinstance(raw_weights, dict):
        raise ValueError("RANK_CLUB_TIER_WEIGHTS phải là một dictionary.")

    normalized = {}
    for rank_number in range(1, len(load_rank_ranges()) + 1):
        row = raw_weights.get(rank_number)
        if row is None:
            row = raw_weights.get(str(rank_number))
        if not isinstance(row, dict) or not row:
            raise ValueError(f"Rank {rank_number} chưa có tỷ lệ Tier CLB.")

        clean_row = {}
        total = 0
        for tier, percent in row.items():
            tier = str(tier).strip().upper()
            if tier not in CLUB_TIER_ORDER:
                raise ValueError(f"Rank {rank_number} có Tier không hợp lệ: {tier}.")
            if isinstance(percent, bool) or not isinstance(percent, (int, float)):
                raise ValueError(f"Tỷ lệ {tier} của Rank {rank_number} phải là số.")
            percent = int(percent)
            if percent < 0 or percent > 100:
                raise ValueError(f"Tỷ lệ {tier} của Rank {rank_number} phải từ 0 đến 100.")
            if percent:
                clean_row[tier] = percent
                total += percent

        if total != 100:
            raise ValueError(f"Tổng tỷ lệ Rank {rank_number} đang là {total}%, bắt buộc phải bằng 100%.")
        normalized[rank_number - 1] = clean_row
    return normalized


def load_rank_tier_weights(force=False):
    now = time.time()
    if not force and _rank_tier_config_cache["value"] is not None and now < _rank_tier_config_cache["expires_at"]:
        return _rank_tier_config_cache["value"]

    configured = RANK_CLUB_TIER_WEIGHTS
    if db is not None:
        try:
            result = execute_query(
                db.table("system_settings").select("setting_value").eq("setting_key", RANK_TIER_SETTING_KEY).limit(1),
                "load_rank_tier_weights",
                attempts=2,
            )
            if result.data:
                stored = result.data[0].get("setting_value")
                if isinstance(stored, str):
                    stored = json.loads(stored)
                configured = _validate_rank_tier_weights(stored)
        except Exception as exc:
            print(f"load_rank_tier_weights fallback warning: {exc}")

    _rank_tier_config_cache.update({"value": configured, "expires_at": now + 30})
    return configured


def get_rank_tier_weights(level: int):
    """Return the active Admin-configured Tier percentages for one rank level."""
    safe_level = max(0, min(len(load_rank_ranges()) - 1, int(level or 0)))
    return load_rank_tier_weights().get(safe_level, RANK_CLUB_TIER_WEIGHTS[safe_level])


def _all_random_teams():
    pools = get_random_team_pools()
    teams = []
    for pool in pools.values():
        for team in pool:
            team = dict(team)
            try:
                team["power_score"] = float(team.get("power_score"))
            except (TypeError, ValueError):
                team["power_score"] = round(73.33 + (int(team.get("overall", 73)) - 73) * 0.75, 2)
            # Tier is always calculated from power_score, never trusted from stale CSV data.
            team["tier"] = power_score_to_tier(team["power_score"])
            teams.append(team)
    return teams


def _normalize_team_name(name):
    return " ".join(str(name or "").strip().casefold().split())


def _is_random3_match(match):
    note = str(match.get("note") or "").casefold()
    return "random 3 chọn 1" in note or FRIENDLY_RANDOM3_MODE in note


def _recent_pair_team_names(user_id, opponent_id, limit=RECENT_TEAM_EXCLUSION_COUNT):
    """CLB người chơi đã dùng trong N trận confirmed gần nhất với đúng đối thủ.

    Lịch sử dùng chung cho Rank thường và Random 3 chọn 1. Khi đổi đối thủ,
    danh sách chống lặp tự tách theo cặp người chơi mới.
    """
    if not user_id or not opponent_id:
        return []
    names = []
    try:
        matches = sorted(
            list_matches(),
            key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""),
            reverse=True,
        )
        for match in matches:
            if str(match.get("status") or "").lower() != "confirmed":
                continue
            p1 = match.get("player1_id")
            p2 = match.get("player2_id")
            if p1 == user_id and p2 == opponent_id:
                name = match.get("team1")
            elif p2 == user_id and p1 == opponent_id:
                name = match.get("team2")
            else:
                continue
            if name:
                names.append(str(name).strip())
            if len(names) >= limit:
                break
    except Exception as exc:
        print(f"recent_pair_team_history warning: {exc}")
    return names


def _teams_in_tiers(teams, tiers, excluded_names=None):
    allowed = {str(tier).upper() for tier in (tiers or [])}
    excluded = {_normalize_team_name(name) for name in (excluded_names or []) if name}
    return [
        team for team in teams
        if str(team.get("tier") or "").upper() in allowed
        and _normalize_team_name(team.get("display")) not in excluded
    ]


def _weighted_tier_choice(tier_weights, teams, excluded_names):
    """Pick a Tier by configured percentage, then return clubs in that Tier.

    If a configured Tier has no available club after anti-repeat filtering,
    the remaining available percentages are automatically re-normalized.
    """
    available = []
    for tier, weight in (tier_weights or {}).items():
        candidates = _teams_in_tiers(teams, [tier], excluded_names)
        if candidates and float(weight or 0) > 0:
            available.append((tier, float(weight), candidates))
    if not available:
        return None, []

    roll = random.random() * sum(weight for _, weight, _ in available)
    cumulative = 0.0
    for tier, weight, candidates in available:
        cumulative += weight
        if roll <= cumulative:
            return tier, candidates
    tier, _, candidates = available[-1]
    return tier, candidates

def _nearest_rank_tier_candidates(tier_weights, teams, excluded_names):
    """Tìm CLB ở Tier gần nhất khi các Tier có tỷ lệ đã hết lựa chọn.

    Danh sách cấm vẫn được tôn trọng. Cơ chế này chỉ mở rộng sang Tier liền kề,
    tránh làm Random 3 chọn 1 thất bại khi một Rank chỉ được cấu hình 1 Tier
    nhưng Tier đó không đủ 6 CLB khác nhau cho cả hai người.
    """
    excluded = {_normalize_team_name(name) for name in (excluded_names or []) if name}
    available_by_tier = {}
    for team in teams:
        if _normalize_team_name(team.get("display")) in excluded:
            continue
        tier = str(team.get("tier") or "").upper()
        if tier in CLUB_TIER_ORDER:
            available_by_tier.setdefault(tier, []).append(team)

    preferred_indexes = [
        CLUB_TIER_ORDER.index(str(tier).upper())
        for tier, weight in (tier_weights or {}).items()
        if str(tier).upper() in CLUB_TIER_ORDER and float(weight or 0) > 0
    ]
    if not preferred_indexes:
        preferred_indexes = list(range(len(CLUB_TIER_ORDER)))

    ranked_tiers = []
    for tier, candidates in available_by_tier.items():
        tier_index = CLUB_TIER_ORDER.index(tier)
        distance = min(abs(tier_index - preferred) for preferred in preferred_indexes)
        ranked_tiers.append((distance, tier_index, tier, candidates))
    if not ranked_tiers:
        return None, []

    ranked_tiers.sort(key=lambda item: (item[0], item[1]))
    nearest_distance = ranked_tiers[0][0]
    nearest = [item for item in ranked_tiers if item[0] == nearest_distance]
    _, _, selected_tier, candidates = random.choice(nearest)
    return selected_tier, candidates


def _pick_rank_team(player, all_teams, extra_excluded=None, opponent_id=None, include_pair_history=True):
    level = get_rank_level(player.get("rank_points", 0))
    tier_weights = get_rank_tier_weights(level)
    recent = (
        _recent_pair_team_names(player.get("id"), opponent_id)
        if include_pair_history and opponent_id
        else []
    )
    # extra là danh sách cấm cứng (đội đã xuất hiện trong lượt hiện tại).
    # recent là danh sách cấm mềm (đội đã dùng trong lịch sử đối đầu gần đây).
    extra = list(extra_excluded or [])
    strict_excluded = list(dict.fromkeys(recent + extra))

    selected_tier, candidates = _weighted_tier_choice(tier_weights, all_teams, strict_excluded)
    if not candidates:
        selected_tier, candidates = _nearest_rank_tier_candidates(
            tier_weights, all_teams, strict_excluded
        )

    # Nếu lịch sử 5 trận làm cạn toàn bộ pool, nới riêng lịch sử nhưng vẫn
    # tuyệt đối không cho trùng đội trong 6 lựa chọn của lượt hiện tại.
    if not candidates and recent:
        selected_tier, candidates = _weighted_tier_choice(tier_weights, all_teams, extra)
        if not candidates:
            selected_tier, candidates = _nearest_rank_tier_candidates(
                tier_weights, all_teams, extra
            )

    if not candidates:
        raise ValueError(
            f"Không đủ CLB hoạt động để tạo lựa chọn cho rank {load_rank_ranges()[level]['name']}."
        )
    return random.choice(candidates), selected_tier, tier_weights, recent


def get_smart_random_rule(player_a, player_b):
    level_a = get_rank_level(player_a.get("rank_points", 0))
    level_b = get_rank_level(player_b.get("rank_points", 0))
    return {
        "level_a": level_a,
        "level_b": level_b,
        "rank_gap": abs(level_a - level_b),
        "advantage": "Mỗi Rank có tỷ lệ xuất hiện Tier CLB riêng.",
        "summary": "Random theo tỷ lệ Tier riêng; tránh CLB đã dùng trong 5 trận confirmed gần nhất với đúng đối thủ, dùng chung cả Rank thường và Random 3 chọn 1; hai bên không trùng CLB.",
        "rule_a": get_rank_tier_weights(level_a),
        "rule_b": get_rank_tier_weights(level_b),
    }


def smart_random_team_pair(player_a, player_b):
    """Random clubs from rank-linked S+..D tiers with anti-repeat protection."""
    all_teams = _all_random_teams()
    if len(all_teams) < 2:
        raise ValueError("Không đủ dữ liệu CLB để Smart Random.")

    team_a, tier_a_selected, weights_a, recent_a = _pick_rank_team(
        player_a, all_teams, opponent_id=player_b.get("id")
    )
    team_b, tier_b_selected, weights_b, recent_b = _pick_rank_team(
        player_b,
        all_teams,
        extra_excluded=[team_a.get("display")],
        opponent_id=player_a.get("id"),
    )

    if str(team_a.get("display")).casefold() == str(team_b.get("display")).casefold():
        allowed_b = set(weights_b.keys())
        excluded_b = {
            _normalize_team_name(name)
            for name in list(recent_b) + [team_a.get("display")]
            if name
        }
        alternatives = [
            team for team in all_teams
            if _normalize_team_name(team.get("display")) not in excluded_b
            and str(team.get("tier") or "").upper() in allowed_b
        ]
        if not alternatives:
            raise ValueError("Không tìm được hai CLB khác nhau trong các Tier phù hợp.")
        team_b = random.choice(alternatives)

    return {
        "mode": SMART_RANDOM_MODE,
        "team_a": team_a["display"],
        "team_b": team_b["display"],
        "overall_a": int(team_a["overall"]),
        "overall_b": int(team_b["overall"]),
        "total_stats_a": int(team_a.get("total_stats") or 0),
        "total_stats_b": int(team_b.get("total_stats") or 0),
        "power_score_a": float(team_a.get("power_score", 0)),
        "power_score_b": float(team_b.get("power_score", 0)),
        "tier_a": team_a["tier"],
        "tier_b": team_b["tier"],
        "logo_a": team_a.get("logo_url") or "",
        "logo_b": team_b.get("logo_url") or "",
        "league_a": team_a.get("league") or "",
        "league_b": team_b.get("league") or "",
        "team_id_a": team_a.get("id"),
        "team_id_b": team_b.get("id"),
        "band_a": tier_a_selected,
        "band_b": tier_b_selected,
        "recent_excluded_a": recent_a,
        "recent_excluded_b": recent_b,
        "rank_gap": abs(get_rank_level(player_a.get("rank_points", 0)) - get_rank_level(player_b.get("rank_points", 0))),
        "summary": get_smart_random_rule(player_a, player_b)["summary"],
    }


def get_available_team_tiers():
    """Return active club tiers for the friendly-mode selector."""
    tiers = []
    for team in _all_random_teams():
        tier = str(team.get("tier") or "").strip().upper()
        if tier and tier not in tiers:
            tiers.append(tier)
    preferred = CLUB_TIER_ORDER
    return sorted(tiers, key=lambda value: (preferred.index(value) if value in preferred else 99, value))


def friendly_random_team_pair(tier, excluded_names=None):
    """Pick two different active clubs from the selected tier; no history is created."""
    selected_tier = str(tier or "").strip().upper()
    if not selected_tier:
        raise ValueError("Hãy chọn Tier CLB cho trận giao hữu.")
    excluded = {str(name or "").casefold() for name in (excluded_names or []) if name}
    candidates = [
        team for team in _all_random_teams()
        if str(team.get("tier") or "").strip().upper() == selected_tier
        and _normalize_team_name(team.get("display")) not in excluded
    ]
    if len(candidates) < 2 and excluded:
        candidates = [
            team for team in _all_random_teams()
            if str(team.get("tier") or "").strip().upper() == selected_tier
        ]
    if len(candidates) < 2:
        raise ValueError(f"Tier {selected_tier} cần ít nhất 2 CLB khác nhau để đá giao hữu.")
    team_a, team_b = random.sample(candidates, 2)
    return {
        "mode": MATCH_MODE_FRIENDLY,
        "selected_tier": selected_tier,
        "team_a": team_a["display"],
        "team_b": team_b["display"],
        "overall_a": int(team_a["overall"]),
        "overall_b": int(team_b["overall"]),
        "total_stats_a": int(team_a.get("total_stats") or 0),
        "total_stats_b": int(team_b.get("total_stats") or 0),
        "power_score_a": float(team_a.get("power_score", 0)),
        "power_score_b": float(team_b.get("power_score", 0)),
        "tier_a": team_a.get("tier") or selected_tier,
        "tier_b": team_b.get("tier") or selected_tier,
        "logo_a": team_a.get("logo_url") or "",
        "logo_b": team_b.get("logo_url") or "",
        "league_a": team_a.get("league") or "",
        "league_b": team_b.get("league") or "",
    }


def apply_host_xp_factor(delta, factor=HOST_XP_FACTOR):
    """Apply the room-host coefficient to the absolute RP change."""
    try:
        safe_factor = float(factor or HOST_XP_FACTOR)
    except (TypeError, ValueError):
        safe_factor = HOST_XP_FACTOR
    value = int(delta or 0)
    if value <= 0:
        return value
    adjusted = round(value * safe_factor)
    return max(1, adjusted)


def require_db():
    if db is None:
        raise RuntimeError("Supabase chưa được cấu hình. Kiểm tra file .env.")


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def get_device_id():
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        device_id = getattr(g, "new_device_id", None)
    if not device_id:
        device_id = str(uuid.uuid4())
        g.new_device_id = device_id
    return device_id


@app.after_request
def set_device_cookie(response):
    device_id = getattr(g, "new_device_id", None)
    if device_id:
        response.set_cookie(
            DEVICE_COOKIE_NAME,
            device_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="Lax",
        )

    # CSS/JS có APP_VERSION trong URL nên có thể cache dài và immutable.
    # Ảnh rank/giao diện giữ 30 ngày để giảm tải nhưng vẫn cho phép thay ảnh
    # cùng tên mà không phải chờ một năm.
    if request.endpoint == "static" or request.path.startswith("/static/"):
        static_path = request.path.lower()
        if static_path.endswith((".css", ".js")):
            cache_control = "public, max-age=31536000, immutable"
        elif static_path.startswith("/static/ranks/") or static_path.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".ico")
        ):
            cache_control = "public, max-age=2592000, stale-while-revalidate=604800"
        else:
            cache_control = "public, max-age=604800, stale-while-revalidate=86400"
        response.headers["Cache-Control"] = cache_control
        response.headers.setdefault("Vary", "Accept-Encoding")
    return response


# =========================
# Database helpers
# =========================

def get_user_by_username(username):
    """Find a user by username without creating extra Supabase clients."""
    require_db()
    normalized = str(username or "").strip()
    if not normalized:
        return None

    result = execute_query(
        db.table("users").select("*").ilike("username", normalized).limit(20),
        "get_user_by_username",
    )
    target = normalized.casefold()
    return next(
        (
            row for row in (result.data or [])
            if str(row.get("username") or "").strip().casefold() == target
        ),
        None,
    )


def calculated_total_matches(player):
    """Nguồn chuẩn duy nhất: tổng trận = thắng + hòa + thua."""
    player = player or {}
    return max(0, int(player.get("wins", 0) or 0)) + max(0, int(player.get("draws", 0) or 0)) + max(0, int(player.get("losses", 0) or 0))


def normalize_player_match_totals(player):
    item = dict(player or {})
    item["total_matches"] = calculated_total_matches(item)
    return item


def get_user(user_id):
    require_db()
    result = execute_query(
        db.table("users").select("*").eq("id", user_id).limit(1),
        "get_user",
    )
    return normalize_player_match_totals(result.data[0]) if result.data else None


def is_user_online_now(user):
    seen = parse_dt((user or {}).get("last_seen_at"))
    cutoff = now_dt() - timedelta(seconds=ONLINE_TIMEOUT_SECONDS)
    return bool((user or {}).get("is_online")) and bool(seen) and seen >= cutoff


def _player_ranking_sort_key(player):
    points = int(player.get("rank_points", 0) or 0)
    wins = int(player.get("wins", 0) or 0)
    goals_for = int(player.get("goals_for", 0) or 0)
    goals_against = int(player.get("goals_against", 0) or 0)
    total_matches = calculated_total_matches(player)
    name = str(player.get("display_name") or player.get("username") or "").casefold()
    return (-points, -wins, -(goals_for - goals_against), -goals_for, -total_matches, name)


def list_players(include_admin=False):
    require_db()
    cached = cache_get("_rz_players_all")
    if cached is None:
        shared = ttl_cache_get("players_raw")
        if shared is None:
            result = execute_query(
                db.table("users").select("*").order("rank_points", desc=True),
                "list_players",
            )
            shared = result.data or []
            ttl_cache_set("players_raw", shared, 8)
        cached = [dict(row) for row in shared]
        cache_set("_rz_players_all", cached)

    players = cached if include_admin else [p for p in cached if p.get("role") == "player"]
    safe = []
    for player in players:
        item = normalize_player_match_totals(player)
        item["is_online"] = is_user_online_now(item)
        safe.append(item)

    # Xếp hạng ổn định khi nhiều người bằng điểm: thắng, hiệu số, bàn thắng, số trận.
    achievement_map = list_user_achievement_map()
    if not include_admin:
        safe.sort(key=_player_ranking_sort_key)
        for position, item in enumerate(safe, 1):
            item["position"] = position
            item["rank_info"] = get_player_rank_info(item, position)
            decorate_player_achievements(item, position, achievement_map)
    else:
        for item in safe:
            item["rank_info"] = get_rank_info(item.get("rank_points", 0))
            decorate_player_achievements(item, None, achievement_map)

    # Gắn mỹ phẩm hồ sơ theo lô để Players/BXH/Dashboard dùng chung,
    # tránh truy vấn N+1 cho từng người chơi.
    try:
        avatar_frame_map = profile_equipment_service.build_avatar_frame_map(safe)
        name_style_map = profile_equipment_service.build_name_style_map(safe)
        profile_badge_map = profile_equipment_service.build_profile_badge_map(safe)
    except Exception as exc:
        app.logger.debug("Player cosmetic map fallback: %s", exc)
        avatar_frame_map = {}
        name_style_map = {}
        profile_badge_map = {}
    for item in safe:
        user_id = str(item.get("id"))
        item["avatar_frame"] = avatar_frame_map.get(user_id)
        item["name_style"] = name_style_map.get(user_id)
        item["profile_badge"] = profile_badge_map.get(user_id)
        metadata = (item.get("name_style") or {}).get("metadata") if isinstance(item.get("name_style"), dict) else {}
        item["name_style_class"] = str((metadata or {}).get("css_class") or "").strip()

    return safe


def users_map():
    cached = cache_get("_rz_users_map")
    if cached is not None:
        return cached

    mapped = {user["id"]: user for user in list_players(include_admin=True)}
    return cache_set("_rz_users_map", mapped)


def get_device_link(device_id):
    result = db.table("user_devices").select("*").eq("device_id", device_id).limit(1).execute()
    return result.data[0] if result.data else None


def is_admin_managed_test_account(user):
    """Tài khoản do Admin tạo/import: không bị khóa theo thiết bị hoặc cảnh báo trùng IP."""
    marker = str((user or {}).get("register_ip") or "").strip().upper()
    return marker.startswith("ADMIN_TEST") or marker.startswith("ADMIN_CREATED")


IP_WARNING_SETTING_KEY = "duplicate_ip_warning_config"
_ip_warning_config_cache = {"value": None, "expires_at": 0.0}


def get_duplicate_ip_warning_config(force=False):
    """Cấu hình cảnh báo IP: bật/tắt toàn cục và danh sách tài khoản tin cậy."""
    now = time.time()
    if not force and _ip_warning_config_cache["value"] is not None and now < _ip_warning_config_cache["expires_at"]:
        return dict(_ip_warning_config_cache["value"])

    config = {"enabled": True, "ignore_admin_managed": True, "trusted_user_ids": []}
    if db is not None:
        try:
            result = execute_query(
                db.table("system_settings").select("setting_value")
                .eq("setting_key", IP_WARNING_SETTING_KEY).limit(1),
                "load_duplicate_ip_warning_config", attempts=2,
            )
            stored = (result.data or [{}])[0].get("setting_value") if result.data else {}
            if isinstance(stored, dict):
                config["enabled"] = bool(stored.get("enabled", True))
                config["ignore_admin_managed"] = bool(stored.get("ignore_admin_managed", True))
                config["trusted_user_ids"] = sorted({str(x) for x in (stored.get("trusted_user_ids") or []) if x})
        except Exception as exc:
            print(f"duplicate ip config warning: {exc}")

    _ip_warning_config_cache.update({"value": dict(config), "expires_at": now + 30})
    return config


def user_ignored_for_duplicate_ip(user, config=None):
    config = config or get_duplicate_ip_warning_config()
    if not user:
        return False
    user_id = str(user.get("id") or "")
    if user_id and user_id in set(config.get("trusted_user_ids") or []):
        return True
    if config.get("ignore_admin_managed", True):
        return user.get("role") == "admin" or is_admin_user(user) or is_admin_managed_test_account(user)
    return False


def link_device_to_user(user):
    # Admin chính và tài khoản do Admin tạo/import không bị giới hạn thiết bị/IP.
    # Đây chỉ là ngoại lệ xác thực; mọi trận Rank vẫn tính W/H/B và RP bình thường.
    if user.get("role") == "admin" or is_admin_managed_test_account(user):
        return True, ""

    device_id = get_device_id()
    link = get_device_link(device_id)

    if link and link["user_id"] != user["id"]:
        return False, "Thiết bị này đã được liên kết với một tài khoản player khác."

    ip = get_client_ip()
    user_agent = request.headers.get("User-Agent", "")

    if not link:
        execute_query(
            db.table("user_devices").insert({
                "user_id": user["id"],
                "device_id": device_id,
                "ip_address": ip,
                "user_agent": user_agent,
                "last_seen_at": now_iso(),
            }),
            "link_device_create",
        )
    else:
        execute_query(
            db.table("user_devices").update({
                "ip_address": ip,
                "user_agent": user_agent,
                "last_seen_at": now_iso(),
            }).eq("id", link["id"]),
            "link_device_update",
        )

    return True, ""


def device_can_register():
    device_id = get_device_id()
    link = get_device_link(device_id)
    if link:
        return False, "Thiết bị này đã có tài khoản player. Mỗi thiết bị chỉ được tạo 1 tài khoản."

    ip = get_client_ip()
    ua = request.headers.get("User-Agent", "")

    # Chặn mềm: cùng IP + cùng User Agent đã từng đăng ký.
    result = (
        db.table("users")
        .select("id")
        .eq("role", "player")
        .eq("register_ip", ip)
        .eq("register_user_agent", ua)
        .limit(1)
        .execute()
    )

    if result.data:
        return False, "Thiết bị/trình duyệt này có dấu hiệu đã đăng ký tài khoản player."

    return True, ""



def list_all_users():
    require_db()
    result = execute_query(
        db.table("users").select("*").order("created_at", desc=True),
        "list_all_users",
    )
    return result.data or []


def log_admin_action(action, target_type="system", target_id=None, target_label="", details=""):
    """Ghi nhật ký quản trị; lỗi ghi log không được làm hỏng thao tác chính."""
    try:
        actor = current_user()
        if not actor or not is_admin_user(actor):
            return
        execute_query(
            db.table("admin_activity_logs").insert({
                "admin_user_id": actor.get("id"),
                "admin_name": actor.get("username") or actor.get("display_name") or "Admin",
                "action": str(action)[:80],
                "target_type": str(target_type)[:50],
                "target_id": str(target_id)[:120] if target_id else None,
                "target_label": str(target_label)[:160] if target_label else None,
                "details": str(details)[:1000] if details else None,
                "ip_address": get_client_ip(),
            }),
            "log_admin_action",
            attempts=2,
        )
    except Exception as exc:
        print(f"Admin audit log warning: {exc}")


def existing_user_id(user_id):
    """Trả về UUID chỉ khi người dùng thực sự còn tồn tại trong public.users."""
    if not user_id:
        return None
    try:
        result = execute_query(
            db.table("users").select("id").eq("id", user_id).limit(1),
            "existing_user_id",
            attempts=1,
        )
        rows = result.data or []
        return rows[0].get("id") if rows else None
    except Exception as exc:
        print(f"existing_user_id warning: {exc}")
        return None


def create_admin_announcement(title, message, admin_user_id=None):
    """Tạo thông báo và tự phục hồi khi khóa ngoại admin cũ bị lệch.

    Một số dự án nâng cấp từ phiên bản cũ còn giữ session/admin UUID không còn
    tồn tại trong public.users. Khi đó Postgres trả mã 23503. Thông báo không
    bắt buộc phải có admin_user_id nên ta thử lại với NULL thay vì gây lỗi 500.
    """
    payload = {
        "admin_user_id": existing_user_id(admin_user_id),
        "title": title,
        "message": message,
        "is_active": True,
    }
    try:
        return db.table("admin_announcements").insert(payload).execute()
    except Exception as exc:
        error_code = str(getattr(exc, "code", "") or "")
        error_text = str(exc)
        if payload["admin_user_id"] and (error_code == "23503" or "23503" in error_text):
            payload["admin_user_id"] = None
            return db.table("admin_announcements").insert(payload).execute()
        raise


def list_admin_activity_logs(limit=150):
    try:
        result = execute_query(
            db.table("admin_activity_logs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit),
            "list_admin_activity_logs",
        )
        return result.data or []
    except Exception as exc:
        print(f"list_admin_activity_logs warning: {exc}")
        return []


def get_password_reset_request(request_id):
    result = execute_query(
        db.table("password_reset_requests").select("*").eq("id", request_id).limit(1),
        "get_password_reset_request",
    )
    return result.data[0] if result.data else None


def list_password_reset_requests(status=None, limit=100):
    try:
        query = (
            db.table("password_reset_requests")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            query = query.eq("status", status)
        result = execute_query(query, "list_password_reset_requests")
        rows = [dict(row) for row in (result.data or [])]
        users = users_map()
        for row in rows:
            user = users.get(row.get("user_id"), {})
            row["current_username"] = user.get("username") or row.get("username_snapshot") or "-"
            row["current_zalo_name"] = user.get("zalo_name") or row.get("zalo_name_snapshot") or "-"
        return rows
    except Exception as exc:
        print(f"list_password_reset_requests warning: {exc}")
        return []


def list_user_devices():
    """Lấy IP thiết bị và lưu trạng thái tải để Admin không hiểu nhầm dữ liệu rỗng."""
    require_db()
    list_user_devices.last_status = {
        "ok": False,
        "row_count": 0,
        "error": None,
        "source": "user_devices",
    }
    try:
        result = execute_query(
            db.table("user_devices")
            .select("user_id,ip_address,last_seen_at,created_at")
            .order("last_seen_at", desc=True),
            "list_user_devices",
        )
        rows = result.data or []
        list_user_devices.last_status = {
            "ok": True,
            "row_count": len(rows),
            "error": None,
            "source": "user_devices",
        }
        return rows
    except Exception as exc:
        # Không làm sập trang Admin, nhưng phải đưa trạng thái lỗi ra giao diện.
        message = str(exc).strip() or exc.__class__.__name__
        list_user_devices.last_status = {
            "ok": False,
            "row_count": 0,
            "error": message[:240],
            "source": "register_ip_only",
        }
        print(f"list_user_devices warning: {exc}")
        return []


list_user_devices.last_status = {"ok": None, "row_count": 0, "error": None, "source": "not_loaded"}


def decorate_admin_users(users):
    """Bổ sung toàn bộ IP và trạng thái trùng IP cho danh sách Admin.

    Tài khoản tin cậy/cấu hình tắt cảnh báo chỉ ảnh hưởng màu cảnh báo, không được
    loại khỏi dữ liệu đối chiếu. Nhờ vậy bộ lọc "Chỉ hiện IP trùng" luôn thấy đủ.
    """
    rows = [dict(user) for user in users]
    for row in rows:
        row["admin_permissions"] = _admin_permissions(row)

    devices = list_user_devices()
    config = get_duplicate_ip_warning_config()
    warnings_enabled = bool(config.get("enabled", True))
    known_ips_by_user = {str(user.get("id")): set() for user in rows}
    latest_ip_by_user = {}
    row_by_id = {str(user.get("id")): user for user in rows}

    # Luôn thu thập IP đăng ký, kể cả tài khoản được tin cậy/được Admin tạo.
    for user in rows:
        user_id = str(user.get("id") or "")
        register_ip = str(user.get("register_ip") or "").strip()
        if user_id and register_ip and not register_ip.upper().startswith(("ADMIN_TEST", "ADMIN_CREATED")):
            known_ips_by_user.setdefault(user_id, set()).add(register_ip)

    # Luôn thu thập IP thiết bị. Dòng đầu tiên là IP mới nhất do truy vấn đã sort desc.
    for device in devices:
        user_id = str(device.get("user_id") or "")
        ip = str(device.get("ip_address") or "").strip()
        if not user_id or not ip or user_id not in row_by_id:
            continue
        known_ips_by_user.setdefault(user_id, set()).add(ip)
        latest_ip_by_user.setdefault(user_id, ip)

    ip_owners = {}
    for user_id, ip_values in known_ips_by_user.items():
        for ip in ip_values:
            ip_owners.setdefault(ip, set()).add(user_id)

    username_by_id = {str(user.get("id")): user.get("username", "-") for user in rows}
    for user in rows:
        user_id = str(user.get("id") or "")
        known_ips = sorted(known_ips_by_user.get(user_id, set()))
        duplicate_ips = [ip for ip in known_ips if len(ip_owners.get(ip, set())) > 1]
        duplicate_accounts = sorted({
            username_by_id.get(owner_id, "-")
            for ip in duplicate_ips
            for owner_id in ip_owners.get(ip, set())
            if owner_id != user_id
        })
        trusted = user_ignored_for_duplicate_ip(user, config)
        detected = bool(duplicate_accounts)

        user["latest_ip"] = latest_ip_by_user.get(user_id) or user.get("register_ip") or "-"
        user["known_ips"] = known_ips
        user["duplicate_ips"] = duplicate_ips
        user["duplicate_ip_count"] = max([len(ip_owners.get(ip, set())) for ip in duplicate_ips] or [0])
        user["duplicate_ip_accounts"] = duplicate_accounts
        user["duplicate_ip_detected"] = detected
        user["duplicate_ip_trusted"] = trusted
        user["duplicate_ip_warning_visible"] = detected and warnings_enabled and not trusted

    return rows

def build_duplicate_ip_groups(users):
    """Gom các IP đang được từ 2 tài khoản trở lên sử dụng để Admin dễ kiểm tra clone."""
    ip_users = {}

    for user in users:
        user_id = str(user.get("id") or "")
        if not user_id:
            continue
        for ip in user.get("known_ips") or []:
            normalized_ip = (ip or "").strip()
            if not normalized_ip:
                continue
            ip_users.setdefault(normalized_ip, {})[user_id] = user

    groups = []
    for ip, owners in ip_users.items():
        if len(owners) < 2:
            continue

        accounts = sorted(
            [
                {
                    "id": owner.get("id"),
                    "username": owner.get("username") or "-",
                    "display_name": owner.get("display_name") or owner.get("username") or "-",
                    "account_status": owner.get("account_status") or "approved",
                    "role": owner.get("role") or "player",
                    "admin_level": owner.get("admin_level") or "none",
                }
                for owner in owners.values()
            ],
            key=lambda item: item["username"].lower(),
        )
        groups.append({
            "ip": ip,
            "account_count": len(accounts),
            "accounts": accounts,
            "usernames": [item["username"] for item in accounts],
        })

    groups.sort(key=lambda item: (-item["account_count"], item["ip"]))
    return groups


def get_invite_code_record(code_value):
    code_value = normalize_invite_code(code_value)
    if not code_value:
        return None
    result = execute_query(
        db.table("registration_invite_codes")
        .select("*")
        .eq("code", code_value)
        .limit(1),
        "get_invite_code_record",
    )
    return result.data[0] if result.data else None


def list_registration_invite_codes(limit=100):
    result = execute_query(
        db.table("registration_invite_codes")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit),
        "list_registration_invite_codes",
    )
    records = result.data or []
    users = {u["id"]: u for u in list_all_users()}
    for record in records:
        record["created_by_name"] = users.get(record.get("created_by"), {}).get("display_name", "-")
        record["used_by_name"] = users.get(record.get("used_by"), {}).get("display_name", "-")
    return records


def auto_confirm_expired_match_if_needed(match):
    """Tự xác nhận trận chờ quá 1 phút, độc lập với trạng thái phòng.

    Hủy/đóng phòng chỉ giải phóng người chơi. Kết quả đã nhập vẫn tiếp tục
    chờ xác nhận và được tính RP sau thời hạn nếu không có tranh chấp.
    """
    if not match or match.get("status") != "waiting_confirm":
        return match
    submitted_at = aware_utc(parse_dt(match.get("updated_at"))) or aware_utc(parse_dt(match.get("created_at")))
    if not submitted_at or submitted_at + timedelta(seconds=RESULT_CONFIRM_TIMEOUT_SECONDS) > now_dt():
        return match
    try:
        # Lưu trạng thái người chơi/phòng trước khi cộng RP để tạo đúng sự kiện
        # chuỗi thắng hoặc SHUTDOWN cho cả luồng tự xác nhận sau 1 phút.
        users_before_streak_event = users_map()
        room_before_result = None
        try:
            room_before_result_query = execute_query(
                db.table("match_rooms").select("*").eq("match_id", match.get("id")).limit(1),
                "load_room_before_auto_confirm_streak_event",
                attempts=2,
            )
            room_before_result = (room_before_result_query.data or [None])[0]
        except Exception as room_exc:
            print(
                f"load_room_before_auto_confirm_streak_event warning match={match.get('id')}: "
                f"{type(room_exc).__name__}: {room_exc}"
            )

        apply_match_result(dict(match))
        streak_event = build_win_streak_event(
            match, room_before_result, users_before_streak_event
        )
        if streak_event:
            publish_global_streak_event(streak_event)

        fresh_result = execute_query(
            db.table("matches").select("*").eq("id", match.get("id")).limit(1),
            "reload_auto_confirmed_match",
            attempts=2,
        )
        fresh = dict(fresh_result.data[0]) if fresh_result.data else dict(match)

        # Chỉ đưa phòng đang chờ kết quả về trạng thái sẵn sàng. Nếu Admin đã
        # hủy phòng, giữ phòng cancelled nhưng kết quả vẫn được tính bình thường.
        room_result = execute_query(
            db.table("match_rooms").select("id,status").eq("match_id", match.get("id")).limit(1),
            "load_room_for_auto_confirm",
            attempts=2,
        )
        linked_room = (room_result.data or [None])[0]
        if linked_room and linked_room.get("status") == "waiting_result_confirm":
            execute_query(
                db.table("match_rooms").update({
                    "status": "waiting_ready",
                    "guest_ready": False,
                    "host_score": None,
                    "guest_score": None,
                    "match_id": None,
                    "submitted_by_id": None,
                    "confirmed_by_id": None,
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                }).eq("id", linked_room.get("id")).eq("status", "waiting_result_confirm"),
                "release_room_after_auto_confirm",
                attempts=2,
            )
        cache_delete("_rz_matches_all", "_rz_rooms_all")
        ttl_cache_delete("matches_raw")
        ttl_cache_delete("rooms_raw")
        return fresh
    except Exception as exc:
        print(f"auto_confirm_expired_match warning match={match.get('id')}: {type(exc).__name__}: {exc}")
        return match


def list_matches(status=None):
    require_db()

    cached = cache_get("_rz_matches_all")
    if cached is None:
        query = db.table("matches").select("*").order("created_at", desc=True)
        result = execute_query(query, "list_matches")
        cached = result.data or []
        cache_set("_rz_matches_all", cached)

    processed_matches = [auto_confirm_expired_match_if_needed(dict(m)) for m in cached]
    matches = [m for m in processed_matches if not status or m.get("status") == status]
    users = users_map()

    for match in matches:
        player1 = users.get(match.get("player1_id"), {})
        player2 = users.get(match.get("player2_id"), {})
        match["player1_name"] = player1.get("display_name", "Unknown")
        match["player2_name"] = player2.get("display_name", "Unknown")
        match["player1_avatar_url"] = player1.get("avatar_url")
        match["player2_avatar_url"] = player2.get("avatar_url")
        match["player1_avatar_frame"] = player1.get("avatar_frame")
        match["player2_avatar_frame"] = player2.get("avatar_frame")
        match["player1_achievement"] = player1.get("featured_achievement")
        match["player2_achievement"] = player2.get("featured_achievement")
        match["submitted_by_name"] = users.get(match.get("submitted_by_id"), {}).get("display_name", "")
        match["winner_name"] = users.get(match.get("winner_id"), {}).get("display_name", "")
        match["loser_name"] = users.get(match.get("loser_id"), {}).get("display_name", "")

    return matches


def match_status_label(status):
    return MATCH_STATUS_LABELS.get(status, str(status or "-").replace("_", " ").title())


def _normalize_match_score(value):
    """Return an integer score while preserving a missing score as None."""
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def _same_user_id(left, right):
    """Compare Supabase/user IDs safely even when one side is not a string."""
    if left is None or right is None:
        return False
    return str(left) == str(right)


def _normalize_match_delta(value):
    """Normalize RP deltas returned as int, float or numeric string."""
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def decorate_match_for_view(match, viewer_id=None):
    """Prepare one match with a single, consistent left/right display order.

    Personal history and Profile always put the viewed player on the left.
    System-wide history keeps the original player1/player2 order. Winner/loser
    display data is derived from the confirmed score, so stale winner_id fields
    cannot make the UI show the wrong side.
    """
    item = dict(match or {})
    item["is_forfeit"] = is_forfeit_match(item)
    item["forfeit_loser_id"] = forfeit_loser_id(item) if item["is_forfeit"] else None
    if item["is_forfeit"]:
        item["note"] = forfeit_display_note(item)
    item["status_label"] = "Bỏ cuộc" if item["is_forfeit"] else match_status_label(item.get("status"))
    item["created_at_display"] = format_vn_datetime(item.get("created_at"))
    item["is_cancelled"] = item.get("status") == "cancelled"
    rp_details = item.get("rp_details") or {}
    repeat_details = rp_details.get("repeat_opponent") if isinstance(rp_details, dict) else {}
    repeat_details = repeat_details if isinstance(repeat_details, dict) else {}
    item["repeat_opponent_details"] = repeat_details
    item["is_no_rp_pair_match"] = repeat_details.get("counted_for_rp") is False
    item["repeat_encounter_number"] = repeat_details.get("encounter_number")

    score1 = _normalize_match_score(item.get("score1"))
    score2 = _normalize_match_score(item.get("score2"))
    item["score1_normalized"] = score1
    item["score2_normalized"] = score2

    player1_id = item.get("player1_id")
    player2_id = item.get("player2_id")
    viewer_is_player1 = _same_user_id(viewer_id, player1_id)
    viewer_is_player2 = _same_user_id(viewer_id, player2_id)
    item["is_mine"] = bool(viewer_is_player1 or viewer_is_player2)

    computed_winner_id = None
    computed_loser_id = None
    is_confirmed_result = (
        item.get("status") == "confirmed"
        and score1 is not None
        and score2 is not None
    )
    if is_confirmed_result:
        if score1 > score2:
            computed_winner_id, computed_loser_id = player1_id, player2_id
        elif score2 > score1:
            computed_winner_id, computed_loser_id = player2_id, player1_id

    # Display-only winner/loser fields use the score as source of truth.
    item["display_winner_id"] = computed_winner_id
    item["display_loser_id"] = computed_loser_id
    if computed_winner_id is not None:
        item["winner_name"] = (
            item.get("player1_name")
            if _same_user_id(computed_winner_id, player1_id)
            else item.get("player2_name")
        )
        item["loser_name"] = (
            item.get("player2_name")
            if _same_user_id(computed_loser_id, player2_id)
            else item.get("player1_name")
        )
    elif is_confirmed_result:
        item["winner_name"] = ""
        item["loser_name"] = ""

    # Personal views always put the relevant player on the left.
    left_is_player1 = not viewer_is_player2
    if left_is_player1:
        left_prefix, right_prefix = "player1", "player2"
        left_score, right_score = score1, score2
        left_delta, right_delta = item.get("delta1"), item.get("delta2")
        left_team, right_team = item.get("team1"), item.get("team2")
    else:
        left_prefix, right_prefix = "player2", "player1"
        left_score, right_score = score2, score1
        left_delta, right_delta = item.get("delta2"), item.get("delta1")
        left_team, right_team = item.get("team2"), item.get("team1")

    def side_value(prefix, suffix):
        return item.get(f"{prefix}_{suffix}")

    item["left_player_id"] = side_value(left_prefix, "id")
    item["left_player_name"] = side_value(left_prefix, "name")
    item["left_avatar_url"] = side_value(left_prefix, "avatar_url")
    item["left_avatar_frame"] = side_value(left_prefix, "avatar_frame")
    item["left_achievement"] = side_value(left_prefix, "achievement")
    item["left_team"] = left_team
    item["left_score"] = left_score
    item["left_delta"] = _normalize_match_delta(left_delta)

    item["right_player_id"] = side_value(right_prefix, "id")
    item["right_player_name"] = side_value(right_prefix, "name")
    item["right_avatar_url"] = side_value(right_prefix, "avatar_url")
    item["right_avatar_frame"] = side_value(right_prefix, "avatar_frame")
    item["right_achievement"] = side_value(right_prefix, "achievement")
    item["right_team"] = right_team
    item["right_score"] = right_score
    item["right_delta"] = _normalize_match_delta(right_delta)

    if item["is_forfeit"]:
        item["score_display"] = "Bỏ cuộc"
    elif item["is_cancelled"]:
        item["score_display"] = "Không tính"
    else:
        left_score_display = left_score if left_score is not None else "-"
        right_score_display = right_score if right_score is not None else "-"
        item["score_display"] = f"{left_score_display} - {right_score_display}"

    item["left_result_code"] = "neutral"
    item["left_result_label"] = item["status_label"]
    item["right_result_code"] = "neutral"
    item["right_result_label"] = item["status_label"]

    if item["is_forfeit"]:
        left_is_loser = _same_user_id(item.get("forfeit_loser_id"), item.get("left_player_id"))
        right_is_loser = _same_user_id(item.get("forfeit_loser_id"), item.get("right_player_id"))
        if left_is_loser:
            item["left_result_code"], item["left_result_label"] = "loss", "THUA BỎ CUỘC"
            item["right_result_code"], item["right_result_label"] = "neutral", "ĐỐI THỦ BỎ CUỘC"
        elif right_is_loser:
            item["left_result_code"], item["left_result_label"] = "neutral", "ĐỐI THỦ BỎ CUỘC"
            item["right_result_code"], item["right_result_label"] = "loss", "THUA BỎ CUỘC"
        else:
            item["left_result_code"] = item["right_result_code"] = "cancelled"
            item["left_result_label"] = item["right_result_label"] = "BỎ CUỘC"
    elif is_confirmed_result:
        if left_score > right_score:
            item["left_result_code"], item["left_result_label"] = "win", "THẮNG"
            item["right_result_code"], item["right_result_label"] = "loss", "THUA"
        elif left_score < right_score:
            item["left_result_code"], item["left_result_label"] = "loss", "THUA"
            item["right_result_code"], item["right_result_label"] = "win", "THẮNG"
        else:
            item["left_result_code"] = item["right_result_code"] = "draw"
            item["left_result_label"] = item["right_result_label"] = "HÒA"
    elif item.get("status") == "cancelled":
        item["left_result_code"] = item["right_result_code"] = "cancelled"
        item["left_result_label"] = item["right_result_label"] = "ĐÃ HỦY"
    elif item.get("status") == "disputed":
        item["left_result_code"] = item["right_result_code"] = "disputed"
        item["left_result_label"] = item["right_result_label"] = "TRANH CHẤP"
    else:
        item["left_result_code"] = item["right_result_code"] = "pending"

    item["result_code"] = "neutral"
    item["result_label"] = item["status_label"]
    item["my_delta"] = None
    item["opponent_id"] = None
    item["opponent_name"] = None
    item["my_avatar_url"] = None
    item["opponent_avatar_url"] = None
    item["my_avatar_frame"] = None
    item["opponent_avatar_frame"] = None
    item["my_achievement"] = None
    item["opponent_achievement"] = None
    item["my_team"] = None
    item["opponent_team"] = None

    if item["is_mine"]:
        # The viewed/current player is always the left side in personal views.
        item["result_code"] = item["left_result_code"]
        item["result_label"] = item["left_result_label"]
        item["my_delta"] = item["left_delta"]
        item["opponent_id"] = item["right_player_id"]
        item["opponent_name"] = item["right_player_name"]
        item["my_avatar_url"] = item["left_avatar_url"]
        item["opponent_avatar_url"] = item["right_avatar_url"]
        item["my_avatar_frame"] = item["left_avatar_frame"]
        item["opponent_avatar_frame"] = item["right_avatar_frame"]
        item["my_achievement"] = item["left_achievement"]
        item["opponent_achievement"] = item["right_achievement"]
        item["my_team"] = item["left_team"]
        item["opponent_team"] = item["right_team"]

    return item

def build_player_activity_map(rooms=None, matches=None):
    rooms = list_rooms() if rooms is None else rooms
    matches = list_matches() if matches is None else matches
    activity = {}

    def set_status(user_id, code, label):
        if not user_id:
            return
        current = activity.get(user_id)
        if not current or ACTIVITY_PRIORITY.get(code, 0) > ACTIVITY_PRIORITY.get(current.get("code"), 0):
            activity[user_id] = {"code": code, "label": label}

    for room in rooms:
        if not room_is_active(room):
            continue
        if room.get("status") == "waiting_ready":
            code, label = "in_room", "Đang trong phòng"
        elif room.get("status") == "waiting_result_confirm":
            code, label = "waiting_confirm", "Chờ xác nhận"
        else:
            code, label = "playing", "Đang thi đấu"
        set_status(room.get("host_user_id"), code, label)
        set_status(room.get("guest_user_id"), code, label)

    for match in matches:
        if match.get("status") == "waiting_confirm":
            code, label = "waiting_confirm", "Chờ xác nhận"
        elif match.get("status") == "playing":
            code, label = "playing", "Đang thi đấu"
        else:
            continue
        set_status(match.get("player1_id"), code, label)
        set_status(match.get("player2_id"), code, label)

    return activity


def get_match(match_id):
    result = execute_query(
        db.table("matches").select("*").eq("id", match_id).limit(1),
        "get_match",
    )
    match = dict(result.data[0]) if result.data else None
    return auto_confirm_expired_match_if_needed(match) if match else None


def get_match_dispute(dispute_id):
    result = execute_query(
        db.table("match_disputes").select("*").eq("id", dispute_id).limit(1),
        "get_match_dispute",
    )
    return dict(result.data[0]) if result.data else None


def get_match_dispute_by_match(match_id, statuses=None):
    query = db.table("match_disputes").select("*").eq("match_id", match_id).order("created_at", desc=True).limit(1)
    if statuses:
        status_list = list(statuses) if not isinstance(statuses, str) else [statuses]
        query = query.in_("status", status_list)
    result = execute_query(query, "get_match_dispute_by_match")
    return dict(result.data[0]) if result.data else None


def list_match_disputes(status=None):
    query = db.table("match_disputes").select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    result = execute_query(query, "list_match_disputes")
    return [dict(item) for item in (result.data or [])]


# Dịch vụ thông báo cá nhân đã tách sang modules/notification_service.py.

def dispute_reason_label(reason_code):
    return DISPUTE_REASON_OPTIONS.get(reason_code, DISPUTE_REASON_OPTIONS["other"])


def create_or_update_match_dispute(
    room,
    raised_by_id,
    reason_code,
    details="",
    source="player",
    evidence_path=None,
):
    if not room or not room.get("match_id"):
        return None

    reason_code = reason_code if reason_code in DISPUTE_REASON_OPTIONS else "other"
    details = (details or "").strip()[:500]
    existing = get_match_dispute_by_match(room.get("match_id"), DISPUTE_PENDING_STATUSES)
    payload = {
        "room_id": room.get("id"),
        "raised_by_id": raised_by_id,
        "reason_code": reason_code,
        "reason_label": dispute_reason_label(reason_code),
        "details": details or None,
        "source": source,
        "submitted_score1": room.get("host_score"),
        "submitted_score2": room.get("guest_score"),
        "status": "pending",
        "updated_at": now_iso(),
    }
    if evidence_path:
        payload.update({
            "evidence_path": evidence_path,
            "evidence_uploaded_at": now_iso(),
        })

    if existing:
        result = execute_query(
            db.table("match_disputes").update(payload).eq("id", existing.get("id")),
            "update_match_dispute",
        )
    else:
        payload["match_id"] = room.get("match_id")
        result = execute_query(
            db.table("match_disputes").insert(payload),
            "create_match_dispute",
        )
    return dict(result.data[0]) if result.data else existing


def decorate_match_dispute(dispute, all_matches=None):
    item = dict(dispute or {})
    match = None
    if item.get("match_id") and all_matches is not None:
        match = next((m for m in all_matches if str(m.get("id")) == str(item.get("match_id"))), None)
    if item.get("match_id") and match is None:
        match = get_match(item.get("match_id"))
    users = users_map()
    player1 = users.get((match or {}).get("player1_id"), {})
    player2 = users.get((match or {}).get("player2_id"), {})
    raised_by = users.get(item.get("raised_by_id"), {})
    resolved_by = users.get(item.get("resolved_by_id"), {})

    item["match"] = match or {}
    item["player1_name"] = player1.get("display_name", "Unknown")
    item["player2_name"] = player2.get("display_name", "Unknown")
    item["player1_username"] = player1.get("username", "-")
    item["player2_username"] = player2.get("username", "-")
    item["player1_points"] = int(player1.get("rank_points", 0) or 0)
    item["player2_points"] = int(player2.get("rank_points", 0) or 0)
    item["raised_by_name"] = raised_by.get("display_name") or ("Hệ thống" if item.get("source") == "timeout" else "Không xác định")
    item["resolved_by_name"] = resolved_by.get("display_name", "")
    item["reason_label"] = item.get("reason_label") or dispute_reason_label(item.get("reason_code"))
    item["evidence_url"] = get_dispute_evidence_signed_url(item.get("evidence_path"))
    if item.get("submitted_score1") is None:
        item["submitted_score1"] = (match or {}).get("score1")
    if item.get("submitted_score2") is None:
        item["submitted_score2"] = (match or {}).get("score2")

    candidates = all_matches if all_matches is not None else list_matches()
    pair = {(match or {}).get("player1_id"), (match or {}).get("player2_id")}
    item["head_to_head"] = [
        other for other in candidates
        if other.get("status") == "confirmed"
        and str(other.get("id")) != str(item.get("match_id"))
        and {other.get("player1_id"), other.get("player2_id")} == pair
    ][:5]
    return item



def invite_expiry_dt(invite):
    explicit = aware_utc(parse_dt(invite.get("expires_at")))
    if explicit:
        return explicit
    created = aware_utc(parse_dt(invite.get("created_at")))
    return created + timedelta(seconds=INVITE_TIMEOUT_SECONDS) if created else None


def expire_invite_if_needed(invite):
    if not invite or invite.get("status") != "pending":
        return invite

    expires_at = invite_expiry_dt(invite)
    invite["expires_at"] = expires_at.isoformat() if expires_at else None
    invite["expires_in_seconds"] = max(0, int((expires_at - now_dt()).total_seconds())) if expires_at else 0

    if expires_at and expires_at <= now_dt():
        try:
            execute_query(
                db.table("match_invites").update({
                    "status": "expired",
                    "updated_at": now_iso(),
                }).eq("id", invite.get("id")).eq("status", "pending"),
                "expire_match_invite",
            )
            invite["status"] = "expired"
            invite["expires_in_seconds"] = 0
        except Exception as exc:
            print(f"expire_invite_if_needed warning: {exc}")
    return invite


def get_invite(invite_id):
    result = execute_query(
        db.table("match_invites").select("*").eq("id", invite_id).limit(1),
        "get_invite",
    )
    invite = dict(result.data[0]) if result.data else None
    return expire_invite_if_needed(invite) if invite else None


def list_invites(status=None):
    cached = cache_get("_rz_invites_all")
    if cached is None:
        shared = ttl_cache_get("invites_raw")
        if shared is None:
            query = db.table("match_invites").select("*").order("created_at", desc=True)
            result = execute_query(query, "list_invites")
            shared = result.data or []
            ttl_cache_set("invites_raw", shared, 3)
        cached = [dict(row) for row in shared]
        cache_set("_rz_invites_all", cached)

    processed = []
    for raw in cached:
        invite = expire_invite_if_needed(dict(raw))
        if status and invite.get("status") != status:
            continue
        processed.append(invite)

    users = users_map()
    for invite in processed:
        from_user = users.get(invite.get("from_user_id"), {})
        to_user = users.get(invite.get("to_user_id"), {})
        invite["from_name"] = from_user.get("display_name", "Unknown")
        invite["from_avatar_url"] = from_user.get("avatar_url")
        invite["from_avatar_frame"] = from_user.get("avatar_frame")
        invite["from_achievement"] = from_user.get("featured_achievement")
        invite["from_points"] = from_user.get("rank_points", 0)
        invite["from_rank"] = get_rank_display(from_user.get("rank_points", 0))
        invite["to_name"] = to_user.get("display_name", "Unknown")
        invite["to_avatar_url"] = to_user.get("avatar_url")
        invite["to_avatar_frame"] = to_user.get("avatar_frame")
        invite["to_achievement"] = to_user.get("featured_achievement")
        invite["to_points"] = to_user.get("rank_points", 0)
        invite["to_rank"] = get_rank_display(to_user.get("rank_points", 0))

    return processed


def room_state_expiry_dt(room):
    """Short state-specific deadline such as ready/result/rematch timeout."""
    explicit = aware_utc(parse_dt(room.get("state_expires_at")))
    if explicit:
        return explicit

    updated = aware_utc(parse_dt(room.get("updated_at"))) or aware_utc(parse_dt(room.get("created_at")))
    if not updated:
        return None

    status = room.get("status")
    note = room.get("note") or ""
    if status == "waiting_ready":
        # Phòng chưa bắt đầu được xử lý bằng bộ đếm không hoạt động 30 phút.
        return None
    if status == "waiting_result_confirm":
        return updated + timedelta(seconds=RESULT_CONFIRM_TIMEOUT_SECONDS)
    if status == "confirmed" and note in {REMATCH_HOST_READY_NOTE, REMATCH_GUEST_READY_NOTE}:
        return updated + timedelta(seconds=REMATCH_TIMEOUT_SECONDS)
    return None


def room_inactivity_expiry_dt(room):
    """Đóng phòng chờ sau 30 phút, phòng đã bắt đầu sau 60 phút không hoạt động."""
    active_statuses = {"waiting_ready", "playing", "friendly_playing", "waiting_result_confirm"}
    status = room.get("status")
    note = room.get("note") or ""
    if status == "confirmed" and note in {REMATCH_HOST_READY_NOTE, REMATCH_GUEST_READY_NOTE}:
        active = True
    else:
        active = status in active_statuses
    if not active:
        return None

    last_activity = aware_utc(parse_dt(room.get("updated_at"))) or aware_utc(parse_dt(room.get("created_at")))
    if not last_activity:
        return None
    timeout_seconds = (
        ROOM_EMPTY_INACTIVITY_TIMEOUT_SECONDS
        if status == "waiting_ready"
        else ROOM_MATCH_INACTIVITY_TIMEOUT_SECONDS
    )
    return last_activity + timedelta(seconds=timeout_seconds)


def room_expiry_dt(room):
    state_expiry = room_state_expiry_dt(room)
    inactivity_expiry = room_inactivity_expiry_dt(room)
    candidates = [dt for dt in (state_expiry, inactivity_expiry) if dt]
    return min(candidates) if candidates else None


def apply_room_abandon_penalty(user_id, amount=ROOM_ABANDON_PENALTY):
    """Trừ RP và tính một trận thua do bỏ trận, không cộng thắng cho đối thủ."""
    if not user_id:
        return None
    player = get_user(user_id)
    if not player:
        return None
    penalty = max(0, int(amount or 0))
    old_points = int(player.get("rank_points", 0) or 0)
    new_points = max(0, old_points - penalty)
    execute_query(
        db.table("users").update({
            "rank_points": new_points,
            "losses": int(player.get("losses", 0) or 0) + 1,
            "total_matches": int(player.get("wins", 0) or 0) + int(player.get("draws", 0) or 0) + int(player.get("losses", 0) or 0) + 1,
            "streak": 0,
        }).eq("id", user_id),
        "apply_room_abandon_penalty",
    )
    cache_delete("_rz_users_map")
    cache_delete("_rz_players_all")
    return -(old_points - new_points)


HOST_BROWSER_OFFLINE_GRACE_SECONDS = 20
HOST_BROWSER_OFFLINE_ROOM_STATUSES = {"playing", "friendly_playing"}


def close_room_if_host_browser_offline(room):
    """Đóng phòng khi chủ đã đóng tab/trình duyệt và presence chuyển Offline.

    Chỉ áp dụng sau khi trận đã bắt đầu. Khách không được cộng/trừ RP, không
    thay đổi thắng/hòa/thua hoặc chuỗi. Điều kiện update theo status giúp chống
    xử lý lặp trên nhiều instance Vercel.
    """
    if not room or room.get("status") not in HOST_BROWSER_OFFLINE_ROOM_STATUSES:
        return False

    host_id = room.get("host_user_id")
    guest_id = room.get("guest_user_id")
    if not host_id or not guest_id:
        return False

    try:
        host = get_user(host_id)
    except Exception as exc:
        print(f"Host offline check warning: {exc}")
        return False
    if not host or host.get("is_online") is not False:
        return False

    last_seen = aware_utc(parse_dt(host.get("last_seen_at")))
    if not last_seen:
        return False
    if now_dt() < last_seen + timedelta(seconds=HOST_BROWSER_OFFLINE_GRACE_SECONDS):
        return False

    original_status = room.get("status")
    reason = (
        f"{host.get('display_name') or host.get('username') or 'Chủ phòng'} "
        f"đã đóng trình duyệt khi trận đang diễn ra và bị trừ {ROOM_ABANDON_PENALTY} RP."
    )
    update_data = {
        "status": "cancelled",
        "guest_ready": False,
        "note": reason,
        "state_expires_at": None,
        "updated_at": now_iso(),
    }
    result = execute_query(
        db.table("match_rooms").update(update_data)
        .eq("id", room.get("id"))
        .eq("status", original_status),
        "host_browser_offline_close_room",
    )
    if not (result.data or []):
        return False

    room.update(update_data)
    penalty_delta = apply_room_abandon_penalty(host_id, ROOM_ABANDON_PENALTY)
    record_room_forfeit_match(
        room,
        offender_role="host",
        penalty_delta=penalty_delta if penalty_delta is not None else -ROOM_ABANDON_PENALTY,
        reason=reason,
        event_type="host_browser_offline_forfeit",
    )

    create_user_notification(
        host_id,
        "⚠️ Bạn đã thoát trận",
        f"Phòng đã đóng vì trình duyệt của bạn Offline. Bạn bị trừ {ROOM_ABANDON_PENALTY} RP và mất chuỗi thắng.",
        "/matches",
        "host_browser_offline_penalty",
    )
    create_user_notification(
        guest_id,
        "🚪 Chủ phòng đã Offline",
        "Phòng đã tự đóng. Bạn không bị cộng hoặc trừ RP và có thể tạo phòng mới ngay.",
        "/rooms",
        "host_browser_offline_room_closed",
    )
    cache_delete("_rz_rooms_all")
    ttl_cache_delete("rooms_raw")
    return True


def close_room_with_timeout_penalty(room, offender_role, reason):
    """Đóng phòng và phạt ngẫu nhiên 22–25 RP đúng một lần."""
    room_id = room.get("id")
    original_status = room.get("status")
    offender_id = room.get("host_user_id") if offender_role == "host" else room.get("guest_user_id")
    update_data = {
        "status": "cancelled",
        "note": reason,
        "state_expires_at": None,
        "updated_at": now_iso(),
    }
    result = execute_query(
        db.table("match_rooms").update(update_data).eq("id", room_id).eq("status", original_status),
        "close_room_timeout_penalty",
    )
    # Nếu request khác đã đóng phòng trước, không trừ điểm lần thứ hai.
    if not (result.data or []):
        return False

    room.update(update_data)
    penalty_amount = random.SystemRandom().randint(*ROOM_TIMEOUT_PENALTY_RANGE)
    penalty_delta = apply_room_abandon_penalty(offender_id, penalty_amount)
    record_room_forfeit_match(
        room,
        offender_role=offender_role,
        penalty_delta=penalty_delta if penalty_delta is not None else -penalty_amount,
        reason=reason,
        event_type="timeout_forfeit",
    )

    offender_name = room.get("host_name") if offender_role == "host" else room.get("guest_name")
    other_id = room.get("guest_user_id") if offender_role == "host" else room.get("host_user_id")
    create_user_notification(
        offender_id,
        "⏱️ Trận bị tính là bỏ trận",
        f"Bạn bị trừ {abs(int(penalty_delta or -penalty_amount))} RP vì {reason.lower()}",
        "/matches",
        "room_timeout_penalty",
    )
    create_user_notification(
        other_id,
        "⏱️ Phòng đấu đã tự đóng",
        f"{offender_name or 'Đối thủ'} bị tính là bỏ trận. Bạn không bị cộng hoặc trừ RP.",
        "/matches",
        "room_timeout",
    )
    return True


def expire_room_if_needed(room):
    if not room:
        return room

    expires_at = room_expiry_dt(room)
    room["state_expires_at"] = expires_at.isoformat() if expires_at else None
    room["timeout_seconds"] = max(0, int((expires_at - now_dt()).total_seconds())) if expires_at else 0
    if not expires_at or expires_at > now_dt():
        return room

    status = room.get("status")
    note = room.get("note") or ""
    mode = room.get("match_mode") or MATCH_MODE_RANKED
    state_expiry = room_state_expiry_dt(room)
    inactivity_expiry = room_inactivity_expiry_dt(room)
    inactivity_expired = bool(
        inactivity_expiry
        and inactivity_expiry <= now_dt()
        and (not state_expiry or inactivity_expiry <= state_expiry)
    )

    try:
        # Trận Xếp hạng đã quay đội nhưng chủ không nhập kết quả trong 60 phút.
        if status == "playing" and mode == MATCH_MODE_RANKED and inactivity_expired:
            close_room_with_timeout_penalty(
                room,
                "host",
                "Chủ phòng không nhập kết quả sau 60 phút và bị tính là thoát trận.",
            )
            room["timeout_seconds"] = 0
            return room

        # Đã nhập tỷ số nhưng chưa xác nhận: sau 1 phút tự xác nhận kết quả.
        # Không phạt người quên xác nhận và không phụ thuộc phòng còn hoạt động hay đã hủy.
        if status == "waiting_result_confirm" and mode == MATCH_MODE_RANKED:
            pending_match = get_match(room.get("match_id")) if room.get("match_id") else None
            if pending_match and pending_match.get("status") == "confirmed":
                room.update({
                    "status": "waiting_ready",
                    "guest_ready": False,
                    "host_score": None,
                    "guest_score": None,
                    "match_id": None,
                    "submitted_by_id": None,
                    "confirmed_by_id": None,
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                })
            room["timeout_seconds"] = 0
            return room

        # Giao hữu hoặc phòng chưa bắt đầu: chỉ đóng, không trừ điểm.
        if inactivity_expired or status == "waiting_ready":
            update_data = {
                "status": "cancelled",
                "note": (
                    "Phòng tự đóng sau 30 phút không hoạt động."
                    if status == "waiting_ready"
                    else "Phòng tự đóng sau 60 phút không hoạt động."
                ),
                "state_expires_at": None,
                "updated_at": now_iso(),
            }
            result = execute_query(
                db.table("match_rooms").update(update_data).eq("id", room.get("id")).eq("status", status),
                "expire_room_inactivity",
            )
            if result.data or []:
                room.update(update_data)
                if room.get("match_id"):
                    execute_query(
                        db.table("matches").update({
                            "status": "cancelled",
                            "note": (
                                "Phòng tự đóng sau 30 phút không hoạt động; không áp dụng phạt RP."
                                if status == "waiting_ready"
                                else "Phòng tự đóng sau 60 phút không hoạt động; không áp dụng phạt RP."
                            ),
                            "updated_at": now_iso(),
                        }).eq("id", room.get("match_id")),
                        "cancel_inactive_room_match",
                    )
            room["timeout_seconds"] = 0
            return room

        if status == "confirmed" and note in {REMATCH_HOST_READY_NOTE, REMATCH_GUEST_READY_NOTE}:
            update_data = {
                "note": REMATCH_EXPIRED_NOTE,
                "state_expires_at": None,
                "updated_at": now_iso(),
            }
            execute_query(
                db.table("match_rooms").update(update_data).eq("id", room.get("id")),
                "expire_rematch_request",
            )
            room.update(update_data)
            room["timeout_seconds"] = 0
    except Exception as exc:
        print(f"expire_room_if_needed warning: {exc}")

    return room


def get_room(room_id):
    result = execute_query(
        db.table("match_rooms").select("*").eq("id", room_id).limit(1),
        "get_room",
    )
    room = dict(result.data[0]) if result.data else None
    if room:
        expire_room_if_needed(room)
        enrich_room(room)
    return room


def enrich_room(room):
    users = users_map()
    host = users.get(room.get("host_user_id"), {})
    guest = users.get(room.get("guest_user_id"), {})

    raw_room_id = str(room.get("id") or "")
    compact_room_id = "".join(ch for ch in raw_room_id.upper() if ch.isalnum())
    room["room_code"] = (compact_room_id[:6] or "ROOM00")

    room["host_name"] = host.get("display_name", "Unknown")
    room["host_name_style_class"] = str(host.get("name_style_class") or "").strip()
    room["host_profile_badge"] = host.get("profile_badge")
    room["host_avatar_url"] = host.get("avatar_url")
    room["host_avatar_frame"] = host.get("avatar_frame")
    room["host_achievement"] = host.get("featured_achievement")
    room["host_points"] = host.get("rank_points", 0)
    room["host_rank_info"] = get_rank_info(host.get("rank_points", 0))
    room["host_rank"] = get_rank_display(host.get("rank_points", 0))
    room["host_streak"] = int(host.get("streak", 0) or 0)
    room["host_streak_badge"] = get_win_streak_badge(room["host_streak"])
    room["has_guest"] = bool(room.get("guest_user_id"))
    room["guest_name"] = guest.get("display_name", "Đang chờ đối thủ") if room["has_guest"] else "Đang chờ đối thủ"
    room["guest_name_style_class"] = str(guest.get("name_style_class") or "").strip() if room["has_guest"] else ""
    room["guest_profile_badge"] = guest.get("profile_badge") if room["has_guest"] else None
    room["guest_avatar_url"] = guest.get("avatar_url") if room["has_guest"] else None
    room["guest_avatar_frame"] = guest.get("avatar_frame") if room["has_guest"] else None
    room["guest_achievement"] = guest.get("featured_achievement") if room["has_guest"] else None
    room["guest_points"] = guest.get("rank_points", 0) if room["has_guest"] else 0
    room["guest_rank_info"] = get_rank_info(guest.get("rank_points", 0)) if room["has_guest"] else None
    room["guest_rank"] = get_rank_display(guest.get("rank_points", 0)) if room["has_guest"] else "Chưa có người chơi"
    room["guest_streak"] = int(guest.get("streak", 0) or 0) if room["has_guest"] else 0
    room["guest_streak_badge"] = get_win_streak_badge(room["guest_streak"]) if room["has_guest"] else None
    room["streak_event"] = parse_win_streak_room_note(room.get("note"))
    if room.get("host_team"):
        info = get_db_team_info(room.get("host_team")) or {}
        room["host_team_overall"] = room.get("host_team_overall") or info.get("overall") or get_team_overall(room.get("host_team"))
        room["host_team_logo_url"] = room.get("host_team_logo_url") or info.get("logo_url")
        room["host_team_league"] = room.get("host_team_league") or info.get("league") or ""
        room["host_team_league_logo_url"] = get_league_logo_url(room["host_team_league"])
        room["host_team_tier"] = info.get("tier") or get_team_tier(room.get("host_team"))
        room["host_team_total_stats"] = int(info.get("total_stats") or 0)
    else:
        room["host_team_total_stats"] = 0
    if room.get("guest_team"):
        info = get_db_team_info(room.get("guest_team")) or {}
        room["guest_team_overall"] = room.get("guest_team_overall") or info.get("overall") or get_team_overall(room.get("guest_team"))
        room["guest_team_logo_url"] = room.get("guest_team_logo_url") or info.get("logo_url")
        room["guest_team_league"] = room.get("guest_team_league") or info.get("league") or ""
        room["guest_team_league_logo_url"] = get_league_logo_url(room["guest_team_league"])
        room["guest_team_tier"] = info.get("tier") or get_team_tier(room.get("guest_team"))
        room["guest_team_total_stats"] = int(info.get("total_stats") or 0)
    else:
        room["guest_team_total_stats"] = 0
    room["smart_random_rule"] = get_smart_random_rule(host, guest)
    room["rematch_host_ready"] = room.get("note") == REMATCH_HOST_READY_NOTE
    room["rematch_guest_ready"] = room.get("note") == REMATCH_GUEST_READY_NOTE
    room["rematch_host_declined"] = room.get("note") == REMATCH_HOST_DECLINED_NOTE
    room["rematch_guest_declined"] = room.get("note") == REMATCH_GUEST_DECLINED_NOTE
    room["rematch_declined"] = room["rematch_host_declined"] or room["rematch_guest_declined"]
    room["match_mode"] = room.get("match_mode") or MATCH_MODE_RANKED
    if (not system_feature_enabled("rank_standard_enabled") and room.get("status") == "waiting_ready"
            and room.get("match_mode") == MATCH_MODE_RANKED and not decode_friendly_random3_state(room.get("note"))):
        room["team_tier"] = FRIENDLY_RANDOM3_MODE
    room["friendly_tier"] = room.get("friendly_tier") or "A"
    random3_state = decode_friendly_random3_state(room.get("note"))
    room["friendly_random3"] = random3_state
    room["friendly_random3_active"] = bool(random3_state)
    room["friendly_random3_host_chosen"] = bool(random3_state and random3_state.get("host_choice") is not None)
    room["friendly_random3_guest_chosen"] = bool(random3_state and random3_state.get("guest_choice") is not None)
    room["host_team_league"] = room.get("host_team_league") or ""
    room["guest_team_league"] = room.get("guest_team_league") or ""
    room["host_team_league_logo_url"] = room.get("host_team_league_logo_url") or get_league_logo_url(room["host_team_league"])
    room["guest_team_league_logo_url"] = room.get("guest_team_league_logo_url") or get_league_logo_url(room["guest_team_league"])
    room["rematch_expired"] = room.get("note") == REMATCH_EXPIRED_NOTE
    room["dispute"] = None
    if room.get("status") == "disputed" and room.get("match_id"):
        try:
            dispute = get_match_dispute_by_match(room.get("match_id"), DISPUTE_PENDING_STATUSES)
            if dispute:
                room["dispute"] = decorate_match_dispute(dispute)
        except Exception as exc:
            print(f"enrich_room dispute warning: {exc}")
    room["timeout_seconds"] = seconds_until(room.get("state_expires_at"))
    state_expiry = room_state_expiry_dt(room)
    inactivity_expiry = room_inactivity_expiry_dt(room)
    inactivity_is_next = bool(
        inactivity_expiry
        and (not state_expiry or inactivity_expiry <= state_expiry)
    )
    if inactivity_is_next and room.get("timeout_seconds", 0) > 0:
        room["timeout_label"] = "Phòng sẽ tự đóng nếu không có hoạt động trong"
    elif room.get("status") == "waiting_ready" and room.get("guest_user_id") and not room.get("guest_ready"):
        room["timeout_label"] = "Phòng sẽ tự đóng nếu không có hoạt động trong"
    elif room.get("status") == "waiting_result_confirm":
        room["timeout_label"] = "Khách cần xác nhận hoặc tranh chấp trong"
    elif room.get("status") == "confirmed" and (room.get("rematch_host_ready") or room.get("rematch_guest_ready")):
        room["timeout_label"] = "Yêu cầu đá tiếp sẽ hết hạn trong"
    else:
        room["timeout_label"] = ""

    room["match_mode_label"] = ("Random 3 chọn 1" if room.get("team_tier") == FRIENDLY_RANDOM3_MODE else ("Xếp hạng (Rank)" if room.get("match_mode") != MATCH_MODE_FRIENDLY else f"Giao hữu Tier {room.get('friendly_tier') or ''}".strip()))
    room["battle_label"] = "Trận đấu xếp hạng" if room.get("match_mode") != MATCH_MODE_FRIENDLY else "Trận đấu giao hữu"
    room["start_countdown_seconds"] = 0
    room["match_elapsed_seconds"] = 0
    if room.get("guest_user_id"):
        time_source = room.get("updated_at") or room.get("created_at")
        event_dt = parse_dt(time_source) if time_source else None
        if event_dt:
            elapsed = max(0, int((now_dt() - event_dt).total_seconds()))
            if room.get("status") == "waiting_ready":
                room["start_countdown_seconds"] = max(0, 300 - elapsed)
            elif room.get("status") in {"playing", "friendly_playing", "waiting_result_confirm"}:
                room["match_elapsed_seconds"] = elapsed
    room["guest_ready_label"] = "Đã sẵn sàng" if room.get("guest_ready") else "Chưa sẵn sàng"
    return room


def list_rooms(status=None):
    cached = cache_get("_rz_rooms_all")
    if cached is None:
        shared = ttl_cache_get("rooms_raw")
        if shared is None:
            query = db.table("match_rooms").select("*").order("created_at", desc=True)
            result = execute_query(query, "list_rooms")
            shared = result.data or []
            ttl_cache_set("rooms_raw", shared, 3)
        cached = [dict(row) for row in shared]
        cache_set("_rz_rooms_all", cached)

    rooms = []
    for raw in cached:
        room = expire_room_if_needed(dict(raw))
        if status and room.get("status") != status:
            continue
        enrich_room(room)
        rooms.append(room)
    return rooms



GLOBAL_STREAK_EVENT_SETTING_KEY = "global_win_streak_event"
GLOBAL_STREAK_EVENT_TTL_SECONDS = 24 * 60 * 60
GLOBAL_STREAK_EVENT_MAX_ITEMS = 30


def _normalize_global_streak_events(raw):
    """Chuẩn hóa dữ liệu cũ (1 dict) và dữ liệu mới (danh sách sự kiện)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    now = now_dt()
    active = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("id") or "").strip()
        if not event_id or event_id in seen:
            continue
        expires_at = aware_utc(parse_dt(item.get("expires_at")))
        if not expires_at or expires_at <= now:
            continue
        seen.add(event_id)
        active.append(dict(item))

    # SHUTDOWN ưu tiên trước; trong cùng loại, sự kiện mới hơn đứng trước.
    active.sort(
        key=lambda item: (
            0 if str(item.get("kind")) == "shutdown" else 1,
            str(item.get("published_at") or ""),
        )
    )
    shutdowns = [item for item in active if str(item.get("kind")) == "shutdown"]
    milestones = [item for item in active if str(item.get("kind")) != "shutdown"]
    shutdowns.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    milestones.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return (shutdowns + milestones)[:GLOBAL_STREAK_EVENT_MAX_ITEMS]


def publish_global_streak_event(event):
    if not isinstance(event, dict) or event.get("kind") not in {"milestone", "shutdown"}:
        return False
    payload = dict(event)
    payload["published_at"] = now_iso()
    payload["expires_at"] = future_iso(GLOBAL_STREAK_EVENT_TTL_SECONDS)
    payload["source"] = "win_streak"
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", GLOBAL_STREAK_EVENT_SETTING_KEY).limit(1),
            "read_global_streak_events", attempts=2,
        )
        raw = ((result.data or [{}])[0]).get("setting_value")
        events = _normalize_global_streak_events(raw)
        events = [item for item in events if str(item.get("id")) != str(payload.get("id"))]
        events.append(payload)
        events = _normalize_global_streak_events(events)
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": GLOBAL_STREAK_EVENT_SETTING_KEY,
                "setting_value": json.dumps(events, ensure_ascii=False),
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "publish_global_streak_event", attempts=2,
        )
        ttl_cache_delete("global_win_streak_events")
        ttl_cache_delete("global_win_streak_event")
        return True
    except Exception as exc:
        print(f"publish_global_streak_event warning: {exc}")
        return False


def get_active_global_streak_events():
    cached = ttl_cache_get("global_win_streak_events")
    if cached is not None:
        return [] if cached is False else cached
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", GLOBAL_STREAK_EVENT_SETTING_KEY).limit(1),
            "get_active_global_streak_events", attempts=2,
        )
        raw = ((result.data or [{}])[0]).get("setting_value")
        events = _normalize_global_streak_events(raw)
        ttl_cache_set("global_win_streak_events", events if events else False, 15)
        return events
    except Exception as exc:
        print(f"get_active_global_streak_events warning: {exc}")
        return []


def get_active_global_streak_event():
    """Tương thích với code cũ: trả sự kiện ưu tiên đầu tiên."""
    events = get_active_global_streak_events()
    return events[0] if events else None


def get_active_announcement():
    try:
        cached = cache_get("_rz_active_announcement")
        if cached is not None:
            return cached

        shared = ttl_cache_get("active_announcement")
        if shared is not None:
            return cache_set("_rz_active_announcement", None if shared is False else shared)
        result = execute_query(
            db.table("admin_announcements")
            .select("*")
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1),
            "get_active_announcement",
        )
        announcement = result.data[0] if result.data else None
        ttl_cache_set("active_announcement", announcement if announcement is not None else False, 15)
        return cache_set("_rz_active_announcement", announcement)
    except Exception:
        return None


def enrich_chat_message(message, users=None):
    if users is None:
        users = users_map()
    user = users.get(message.get("user_id"), {})
    message["user_name"] = user.get("display_name", "Unknown")
    message["user_avatar_url"] = user.get("avatar_url")
    message["user_avatar_frame"] = user.get("avatar_frame")
    message["user_avatar_frame_url"] = (user.get("avatar_frame") or {}).get("image_url") if isinstance(user.get("avatar_frame"), dict) else None
    message["user_achievement"] = user.get("featured_achievement")
    message["user_role"] = "admin" if is_admin_user(user) else user.get("role", "player")
    # Giữ timestamp gốc cho logic chưa đọc, đồng thời gửi chuỗi giờ Việt Nam dễ đọc.
    message["created_at_display"] = format_vn_datetime(message.get("created_at"))
    return message


def list_chat_messages(scope="global", room_id=None, limit=20):
    query = db.table("chat_messages").select("*").eq("scope", scope)

    if room_id:
        query = query.eq("room_id", room_id)
    else:
        query = query.is_("room_id", "null")

    result = execute_query(query.order("created_at", desc=True).limit(limit), "list_chat_messages")
    messages = list(reversed(result.data or []))
    users = users_map()
    return [enrich_chat_message(message, users) for message in messages]



def user_can_chat(user_id, scope="global", room_id=None):
    query = db.table("chat_messages").select("*").eq("user_id", user_id).eq("scope", scope)

    if room_id:
        query = query.eq("room_id", room_id)
    else:
        query = query.is_("room_id", "null")

    result = execute_query(query.order("created_at", desc=True).limit(1), "user_can_chat")
    if not result.data:
        return True, ""

    last_time = parse_dt(result.data[0].get("created_at"))
    if not last_time:
        return True, ""

    diff = (now_dt() - last_time).total_seconds()
    if diff < CHAT_COOLDOWN_SECONDS:
        wait = max(1, int(CHAT_COOLDOWN_SECONDS - diff))
        return False, f"Bạn gửi quá nhanh. Chờ {wait} giây."

    return True, ""


def touch_room_activity(room_id):
    """Reset the 60-minute inactivity timer after a meaningful room action."""
    if not room_id:
        return
    try:
        execute_query(
            db.table("match_rooms").update({"updated_at": now_iso()}).eq("id", room_id),
            "touch_room_activity",
            attempts=1,
        )
        cache_delete("_rz_rooms_all")
    except Exception as exc:
        print(f"touch_room_activity warning: {exc}")


def create_chat_message(user_id, message, scope="global", room_id=None):
    message = (message or "").strip()

    if not message:
        return False, "Tin nhắn không được để trống."

    if len(message) > CHAT_MAX_LENGTH:
        return False, f"Tin nhắn tối đa {CHAT_MAX_LENGTH} ký tự."

    ok, error = user_can_chat(user_id, scope, room_id)
    if not ok:
        return False, error

    db.table("chat_messages").insert({
        "user_id": user_id,
        "room_id": room_id,
        "scope": scope,
        "message": message,
    }).execute()

    if scope == "room" and room_id:
        touch_room_activity(room_id)

    return True, ""



def current_user():
    cached = cache_get("_rz_current_user")
    if cached is not None:
        return cached

    user_id = session.get("user_id")
    if not user_id:
        return None

    try:
        shared_user = ttl_cache_get(f"user:{user_id}")
        user = dict(shared_user) if shared_user is not None else get_user(user_id)
        if user:
            decorate_player_achievements(user)
            ttl_cache_set(f"user:{user_id}", dict(user), 8)
            session["username"] = user.get("username", "")
            session["display_name"] = user.get("display_name", "")
            session["avatar_url"] = user.get("avatar_url")
            session["role"] = user.get("role", "player")
            session["account_status"] = user.get("account_status", "approved")
            session["admin_level"] = user.get("admin_level", "none")
            session["zcoin_balance"] = int(user.get("zcoin_balance") or 0)
            return cache_set("_rz_current_user", user)
    except Exception as exc:
        print(f"current_user warning: {exc}")

    # Fallback để tránh trắng trang khi Supabase ngắt kết nối vài giây.
    fallback_user = {
        "id": user_id,
        "username": session.get("username", "player"),
        "display_name": session.get("display_name", "Player"),
        "avatar_url": session.get("avatar_url"),
        "role": session.get("role", "player"),
        "account_status": session.get("account_status", "approved"),
        "admin_level": session.get("admin_level", "none"),
        "zcoin_balance": int(session.get("zcoin_balance") or 0),
        "rank_points": 0,
        "is_online": True,
        "matchmaking_cooldown_until": None,
    }
    return cache_set("_rz_current_user", fallback_user)


def is_player_in_cooldown(user):
    cooldown = parse_dt(user.get("matchmaking_cooldown_until"))
    return bool(cooldown and cooldown > now_dt())


def cooldown_text(user):
    cooldown = parse_dt(user.get("matchmaking_cooldown_until"))
    if not cooldown or cooldown <= now_dt():
        return ""
    seconds = int((cooldown - now_dt()).total_seconds())
    minutes = max(1, seconds // 60 + (1 if seconds % 60 else 0))
    return f"{minutes} phút"



def current_pending_invites():
    cached = cache_get("_rz_current_pending_invites")
    if cached is not None:
        return cached
    try:
        user = current_user()
        if not user:
            return cache_set("_rz_current_pending_invites", [])
        invites = list_invites("pending")
        return cache_set("_rz_current_pending_invites", [invite for invite in invites if invite["to_user_id"] == user["id"]])
    except Exception as exc:
        print(f"current_pending_invites warning: {exc}")
        return []


def current_pending_invite_count():
    try:
        return len(current_pending_invites())
    except Exception:
        return 0


def room_is_active(room):
    if room.get("status") in {"waiting_ready", "playing", "friendly_playing", "waiting_result_confirm"}:
        return True
    return (
        room.get("status") == "confirmed"
        and (room.get("note") or "") in {REMATCH_HOST_READY_NOTE, REMATCH_GUEST_READY_NOTE}
    )


ACTIVE_ROOM_STATUSES = {
    "waiting_ready",
    "playing",
    "friendly_playing",
    "waiting_result_confirm",
    "waiting_confirm",
    "disputed",
}


def _direct_active_rooms_for_user(user_id, limit=20):
    """Đọc phòng active trực tiếp từ bảng match_rooms, không dùng cache Vercel."""
    if not user_id:
        return []
    result = execute_query(
        db.table("match_rooms")
        .select("*")
        .or_(f"host_user_id.eq.{user_id},guest_user_id.eq.{user_id}")
        .in_("status", sorted(ACTIVE_ROOM_STATUSES))
        .order("updated_at", desc=True)
        .limit(limit),
        "active_room_for_user_direct",
        attempts=2,
    )
    return list(result.data or [])


def cleanup_duplicate_waiting_rooms(user_id):
    """Xóa an toàn các phòng waiting_ready bị nhân đôi của một người chơi.

    Chỉ đụng tới phòng chưa có match_id. Phòng có đối thủ được ưu tiên giữ lại;
    nếu nhiều phòng cùng loại thì giữ phòng cập nhật mới nhất. Lời mời gắn với
    phòng bị xóa cũng được đóng để không còn trạng thái treo.
    """
    try:
        rooms = [
            room for room in _direct_active_rooms_for_user(user_id)
            if str(room.get("status") or "") == "waiting_ready" and not room.get("match_id")
        ]
    except Exception as exc:
        print(f"cleanup_duplicate_waiting_rooms load warning: {exc}")
        return 0
    if len(rooms) <= 1:
        return 0

    rooms.sort(
        key=lambda room: (
            1 if room.get("guest_user_id") else 0,
            1 if room.get("invite_id") else 0,
            str(room.get("updated_at") or room.get("created_at") or ""),
            str(room.get("id") or ""),
        ),
        reverse=True,
    )
    keep_id = str(rooms[0].get("id"))
    removed = 0
    for room in rooms[1:]:
        room_id = room.get("id")
        if not room_id or str(room_id) == keep_id:
            continue
        try:
            deleted = execute_query(
                db.table("match_rooms").delete()
                .eq("id", room_id)
                .eq("status", "waiting_ready")
                .is_("match_id", "null"),
                "cleanup_duplicate_waiting_room",
                attempts=2,
            )
            if deleted.data:
                removed += 1
                invite_id = room.get("invite_id")
                if invite_id:
                    execute_query(
                        db.table("match_invites").update({
                            "status": "cancelled",
                            "updated_at": now_iso(),
                        }).eq("id", invite_id).eq("status", "pending"),
                        "cleanup_duplicate_waiting_room_invite",
                        attempts=2,
                    )
        except Exception as exc:
            print(f"cleanup duplicate room warning room={room_id}: {exc}")
    if removed:
        cache_delete("_rz_rooms_all")
        cache_delete("_rz_invites_all")
        cache_delete("_rz_current_pending_invites")
        ttl_cache_delete("rooms_raw")
        ttl_cache_delete("invites_raw")
    return removed


def active_room_for_user(user_id, exclude_room_id=None):
    """Tìm trực tiếp mọi phòng active của người chơi từ match_rooms."""
    if not user_id:
        return None
    try:
        rooms = _direct_active_rooms_for_user(user_id)
        for room in rooms:
            if exclude_room_id and str(room.get("id")) == str(exclude_room_id):
                continue
            return room
    except Exception as exc:
        print(f"active_room_for_user direct warning: {exc}")

    try:
        for room in list_rooms():
            if exclude_room_id and str(room.get("id")) == str(exclude_room_id):
                continue
            if str(room.get("status") or "").lower() in ACTIVE_ROOM_STATUSES and user_id in [room.get("host_user_id"), room.get("guest_user_id")]:
                return room
    except Exception as exc:
        print(f"active_room_for_user fallback warning: {exc}")
    return None


def build_room_head_to_head(room):
    """Thống kê đối đầu trong phòng bằng truy vấn nhỏ, có fallback an toàn.

    Trước đây mỗi lần khách nhận thay đổi trạng thái và tải lại phòng, hàm này
    gọi ``list_matches("confirmed")`` nên phải lấy và làm giàu toàn bộ lịch sử
    trận của hệ thống. Phòng chơi nhiều ván liên tiếp vì thế có thể tải chậm hơn,
    đặc biệt ở phía khách vốn đồng bộ bằng polling. Bản này chỉ đọc các cột cần
    thiết của đúng hai người chơi kể từ thời điểm mở phòng.
    """
    host_id = room.get("host_user_id")
    guest_id = room.get("guest_user_id")
    room_created_at = room.get("created_at")
    room_opened_at = parse_dt(room_created_at)

    empty = {
        "available": bool(host_id and guest_id),
        "total": 0,
        "host_wins": 0,
        "guest_wins": 0,
        "draws": 0,
        "host_goals": 0,
        "guest_goals": 0,
        "matches": [],
        "since": format_vn_datetime(room_created_at),
    }
    if not host_id or not guest_id:
        return empty

    raw_matches = None
    try:
        pair_filter = (
            f"and(player1_id.eq.{host_id},player2_id.eq.{guest_id}),"
            f"and(player1_id.eq.{guest_id},player2_id.eq.{host_id})"
        )
        query = (
            db.table("matches")
            .select("id,player1_id,player2_id,score1,score2,delta1,delta2,created_at,status")
            .eq("status", "confirmed")
            .or_(pair_filter)
        )
        if room_created_at:
            query = query.gte("created_at", room_created_at)
        query = query.order("created_at", desc=True).limit(100)
        result = execute_query(query, "room_head_to_head_pair", attempts=2)
        raw_matches = result.data or []
    except Exception as exc:
        # Không để phần lịch sử phụ làm hỏng toàn bộ phòng nếu Supabase/PostgREST
        # tạm thời không nhận bộ lọc OR. Fallback giữ nguyên hành vi bản cũ.
        app.logger.warning("Room head-to-head optimized query failed; using cache fallback: %s", exc)

    pair = {str(host_id), str(guest_id)}
    if raw_matches is None:
        raw_matches = []
        for match in list_matches("confirmed"):
            if {str(match.get("player1_id")), str(match.get("player2_id"))} != pair:
                continue
            match_time = parse_dt(match.get("created_at"))
            if room_opened_at and match_time and match_time < room_opened_at:
                continue
            raw_matches.append(match)

    selected = []
    for match in raw_matches:
        if {str(match.get("player1_id")), str(match.get("player2_id"))} != pair:
            continue
        match_time = parse_dt(match.get("created_at"))
        if room_opened_at and match_time and match_time < room_opened_at:
            continue

        try:
            score1 = int(match.get("score1") or 0)
            score2 = int(match.get("score2") or 0)
        except (TypeError, ValueError):
            continue

        host_is_player1 = str(match.get("player1_id")) == str(host_id)
        host_score = score1 if host_is_player1 else score2
        guest_score = score2 if host_is_player1 else score1
        item = {
            "id": match.get("id"),
            "created_at_display": format_vn_datetime(match.get("created_at")),
            "host_score": host_score,
            "guest_score": guest_score,
            "host_delta": _normalize_match_delta(
                match.get("delta1") if host_is_player1 else match.get("delta2")
            ),
            "guest_delta": _normalize_match_delta(
                match.get("delta2") if host_is_player1 else match.get("delta1")
            ),
        }
        selected.append(item)

        empty["host_goals"] += host_score
        empty["guest_goals"] += guest_score
        if host_score > guest_score:
            empty["host_wins"] += 1
        elif guest_score > host_score:
            empty["guest_wins"] += 1
        else:
            empty["draws"] += 1

    empty["total"] = len(selected)
    # Cột phải chỉ cần các trận mới nhất; tổng W-D-L vẫn tính trên toàn phiên.
    empty["matches"] = selected[:8]
    return empty


def _room_by_match_id(rooms):
    return {
        str(room.get("match_id")): room
        for room in (rooms or [])
        if room.get("match_id") not in (None, "")
    }


def match_blocks_new_room(match, linked_room=None):
    """Chỉ khóa người chơi khi trận còn gắn với một phòng đang hoạt động.

    Một bản ghi ``matches`` còn ``playing``/``waiting_confirm`` nhưng phòng đã
    ``cancelled`` hoặc không còn tồn tại là dữ liệu mồ côi. Nó không được tiếp
    tục chặn người chơi tạo phòng mới.
    """
    if not match or match.get("status") not in {"playing", "waiting_confirm"}:
        return False
    return bool(linked_room and room_is_active(linked_room))


def active_match_for_user(user_id):
    """Trả về trận thật sự đang khóa người chơi, bỏ qua match mồ côi."""
    user_key = str(user_id)
    rooms = list_rooms()
    rooms_by_match = _room_by_match_id(rooms)
    for match in list_matches():
        if user_key not in {str(match.get("player1_id")), str(match.get("player2_id"))}:
            continue
        linked_room = rooms_by_match.get(str(match.get("id")))
        if match_blocks_new_room(match, linked_room):
            return match
    return None


def busy_user_ids(rooms=None, matches=None):
    """Trả về tập user đang có phòng hoặc trận thật sự chưa hoàn tất."""
    rooms = list_rooms() if rooms is None else rooms
    matches = list_matches() if matches is None else matches
    busy = set()

    for room in rooms:
        if room_is_active(room):
            busy.add(room.get("host_user_id"))
            busy.add(room.get("guest_user_id"))

    rooms_by_match = _room_by_match_id(rooms)
    for match in matches:
        linked_room = rooms_by_match.get(str(match.get("id")))
        if match_blocks_new_room(match, linked_room):
            busy.add(match.get("player1_id"))
            busy.add(match.get("player2_id"))

    busy.discard(None)
    return busy


def has_active_room_between(user_a, user_b):
    active_statuses = {"waiting_ready", "playing", "waiting_result_confirm"}
    for room in list_rooms():
        same_pair = {room.get("host_user_id"), room.get("guest_user_id")} == {user_a, user_b}
        if same_pair and room.get("status") in active_statuses:
            return True
    return False


def has_active_match_between(user_a, user_b):
    active_statuses = {"playing", "waiting_confirm"}
    for match in list_matches():
        same_pair = {match.get("player1_id"), match.get("player2_id")} == {user_a, user_b}
        if same_pair and match.get("status") in active_statuses:
            return True
    return False


def has_pending_invite_between(user_a, user_b):
    for invite in list_invites("pending"):
        same_pair = {invite.get("from_user_id"), invite.get("to_user_id")} == {user_a, user_b}
        if same_pair:
            return True
    return False


def is_solo_waiting_room(room, user_id):
    """True only when user is the host of an empty room that has not started."""
    if not room or not user_id:
        return False
    return bool(
        str(room.get("host_user_id")) == str(user_id)
        and room.get("status") == "waiting_ready"
        and not room.get("guest_user_id")
    )


def matchmaking_snapshot(user_a, user_b=None):
    """Fetch only the small raw state needed by invite actions.

    This avoids loading/enriching every room, match, achievement and team merely
    to decide whether two users are available.
    """
    ids = {str(user_a)}
    if user_b:
        ids.add(str(user_b))
    rooms_result = execute_query(
        db.table("match_rooms")
        .select("id,match_id,host_user_id,guest_user_id,status,invite_id")
        .in_("status", ["waiting_ready", "playing", "friendly_playing", "waiting_result_confirm", "waiting_confirm", "disputed"]),
        "matchmaking_active_rooms",
        attempts=3,
    )
    matches_result = execute_query(
        db.table("matches")
        .select("id,player1_id,player2_id,status")
        .in_("status", ["playing", "waiting_confirm", "processing_result", "disputed"]),
        "matchmaking_active_matches",
        attempts=3,
    )
    invites_result = execute_query(
        db.table("match_invites")
        .select("id,from_user_id,to_user_id,status,expires_at,created_at")
        .eq("status", "pending"),
        "matchmaking_pending_invites",
        attempts=3,
    )
    rooms = [dict(x) for x in (rooms_result.data or [])]
    active_match_ids = {str(r.get("match_id")) for r in rooms if r.get("match_id")}
    # Trận mồ côi không còn phòng hoạt động không được chặn ghép trận/lời mời.
    matches = [dict(x) for x in (matches_result.data or []) if str(x.get("id")) in active_match_ids]

    invites = []
    now = now_dt()
    for raw in (invites_result.data or []):
        invite = dict(raw)
        expires_at = parse_dt(invite.get("expires_at"))
        if expires_at and expires_at <= now:
            try:
                execute_query(
                    db.table("match_invites").update({
                        "status": "expired",
                        "updated_at": now_iso(),
                    }).eq("id", invite.get("id")).eq("status", "pending"),
                    "matchmaking_expire_stale_invite",
                    attempts=1,
                )
            except Exception as exc:
                print(f"matchmaking stale invite warning id={invite.get('id')}: {exc}")
            continue
        invites.append(invite)

    def room_for(uid):
        uid = str(uid)
        return next((r for r in rooms if uid in {str(r.get("host_user_id")), str(r.get("guest_user_id"))}), None)

    def match_for(uid):
        uid = str(uid)
        return next((m for m in matches if uid in {str(m.get("player1_id")), str(m.get("player2_id"))}), None)

    pair_pending = False
    if user_b:
        target = {str(user_a), str(user_b)}
        pair_pending = any({str(i.get("from_user_id")), str(i.get("to_user_id"))} == target for i in invites)
    return {
        "rooms": rooms,
        "matches": matches,
        "invites": invites,
        "room_a": room_for(user_a),
        "room_b": room_for(user_b) if user_b else None,
        "match_a": match_for(user_a),
        "match_b": match_for(user_b) if user_b else None,
        "pair_pending": pair_pending,
    }


def mark_current_user_active():
    user_id = session.get("user_id")
    if not user_id:
        return

    # Admin có thể chủ động ẩn trạng thái Online trong chính phiên đăng nhập.
    # Người chơi thường luôn dùng presence tự động như trước.
    try:
        cached_user = current_user()
    except Exception:
        cached_user = None
    is_admin_account = bool(cached_user and is_admin_user(cached_user))
    forced_offline = is_admin_account and session.get("admin_presence_mode") == "offline"

    try:
        db.table("users").update({
            "is_online": not forced_offline,
            "last_seen_at": now_iso(),
        }).eq("id", user_id).execute()
        # Dữ liệu Players được cache RAM ngắn. Xóa cache sau heartbeat để các
        # instance đang ấm không tiếp tục dùng last_seen_at cũ.
        ttl_cache_delete("players_raw", f"user:{user_id}")
        cache_delete("_rz_players_all")
        cache_delete("_rz_current_user")
    except Exception as exc:
        print(f"Heartbeat warning: {exc}")


def mark_current_user_offline():
    """Đánh dấu offline khi tab/trình duyệt đóng; timeout vẫn là lớp dự phòng."""
    user_id = session.get("user_id")
    if not user_id:
        return
    try:
        db.table("users").update({
            "is_online": False,
            "last_seen_at": now_iso(),
        }).eq("id", user_id).execute()
        ttl_cache_delete("players_raw", f"user:{user_id}")
        cache_delete("_rz_players_all")
        cache_delete("_rz_current_user")
    except Exception as exc:
        print(f"Presence offline warning: {exc}")


def ensure_admin():
    global _admin_checked
    if _admin_checked or db is None:
        return

    admin = get_user_by_username("admin")
    if not admin:
        # Không tự tạo/reset mật khẩu owner trong runtime. Tài khoản sở hữu phải
        # được tạo bằng migration hoặc thao tác thủ công an toàn trong Supabase.
        app.logger.warning("Owner account 'admin' is missing; ensure_admin skipped creation for safety.")
    else:
        # Chỉ chuẩn hóa vai trò; tuyệt đối không ghi đè password_hash.
        execute_query(
            db.table("users").update({
                "display_name": "Admin",
                "role": "admin",
                "admin_level": "owner",
                "account_status": "approved",
            }).eq("username", "admin"),
            "ensure_admin_update_role_only",
        )

    _admin_checked = True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Bạn cần đăng nhập trước.", "warning")
            return redirect(url_for("login"))

        user = current_user()
        if not user:
            session.clear()
            flash("Phiên đăng nhập không hợp lệ.", "warning")
            return redirect(url_for("login"))

        status = user.get("account_status", "approved")
        if status != "approved":
            session.clear()
            messages = {
                "pending": "Tài khoản đang chờ Admin duyệt.",
                "rejected": "Tài khoản đã bị từ chối.",
                "banned": "Tài khoản đã bị khóa.",
            }
            flash(messages.get(status, "Tài khoản chưa được phép sử dụng."), "danger")
            return redirect(url_for("login"))

        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        user = None
        if user_id:
            try:
                user = get_user(user_id)
                if user:
                    decorate_player_achievements(user)
                    session["username"] = user.get("username", "")
                    session["display_name"] = user.get("display_name", "")
                    session["avatar_url"] = user.get("avatar_url")
                    session["role"] = user.get("role", "player")
                    session["account_status"] = user.get("account_status", "approved")
                    session["admin_level"] = user.get("admin_level", "none")
                    session["zcoin_balance"] = int(user.get("zcoin_balance") or 0)
                    cache_set("_rz_current_user", user)
            except Exception as exc:
                print(f"admin_required warning: {exc}")

        if not user:
            session.clear()
            flash("Phiên đăng nhập admin không hợp lệ. Vui lòng đăng nhập lại.", "warning")
            return redirect(url_for("admin_login"))

        if not is_admin_user(user):
            flash("Bạn không có quyền admin.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not is_owner_user(user):
            flash("Chỉ chủ hệ thống mới có quyền này.", "danger")
            return redirect(url_for("admin"))
        return view(*args, **kwargs)
    return wrapped


def admin_permission_required(permission_code: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not has_admin_permission(user, permission_code):
                flash("Admin phụ chưa được Chủ hệ thống cấp quyền sử dụng chức năng này.", "danger")
                return redirect_admin("overview")
            return view(*args, **kwargs)
        return wrapped
    return decorator

@app.before_request
def enforce_server_maintenance():
    """Khóa toàn bộ website cho người dùng thường, kể cả /login.

    Admin luôn dùng /admin-login để vào hệ thống khi máy chủ đang bảo trì.
    Static assets và trang đăng nhập Admin được phép để màn hình bảo trì vẫn tải đẹp.
    """
    endpoint = request.endpoint or ""
    allowed_public = {"static", "admin_login"}
    if endpoint in allowed_public:
        return None

    status = get_maintenance_status()
    if not status.get("closed"):
        return None

    if _current_session_is_admin():
        return None

    # Không cho người dùng thường lách qua /login, API, link trực tiếp hoặc phiên cũ.
    if session.get("user_id"):
        session.clear()
    response = make_response(render_template("maintenance.html", maintenance=status), 503)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.before_request
def before_request():
    try:
        # Chạy tối đa 1 lần/6 giờ cho toàn hệ thống để tạo cảnh báo 3 ngày
        # và áp dụng RP suy giảm kể cả khi người chơi chưa quay lại đăng nhập.
        if request.endpoint != "static":
            process_inactivity_decay_batch()

        # V4.9: chỉ thao tác thật của người dùng mới gia hạn phiên. Heartbeat/polling không gia hạn.
        if session.get("user_id"):
            now_ts = int(time.time())
            last_real = int(session.get("last_real_activity", 0) or 0)

            # V1.14.41.78: người chơi có thể chuyển sang cửa sổ PES/Parsec trong khi
            # trang phòng nằm nền. Mọi request thuộc đúng phòng đấu được xem là
            # hoạt động hợp lệ để không bị đăng xuất giữa trận.
            room_request_active = (
                request.path.startswith("/room/")
                or request.path.startswith("/api/room/")
            )
            if room_request_active:
                session["last_real_activity"] = now_ts
                session.modified = True
                last_real = now_ts

            if not last_real:
                session["last_real_activity"] = now_ts
            elif now_ts - last_real >= IDLE_TIMEOUT_SECONDS and request.endpoint not in {"logout", "static", "api_session_timeout_check", "api_session_activity"}:
                room = None
                try:
                    room = active_room_for_user(session.get("user_id"))
                except Exception as exc:
                    print(f"idle room check warning: {exc}")
                decision = idle_decision(now_ts=now_ts, last_activity_ts=last_real, room=room)
                if decision.protected:
                    # Tuyệt đối không đăng xuất khi người chơi đang ở một trận/phòng cần hoàn tất.
                    session["last_real_activity"] = now_ts
                    session.modified = True
                elif decision.expired:
                    try:
                        execute_query(
                            db.table("users").update({"is_online": False, "last_seen_at": now_iso()}).eq("id", session.get("user_id")),
                            "idle_logout_mark_offline",
                            attempts=1,
                        )
                    except Exception as exc:
                        print(f"idle logout warning: {exc}")
                    session.clear()
                    if request.path.startswith("/api/"):
                        return jsonify({"ok": False, "error": "session_expired", "redirect": url_for("login")}), 401
                    flash("Bạn đã được đăng xuất do không hoạt động trong 60 phút.", "warning")
                    return redirect(url_for("login"))

        # Không gọi ensure_admin() ở mọi request. Trước đây mỗi Vercel instance mới
        # lại đọc + cập nhật bảng users trước khi tải /bxh, tạo thêm kết nối Supabase
        # và có thể gây [Errno 16] Device or resource busy.
        if db is not None and session.get("user_id"):
            # Chỉ cập nhật online tối đa 1 lần/45 giây thay vì ở mọi request
            # (HTML, API, ảnh, heartbeat đều từng tạo một lệnh UPDATE riêng).
            now_ts = int(time.time())
            last_touch = int(session.get("last_activity_touch", 0) or 0)
            if request.endpoint == "heartbeat" or now_ts - last_touch >= 45:
                mark_current_user_active()
                session["last_activity_touch"] = now_ts

            user = current_user()
            allowed = {"change_password", "logout", "static", "heartbeat"}
            if user and user.get("must_change_password") and request.endpoint not in allowed:
                flash("Bạn đang dùng mật khẩu tạm thời. Hãy đổi mật khẩu mới để tiếp tục.", "warning")
                return redirect(url_for("change_password"))
    except Exception as exc:
        # Lỗi cập nhật online không được phép làm hỏng route chính.
        print(f"Before request warning: {exc}")


@app.context_processor

def inject_globals():
    try:
        user = current_user()
    except Exception as exc:
        print(f"inject user warning: {exc}")
        user = None

    if request.endpoint == "change_password":
        return {
            "APP_NAME": APP_NAME,
            "current_user": user,
            "get_rank_name": get_rank_name,
            "get_rank_info": get_rank_info,
            "get_rank_display": get_rank_display,
            "get_team_overall": get_team_overall,
            "get_team_tier": get_team_tier,
        "get_win_streak_title": get_win_streak_title,
        "get_win_streak_badge": get_win_streak_badge,
        "get_league_logo_url": get_league_logo_url,
            "TEAM_COUNT": TEAM_COUNT,
            "APP_VERSION": APP_VERSION,
            "RANKS": load_rank_ranges(),
            "format_vn_datetime": format_vn_datetime,
            "pending_invite_count": 0,
            "incoming_invites": [],
            "active_room": None,
            "cooldown_text": "",
            "active_announcement": None,
            "bell_notifications": [],
            "unread_notification_count": 0,
        }

    # Tối ưu phản hồi HTML: không chặn render để chờ phòng, lời mời và thông báo
    # hệ thống. Các dữ liệu này đã có API nền trong base.html và sẽ xuất hiện ngay
    # sau khi trang hiển thị. Chỉ giữ thông báo cá nhân vì chưa có API riêng.
    pending_count = 0
    incoming = []
    active_room = None
    cooldown = cooldown_text(user) if user else ""
    announcement = None
    try:
        bell_notifications = list_bell_notifications(user.get("id"), 20) if user else []
        unread_notification_count = sum(1 for notice in bell_notifications if not notice.get("is_read"))
    except Exception:
        bell_notifications = []
        unread_notification_count = 0

    return {
        "APP_NAME": APP_NAME,
        "current_user": user,
        "get_rank_name": get_rank_name,
        "get_rank_info": get_rank_info,
        "get_rank_display": get_rank_display,
        "get_team_overall": get_team_overall,
        "get_team_tier": get_team_tier,
        "get_win_streak_title": get_win_streak_title,
        "get_win_streak_badge": get_win_streak_badge,
        "TEAM_COUNT": TEAM_COUNT,
        "APP_VERSION": APP_VERSION,
        "RANKS": load_rank_ranges(),
        "format_vn_datetime": format_vn_datetime,
        "pending_invite_count": pending_count,
        "incoming_invites": incoming,
        "active_room": active_room,
        "cooldown_text": cooldown,
        "active_announcement": announcement,
        "bell_notifications": bell_notifications,
        "unread_notification_count": unread_notification_count,
        "quick_match_config": get_quick_match_config(),
    }


@app.route("/notifications")
@login_required
def notifications():
    user = current_user()
    unread_only = (request.args.get("filter") or "all") == "unread"
    notices, _ = list_user_notifications(
        user.get("id"), page=1, per_page=20, unread_only=unread_only
    )
    return render_template(
        "notifications.html",
        notifications=notices,
        page=1,
        has_next=False,
        notification_filter="unread" if unread_only else "all",
        notification_retention_days=7,
        notification_max_items=20,
    )


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    user = current_user()
    execute_query(
        db.table("user_notifications").update({
            "is_read": True,
            "read_at": now_iso(),
        }).eq("user_id", user.get("id")).eq("is_read", False),
        "mark_all_notifications_read",
    )
    ttl_cache_delete(f"bell_notifications:{user.get('id')}")
    flash("Đã đánh dấu tất cả thông báo là đã đọc.", "success")
    return redirect(url_for("notifications"))


@app.route("/notification/<notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    user = current_user()
    execute_query(
        db.table("user_notifications").update({
            "is_read": True,
            "read_at": now_iso(),
        }).eq("id", notification_id).eq("user_id", user.get("id")),
        "mark_notification_read",
    )
    ttl_cache_delete(f"bell_notifications:{user.get('id')}")
    next_url = request.form.get("next_url", "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/session/activity", methods=["POST"])
@login_required
def api_session_activity():
    """Gia hạn phiên chỉ khi trình duyệt báo có thao tác thật của người dùng."""
    now_ts = int(time.time())
    session["last_real_activity"] = now_ts
    session["last_activity_touch"] = now_ts
    session.modified = True
    return jsonify({"ok": True, "last_activity": now_ts})


@app.route("/api/session/timeout-check")
@login_required
def api_session_timeout_check():
    """Chỉ gọi một lần khi bộ đếm 60 phút hết; không phải polling."""
    user = current_user()
    room = None
    try:
        if user:
            room = active_room_for_user(user.get("id"))
    except Exception as exc:
        print(f"timeout check room warning: {exc}")
    protected = room_blocks_idle_logout(room)
    return jsonify({
        "ok": True,
        "protected": protected,
        "room_url": url_for("room_detail", room_id=room.get("id")) if protected and room else None,
    })


@app.route("/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    mark_current_user_active()
    return jsonify({"ok": True})


@app.route("/presence/offline", methods=["POST"])
@login_required
def presence_offline():
    # sendBeacon không cần phản hồi JSON lớn. Khi chỉ chuyển trang nội bộ,
    # before_request/heartbeat của trang mới sẽ đánh dấu online lại ngay.
    mark_current_user_offline()
    return ("", 204)


@app.route("/api/invites/pending")
@login_required
def api_pending_invites():
    """Truy vấn trực tiếp lời mời của người hiện tại để giảm độ trễ popup."""
    user = current_user()
    if not user:
        return jsonify({"invites": []})

    try:
        # Lấy nhiều bản ghi thay vì chỉ 1 bản ghi mới nhất. Nếu lời mời mới nhất
        # vừa hết hạn trong lúc xử lý, lời mời hợp lệ cũ hơn vẫn phải được trả về.
        result = execute_query(
            db.table("match_invites")
              .select("id,from_user_id,to_user_id,tier,status,expires_at,created_at")
              .eq("to_user_id", user["id"])
              .eq("status", "pending")
              .order("created_at", desc=True)
              .limit(20),
            "api_pending_invites_direct",
            attempts=2,
        )
        rows = result.data or []
        data = []
        for row in rows:
            invite = expire_invite_if_needed(dict(row))
            if invite.get("status") != "pending":
                continue
            sender = get_user(invite.get("from_user_id")) or {}
            decorate_player_achievements(sender)
            data.append({
                "id": invite["id"],
                "from_name": sender.get("display_name", "Unknown"),
                "from_avatar_url": sender.get("avatar_url"),
                "from_avatar_frame": sender.get("avatar_frame"),
                "from_achievement": sender.get("featured_achievement"),
                "from_rank": get_rank_display(sender.get("rank_points", 0)),
                "from_points": sender.get("rank_points", 0),
                "tier": invite.get("tier") or SMART_RANDOM_MODE,
                "expires_in_seconds": int(invite.get("expires_in_seconds") or 0),
                "accept_url": url_for("respond_invite", invite_id=invite["id"]),
                "reject_url": url_for("respond_invite", invite_id=invite["id"]),
            })
        response = jsonify({"invites": data})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["X-Invite-Poll"] = "fast-active"
        return response
    except Exception as exc:
        # Không trả danh sách rỗng khi DB lỗi vì phía trình duyệt sẽ hiểu nhầm là
        # không còn lời mời và tự ẩn popup đang hiển thị.
        print(f"api_pending_invites ERROR user={user.get('id')}: {type(exc).__name__}: {exc}")
        response = jsonify({"ok": False, "error": "invite_poll_failed"})
        response.status_code = 503
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response



@app.route("/api/active-room")
@login_required

def api_active_room():
    user = current_user()

    if not user or user.get("role") == "admin":
        return jsonify({"ok": True, "has_room": False})

    try:
        room = active_room_for_user(user["id"])
    except Exception:
        return jsonify({"ok": False, "has_room": False, "error": "temporary_db_error"}), 503

    if not room:
        return jsonify({"ok": True, "has_room": False})

    is_host = room.get("host_user_id") == user["id"]
    is_guest = room.get("guest_user_id") == user["id"]
    has_opponent = bool(room.get("guest_user_id"))

    # Chỉ ép quay lại khi trận đã bắt đầu hoặc đang chờ xác nhận.
    # Phòng trống/chờ sẵn sàng vẫn cho phép người dùng xem các trang khác.
    must_finish_statuses = {"playing", "friendly_playing", "waiting_result_confirm"}
    auto_redirect = bool(room.get("status") in must_finish_statuses and has_opponent)

    return jsonify({
        "ok": True,
        "has_room": True,
        "room_id": room["id"],
        "room_url": url_for("room_detail", room_id=room["id"]),
        "status": room.get("status"),
        "is_host": is_host,
        "is_guest": is_guest,
        "has_opponent": has_opponent,
        "auto_redirect": auto_redirect,
    })

def build_room_state_key(room):
    """Tạo khóa trạng thái ổn định dùng chung cho HTML và API phòng đấu."""
    return "|".join([
        # Thành viên phòng phải nằm trong state key. Nếu khách vừa tham gia
        # nhưng status vẫn là waiting_ready và guest_ready vẫn False, thiếu
        # guest_user_id sẽ khiến chủ phòng nhận 204 và không làm mới giao diện.
        str(room.get("host_user_id")),
        str(room.get("guest_user_id")),
        str(room.get("status")),
        str(room.get("host_team")),
        str(room.get("guest_team")),
        str(room.get("guest_ready")),
        str(room.get("host_score")),
        str(room.get("guest_score")),
        str(room.get("rematch_host_ready")),
        str(room.get("rematch_guest_ready")),
        str(room.get("rematch_host_declined")),
        str(room.get("rematch_guest_declined")),
        str(room.get("rematch_expired")),
        str(room.get("state_expires_at")),
        str((room.get("dispute") or {}).get("status")),
        str((room.get("dispute") or {}).get("updated_at")),
        str(room.get("parsec_link")),
        str(room.get("host_name_style_class")),
        str(room.get("guest_name_style_class")),
        str(((room.get("host_profile_badge") or {}).get("image_url"))),
        str(((room.get("guest_profile_badge") or {}).get("image_url"))),
    ])


def polling_stop_response(reason="stopped"):
    """Kết thúc một poller cũ mà không tạo lỗi 4xx trên trình duyệt."""
    response = app.response_class(status=204)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-PES-Polling-Stop"] = str(reason or "stopped")[:80]
    return response


@app.route("/api/room/<room_id>/state")
@login_required

def api_room_state(room_id):
    user = current_user()

    try:
        room = get_room(room_id)
    except Exception:
        return jsonify({"ok": False, "error": "temporary_db_error"}), 503

    if not room:
        return polling_stop_response("room_not_found")

    if close_room_if_host_browser_offline(room):
        return polling_stop_response("host_browser_offline")

    if user["id"] not in [room["host_user_id"], room["guest_user_id"]] and not is_admin_user(user):
        return polling_stop_response("room_access_ended")

    state_key = build_room_state_key(room)

    # V4.1: nếu trạng thái chưa đổi, trả response rỗng để giảm dữ liệu truyền.
    # Client vẫn giữ polling nhưng không phải nhận/phân tích JSON lặp lại.
    since_state_key = (request.args.get("since") or "").strip()
    if since_state_key and since_state_key == state_key:
        response = app.response_class(status=204)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["X-Room-State-Unchanged"] = "1"
        return response

    rematch_declined_by_me = (
        (user["id"] == room.get("host_user_id") and room.get("rematch_host_declined"))
        or (user["id"] == room.get("guest_user_id") and room.get("rematch_guest_declined"))
    )

    return jsonify({
        "ok": True,
        "state_key": state_key,
        "status": room.get("status"),
        "rematch_declined": bool(room.get("rematch_declined")),
        "rematch_declined_by_me": bool(rematch_declined_by_me),
        "rematch_expired": bool(room.get("rematch_expired")),
        "timeout_seconds": int(room.get("timeout_seconds") or 0),
        "timeout_label": room.get("timeout_label") or "",
    })

# =========================
# Auth
# =========================
@app.route("/")
def index():
    # Trang chủ công khai luôn mở thẳng Bảng xếp hạng.
    # Người dùng chỉ được chuyển tới màn hình đăng nhập khi chủ động bấm Đăng nhập.
    return redirect(url_for("ranking"))


@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    get_device_id()

    existing = current_user() if session.get("user_id") else None
    if existing and is_admin_user(existing):
        return redirect(url_for("admin"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        try:
            user = get_user_by_username(username)
        except Exception as exc:
            app.logger.warning("Admin login database warning: %s", exc)
            flash("Máy chủ dữ liệu đang bận. Vui lòng thử lại sau vài giây.", "warning")
            return redirect(url_for("admin_login"))

        if not user or user.get("password_hash") != hash_password(password):
            flash("Sai tài khoản hoặc mật khẩu Admin.", "danger")
            return redirect(url_for("admin_login"))
        if user.get("account_status", "approved") != "approved" or not is_admin_user(user):
            flash("Tài khoản này không có quyền truy cập trang quản trị.", "danger")
            return redirect(url_for("admin_login"))

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user.get("username", "")
        session["display_name"] = user.get("display_name", "")
        session["avatar_url"] = user.get("avatar_url")
        session["role"] = user.get("role", "player")
        session["account_status"] = user.get("account_status", "approved")
        session["admin_level"] = user.get("admin_level", "none")
        session["zcoin_balance"] = int(user.get("zcoin_balance") or 0)
        session["last_real_activity"] = int(time.time())
        session["last_activity_touch"] = int(time.time())
        execute_query(
            db.table("users").update({"is_online": True, "last_seen_at": now_iso()}).eq("id", user["id"]),
            "admin_login_mark_online",
            attempts=2,
        )
        return redirect(url_for("admin"))

    return render_template("admin_login.html", auth_only=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    get_device_id()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        try:
            user = get_user_by_username(username)
        except Exception as exc:
            # A temporary Supabase/Vercel socket failure must not become a raw 500.
            print(f"Login database warning: {exc}")
            flash("Máy chủ dữ liệu đang bận. Vui lòng đăng nhập lại sau vài giây.", "warning")
            return redirect(url_for("login"))

        if not user or user["password_hash"] != hash_password(password):
            flash("Sai tên tài khoản hoặc mật khẩu.", "danger")
            return redirect(url_for("login"))

        status = user.get("account_status", "approved")
        if status != "approved":
            messages = {
                "pending": "Tài khoản của bạn đang chờ Admin duyệt.",
                "rejected": "Tài khoản của bạn đã bị từ chối.",
                "banned": "Tài khoản của bạn đã bị khóa. Hãy liên hệ Admin.",
            }
            flash(messages.get(status, "Tài khoản chưa được phép đăng nhập."), "danger")
            return redirect(url_for("login"))

        ok, msg = link_device_to_user(user)
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("login"))

        remember_account = request.form.get("remember_account") == "1"
        session.permanent = remember_account
        session["remember_account"] = remember_account
        session["user_id"] = user["id"]
        session["username"] = user.get("username", "")
        session["display_name"] = user.get("display_name", "")
        session["avatar_url"] = user.get("avatar_url")
        session["role"] = user.get("role", "player")
        session["account_status"] = status
        session["admin_level"] = user.get("admin_level", "none")
        session["zcoin_balance"] = int(user.get("zcoin_balance") or 0)
        session["last_real_activity"] = int(time.time())
        session["last_activity_touch"] = int(time.time())
        # Tính RP không hoạt động trước khi cập nhật last_seen_at của lần đăng nhập mới.
        try:
            process_inactivity_for_user(user)
        except Exception as exc:
            print(f"Login inactivity decay warning: {exc}")
        execute_query(
            db.table("users").update({"is_online": True, "last_seen_at": now_iso()}).eq("id", user["id"]),
            "login_mark_online",
        )

        if user.get("must_change_password"):
            flash("Đăng nhập bằng mật khẩu tạm thành công. Hãy tạo mật khẩu mới.", "warning")
            return redirect(url_for("change_password"))

        # Người mở link chia sẻ khi chưa đăng nhập sẽ được đưa trở lại đúng
        # phòng sau khi đăng nhập, thay vì bị rơi về Dashboard/BXH.
        pending_room_join_id = session.pop("pending_room_join_id", None)
        if pending_room_join_id:
            return redirect(url_for("room_join_shared", room_id=pending_room_join_id))

        return redirect(url_for(post_login_endpoint(get_system_features(), is_admin=is_admin_user(user))))

    return render_template("login.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        zalo_name = request.form.get("zalo_name", "").strip()
        user = get_user_by_username(username) if username else None

        matches_identity = bool(
            user
            and zalo_name
            and (user.get("zalo_name") or "").strip().casefold() == zalo_name.casefold()
        )

        if matches_identity:
            existing = execute_query(
                db.table("password_reset_requests")
                .select("id")
                .eq("user_id", user["id"])
                .eq("status", "pending")
                .limit(1),
                "find_pending_password_reset",
            )
            if not existing.data:
                execute_query(
                    db.table("password_reset_requests").insert({
                        "user_id": user["id"],
                        "username_snapshot": user.get("username"),
                        "zalo_name_snapshot": user.get("zalo_name"),
                        "status": "pending",
                        "requested_ip": get_client_ip(),
                    }),
                    "create_password_reset_request",
                )

        flash("Nếu tài khoản và tên Zalo khớp, yêu cầu đã được gửi đến Admin. Hãy liên hệ Admin qua Zalo để nhận mật khẩu tạm.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()
    if request.method == "POST":
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        if user.get("password_hash") != hash_password(current_password):
            flash("Mật khẩu tạm hoặc mật khẩu hiện tại không đúng.", "danger")
            return redirect(url_for("change_password"))
        valid_password, password_error = validate_new_password(new_password)
        if not valid_password:
            flash(password_error, "danger")
            return redirect(url_for("change_password"))
        if hash_password(new_password) == user.get("password_hash"):
            flash("Mật khẩu mới phải khác mật khẩu tạm hoặc mật khẩu hiện tại.", "warning")
            return redirect(url_for("change_password"))

        changed_at = now_iso()
        execute_query(
            db.table("users").update({
                "password_hash": hash_password(new_password),
                "must_change_password": False,
                "password_changed_at": changed_at,
            }).eq("id", user["id"]),
            "user_change_password",
        )
        try:
            execute_query(
                db.table("password_reset_requests").update({
                    "status": "resolved",
                    "admin_note": "User đã tự đổi mật khẩu.",
                    "resolved_at": changed_at,
                }).eq("user_id", user["id"]).eq("status", "pending"),
                "close_password_reset_after_user_change",
            )
        except Exception as exc:
            print(f"close password reset warning: {exc}")
        flash("Đã đổi mật khẩu thành công.", "success")
        pending_room_join_id = session.pop("pending_room_join_id", None)
        if pending_room_join_id:
            return redirect(url_for("room_join_shared", room_id=pending_room_join_id))
        return redirect(url_for("profile", user_id=user["id"]) + "#account-controls")

    if not user.get("must_change_password"):
        return redirect(url_for("profile", user_id=user["id"]) + "#account-controls")
    return render_template("change_password.html", force_change=True, auth_only=True, minimum_password_length=minimum_password_length())


@app.route("/register", methods=["GET", "POST"])
def register():
    if not system_feature_enabled("registration_codes_enabled"):
        flash("Tính năng đăng ký tài khoản đang tạm tắt.", "warning")
        return redirect(url_for("login"))
    get_device_id()

    if request.method == "POST":
        can_register, msg = device_can_register()
        if not can_register:
            flash(msg, "danger")
            return redirect(url_for("register"))

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        zalo_name = request.form.get("zalo_name", "").strip()

        if not username or not password or not zalo_name:
            flash("Vui lòng nhập đủ Tên tài khoản, Mật khẩu và Tên Zalo.", "danger")
            return redirect(url_for("register"))

        if len(username) < 3 or len(username) > 30:
            flash("Tên tài khoản phải từ 3 đến 30 ký tự.", "danger")
            return redirect(url_for("register"))

        if len(password) < minimum_password_length():
            flash(f"Mật khẩu phải có ít nhất {minimum_password_length()} ký tự.", "danger")
            return redirect(url_for("register"))

        if len(zalo_name) < 2 or len(zalo_name) > 80:
            flash("Tên Zalo không hợp lệ.", "danger")
            return redirect(url_for("register"))

        if get_user_by_username(username):
            flash("Tên tài khoản đã tồn tại.", "danger")
            return redirect(url_for("register"))

        ip = get_client_ip()
        ua = request.headers.get("User-Agent", "")

        created = execute_query(
            db.table("users").insert({
                "username": username,
                "password_hash": hash_password(password),
                "display_name": username,
                "zalo_name": zalo_name,
                "role": "player",
                "account_status": "pending",
                "invite_code_used": None,
                "rank_points": DEFAULT_POINTS,
                "register_ip": ip,
                "register_user_agent": ua,
            }),
            "register_user",
        )

        user = created.data[0]
        link_device_to_user(user)

        flash("Đăng ký thành công. Tài khoản đang chờ Admin duyệt.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    user_id = session.get("user_id")
    if user_id:
        try:
            execute_query(
                db.table("users").update({"is_online": False, "last_seen_at": now_iso()}).eq("id", user_id),
                "logout_mark_offline",
            )
        except Exception as exc:
            print(f"logout warning: {exc}")
    session.clear()
    if request.args.get("reason") == "inactive":
        flash("Bạn đã được đăng xuất do không hoạt động trong 60 phút.", "warning")
    else:
        flash("Đã đăng xuất.", "success")
    return redirect(url_for("login"))


# =========================
# Chat / Announcements
# =========================
@app.route("/chat")
@login_required
def lobby_chat():
    if not system_feature_enabled("lobby_chat_enabled"):
        return redirect(url_for("dashboard"))
    return render_template("chat.html", messages=list_chat_messages("global", limit=20))


@app.route("/chat/send", methods=["POST"])
@login_required
def send_global_chat():
    user = current_user()
    message = request.form.get("message", "")

    ok, error = create_chat_message(user["id"], message, scope="global")
    if not ok:
        flash(error, "warning")
    else:
        flash("Đã gửi tin nhắn.", "success")

    return redirect(url_for("lobby_chat"))


@app.route("/api/chat/global")
@login_required
def api_global_chat():
    if not system_feature_enabled("lobby_chat_enabled"):
        return polling_stop_response("lobby_chat_disabled")
    messages = list_chat_messages("global", limit=20)
    return jsonify({"ok": True, "messages": messages})


@app.route("/api/chat/global/status")
@login_required
def api_global_chat_status():
    if not system_feature_enabled("lobby_chat_enabled"):
        return polling_stop_response("lobby_chat_disabled")
    """Dữ liệu nhẹ để hiển thị số tin chat sảnh chưa đọc khi khung chat đang đóng."""
    user = current_user()
    limit = 100
    query = (
        db.table("chat_messages")
        .select("id,user_id,created_at")
        .eq("scope", "global")
        .is_("room_id", "null")
        .order("created_at", desc=True)
        .limit(limit)
    )
    result = execute_query(query, "api_global_chat_status")
    rows = list(reversed(result.data or []))

    messages = [
        {
            "id": row.get("id"),
            "created_at": row.get("created_at"),
            "is_own": row.get("user_id") == user.get("id"),
        }
        for row in rows
    ]

    return jsonify({
        "ok": True,
        "messages": messages,
        "latest_created_at": messages[-1]["created_at"] if messages else None,
        "limit_reached": len(messages) >= limit,
    })


@app.route("/api/room/<room_id>/chat")
@login_required
def api_room_chat(room_id):
    if not system_feature_enabled("room_chat_enabled"):
        return polling_stop_response("room_chat_disabled")
    user = current_user()
    room = get_room(room_id)

    if not room:
        return polling_stop_response("room_not_found")

    if user["id"] not in [room["host_user_id"], room["guest_user_id"]] and not is_admin_user(user):
        return polling_stop_response("room_access_ended")

    messages = list_chat_messages("room", room_id=room_id, limit=20)
    return jsonify({"ok": True, "messages": messages})


@app.route("/room/<room_id>/chat/send", methods=["POST"])
@login_required
def send_room_chat(room_id):
    if not system_feature_enabled("room_chat_enabled"):
        flash("Chat phòng đang bị tắt.", "warning")
        return redirect(url_for("room_detail", room_id=room_id))
    user = current_user()
    room = get_room(room_id)

    if not room:
        flash("Không tìm thấy phòng.", "danger")
        return redirect(url_for("rooms"))

    if user["id"] not in [room["host_user_id"], room["guest_user_id"]] and not is_admin_user(user):
        flash("Bạn không thuộc phòng này.", "danger")
        return redirect(url_for("rooms"))

    message = request.form.get("message", "")
    ok, error = create_chat_message(user["id"], message, scope="room", room_id=room_id)

    if not ok:
        flash(error, "warning")

    return redirect(url_for("room_detail", room_id=room_id))


@app.route("/api/room/<room_id>/chat/send", methods=["POST"])
@login_required
def api_send_room_chat(room_id):
    """Gửi chat phòng bằng AJAX, không redirect và không tải lại khung phòng."""
    if not system_feature_enabled("room_chat_enabled"):
        return jsonify({"ok": False, "disabled": True, "error": "Chat phòng đang bị tắt."})

    user = current_user()
    room = get_room(room_id)
    if not room:
        return polling_stop_response("room_not_found")
    if user["id"] not in [room["host_user_id"], room["guest_user_id"]] and not is_admin_user(user):
        return polling_stop_response("room_access_ended")

    payload = request.get_json(silent=True) or request.form
    message = payload.get("message", "")
    ok, error = create_chat_message(user["id"], message, scope="room", room_id=room_id)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True})


@app.route("/admin/announcement", methods=["POST"])
@login_required
@admin_required
@admin_permission_required("announcements_manage")
def admin_create_announcement():
    user = current_user()
    title = request.form.get("title", "THÔNG BÁO").strip() or "THÔNG BÁO"
    message = request.form.get("message", "").strip()

    if not message:
        flash("Nội dung thông báo không được để trống.", "danger")
        return redirect_admin("system")

    created = create_admin_announcement(
        title=title[:40],
        message=message[:220],
        admin_user_id=user.get("id"),
    )
    announcement_id = created.data[0].get("id") if created.data else None
    log_admin_action("Đăng thông báo", "announcement", announcement_id, title[:40], message[:220])

    flash("Đã đăng thông báo admin.", "success")
    return redirect_admin("system")


@app.route("/admin/announcement/clear", methods=["POST"])
@login_required
@admin_required
@admin_permission_required("announcements_manage")
def admin_clear_announcement():
    db.table("admin_announcements").update({"is_active": False}).eq("is_active", True).execute()
    log_admin_action("Tắt thông báo", "announcement", details="Đã tắt toàn bộ thông báo đang hoạt động.")
    flash("Đã tắt thông báo admin.", "success")
    return redirect_admin("system")


@app.route("/api/announcement/current")
@login_required
def api_current_announcement():
    events = get_active_global_streak_events()
    if events:
        announcements = []
        for event in events:
            kind = str(event.get("kind") or "milestone")
            announcements.append({
                "id": f"streak:{event.get('id', 'event')}",
                "title": event.get("title") or "DANH HIỆU CHUỖI THẮNG",
                "message": event.get("subtitle") or "Một danh hiệu mới vừa được thiết lập!",
                "created_at": event.get("published_at"),
                "expires_at": event.get("expires_at"),
                "announcement_type": "shutdown" if kind == "shutdown" else "win_streak",
                "icon": "⚡" if kind == "shutdown" else "🏆",
            })
        return jsonify({"ok": True, "announcements": announcements, "announcement": announcements[0]})

    announcement = get_active_announcement()
    if not announcement:
        return jsonify({"ok": True, "announcements": [], "announcement": None})
    admin_item = {
        "id": announcement["id"],
        "title": announcement["title"],
        "message": announcement["message"],
        "created_at": announcement["created_at"],
        "announcement_type": "admin",
        "icon": "📢",
    }
    return jsonify({"ok": True, "announcements": [admin_item], "announcement": admin_item})


@app.route("/api/chat/global/send", methods=["POST"])
@login_required
def api_send_global_chat():
    if not system_feature_enabled("lobby_chat_enabled"):
        return jsonify({"ok": False, "disabled": True, "error": "Chat Sảnh đang bị tắt."})
    user = current_user()
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")

    ok, error = create_chat_message(user["id"], message, scope="global")
    if not ok:
        return jsonify({"ok": False, "error": error}), 400

    return jsonify({"ok": True})


@app.route("/api/admin/announcement/send", methods=["POST"])
@login_required
@admin_required
@admin_permission_required("announcements_manage")
def api_admin_send_announcement():
    if not system_feature_enabled("announcements_enabled"):
        return jsonify({"ok": False, "error": "Thông báo hệ thống đang bị tắt."}), 403
    user = current_user()
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "THÔNG BÁO").strip()[:40] or "THÔNG BÁO"
    message = (payload.get("message") or "").strip()[:220]

    if not message:
        return jsonify({"ok": False, "error": "Nội dung thông báo không được để trống."}), 400

    created = create_admin_announcement(
        title=title,
        message=message,
        admin_user_id=user.get("id"),
    )
    announcement_id = created.data[0].get("id") if created.data else None
    log_admin_action("Đăng thông báo", "announcement", announcement_id, title, message)

    return jsonify({"ok": True})


@app.route("/api/online-count")
@login_required
def api_online_count():
    players = list_players(include_admin=True)
    online_count = sum(1 for player in players if player.get("is_online"))
    return jsonify({"ok": True, "online_count": online_count})


# =========================
# Hướng dẫn người chơi
# =========================
@app.route("/huong-dan")
@login_required
def guide():
    return render_template("guide.html")


# =========================
# Dashboard / players
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    if not dashboard_is_enabled(get_system_features()):
        return redirect(url_for("ranking"))
    user = current_user()
    try:
        player_rows = list_players()
        presence_rows = list_players(include_admin=True)
        matches = list_matches()
        rooms = list_rooms()
        invite_count = current_pending_invite_count()
    except Exception:
        player_rows, presence_rows, matches, rooms = [], [], [], []
        invite_count = 0
        flash("Dữ liệu đang tải chậm, vui lòng thử lại sau vài giây.", "warning")

    me = next((player for player in player_rows if player.get("id") == user.get("id")), dict(user))
    my_position = next((index for index, player in enumerate(player_rows, 1) if player.get("id") == user.get("id")), None)
    my_rank_info = get_player_rank_info(me, my_position)
    total = calculated_total_matches(me)
    wins = int(me.get("wins", 0) or 0)
    me["winrate"] = round((wins / total) * 100, 1) if total else 0

    my_matches = [
        decorate_match_for_view(match, user.get("id"))
        for match in matches
        if user.get("id") in {match.get("player1_id"), match.get("player2_id")}
    ]
    recent_matches = my_matches[:5]

    active_room = active_room_for_user(user.get("id"))
    attention = {
        "invites": invite_count,
        "has_room": bool(active_room),
        "waiting_confirm": len([m for m in matches if m.get("status") == "waiting_confirm" and user.get("id") in {m.get("player1_id"), m.get("player2_id")}]),
        "disputed": len([m for m in matches if m.get("status") == "disputed" and user.get("id") in {m.get("player1_id"), m.get("player2_id")}]),
    }

    activity_map = build_player_activity_map(rooms, matches)
    online_players = [p for p in presence_rows if p.get("is_online") and p.get("id") != user.get("id")]
    solo_room_user_ids = {
        str(room.get("host_user_id"))
        for room in rooms
        if is_solo_waiting_room(room, room.get("host_user_id"))
    }
    for player in online_players:
        status = activity_map.get(player.get("id"), {"code": "ready", "label": "Sẵn sàng"})
        player["activity_code"] = status["code"]
        player["activity_label"] = status["label"]
        player["can_receive_invite"] = bool(
            status["code"] == "ready" or str(player.get("id")) in solo_room_user_ids
        )
        player["is_busy"] = not player["can_receive_invite"]

    online_players.sort(key=lambda p: (p.get("is_busy", False), _player_ranking_sort_key(p)))

    return render_template(
        "dashboard.html",
        me=me,
        my_position=my_position,
        my_rank_info=my_rank_info,
        attention=attention,
        online_players=online_players,
        recent_matches=recent_matches,
    )


@app.route("/rooms/create", methods=["POST"])
@login_required
def create_open_room():
    user = current_user()
    limit_message = daily_rank_block_message(user.get("id"))
    if limit_message:
        flash(limit_message, "warning")
        return redirect(url_for("dashboard"))
    cleanup_duplicate_waiting_rooms(user["id"])
    existing = active_room_for_user(user["id"])
    if existing:
        return redirect(url_for("room_detail", room_id=existing["id"]))
    if active_match_for_user(user["id"]):
        flash("Bạn đang có trận chưa hoàn tất.", "warning")
        return redirect(url_for("dashboard"))

    room = execute_query(
        db.table("match_rooms").insert({
            "invite_id": None,
            "host_user_id": user["id"],
            "guest_user_id": None,
            "team_tier": (SMART_RANDOM_MODE if system_feature_enabled("rank_standard_enabled") else FRIENDLY_RANDOM3_MODE),
            "match_mode": MATCH_MODE_RANKED,
            "friendly_tier": "A",
            "status": "waiting_ready",
            "guest_ready": False,
            "note": "Phòng mở đang chờ chủ phòng mời đối thủ.",
            "state_expires_at": None,
            "updated_at": now_iso(),
        }),
        "create_open_room",
    ).data[0]
    # Chống double-click / hai request chạy đồng thời trên nhiều Vercel instance.
    cleanup_duplicate_waiting_rooms(user["id"])
    canonical_room = active_room_for_user(user["id"])
    if canonical_room:
        room = canonical_room
    flash("Đã tạo phòng đấu. Bạn có thể mời đối thủ từ danh sách Players.", "success")
    return redirect(url_for("room_detail", room_id=room["id"]))


@app.route("/players")
@login_required
def players():
    player_rows = list_players(include_admin=True)
    rooms = list_rooms()
    activity_map = build_player_activity_map(rooms=rooms)
    solo_room_user_ids = {
        str(room.get("host_user_id"))
        for room in rooms
        if is_solo_waiting_room(room, room.get("host_user_id"))
    }
    viewer = current_user()
    viewer_room = active_room_for_user(viewer.get("id")) if viewer else None
    viewer_can_invite = bool(
        viewer
        and not active_match_for_user(viewer.get("id"))
        and (not viewer_room or is_solo_waiting_room(viewer_room, viewer.get("id")))
    )
    query = (request.args.get("q") or "").strip().casefold()
    status_filter = (request.args.get("status") or "all").strip()

    for player in player_rows:
        if not player.get("is_online"):
            status = {"code": "offline", "label": "Offline"}
        else:
            status = activity_map.get(player.get("id"), {"code": "ready", "label": "Sẵn sàng"})
        player["activity_code"] = status["code"]
        player["activity_label"] = status["label"]
        player["is_busy"] = status["code"] not in {"ready", "offline"}
        player["can_receive_invite"] = bool(
            player.get("is_online")
            and (status["code"] == "ready" or str(player.get("id")) in solo_room_user_ids)
        )
        total = calculated_total_matches(player)
        player["winrate"] = round((int(player.get("wins", 0) or 0) / total) * 100, 1) if total else 0
        player["last_seen_display"] = format_vn_datetime(player.get("last_seen_at"))

    if query:
        player_rows = [
            player for player in player_rows
            if query in str(player.get("display_name") or "").casefold()
            or query in str(player.get("username") or "").casefold()
        ]
    if status_filter != "all":
        player_rows = [player for player in player_rows if player.get("activity_code") == status_filter]

    status_order = {"ready": 0, "in_room": 1, "waiting_confirm": 2, "playing": 3, "offline": 4}
    player_rows.sort(key=lambda p: (status_order.get(p.get("activity_code"), 9), _player_ranking_sort_key(p)))
    return render_template(
        "players.html",
        players=player_rows,
        q=request.args.get("q", ""),
        status_filter=status_filter,
        viewer_can_invite=viewer_can_invite,
    )


def _build_recent_form_map(matches, player_ids=None, limit=5):
    """Build recent form pills for leaderboard rows using confirmed matches only."""
    tracked_ids = set(player_ids or []) if player_ids else None
    recent_map = {}

    for match in matches or []:
        if match.get("status") != "confirmed":
            continue

        player1_id = match.get("player1_id")
        player2_id = match.get("player2_id")
        score1 = match.get("score1")
        score2 = match.get("score2")
        if not player1_id or not player2_id or score1 is None or score2 is None:
            continue

        for player_id, my_score, opponent_score in (
            (player1_id, score1, score2),
            (player2_id, score2, score1),
        ):
            if tracked_ids is not None and player_id not in tracked_ids:
                continue

            bucket = recent_map.setdefault(player_id, [])
            if len(bucket) >= limit:
                continue

            if my_score > opponent_score:
                bucket.append({"code": "win", "short": "T", "label": "Thắng"})
            elif my_score < opponent_score:
                bucket.append({"code": "loss", "short": "B", "label": "Bại"})
            else:
                bucket.append({"code": "draw", "short": "H", "label": "Hòa"})

    return recent_map


@app.route("/ranking")
@app.route("/bxh")
def ranking():
    user = current_user()

    # Admin có thể khóa BXH công khai. Người đã đăng nhập vẫn được xem BXH,
    # chỉ khách chưa đăng nhập mới được chuyển về trang đăng nhập.
    if not user and not system_feature_enabled("public_ranking_enabled"):
        flash("Bảng xếp hạng công khai đang được Admin tạm khóa. Vui lòng đăng nhập để tiếp tục.", "warning")
        return redirect(url_for("login"))

    try:
        player_rows = list_players()
    except Exception as exc:
        # BXH là trang công khai; nếu Supabase chập chờn thì vẫn trả trang thay vì HTTP 500.
        print(f"ranking list_players warning: {exc}")
        player_rows = []

    query = (request.args.get("q") or "").strip().casefold()
    rank_filter = (request.args.get("rank") or "all").strip()

    current_player = None
    current_position = None
    if user:
        current_player = next((player for player in player_rows if player.get("id") == user.get("id")), None)
        current_position = current_player.get("position") if current_player else None

    filtered = player_rows
    if query:
        filtered = [
            player for player in filtered
            if query in str(player.get("display_name") or "").casefold()
            or query in str(player.get("username") or "").casefold()
        ]
    if rank_filter != "all":
        filtered = [player for player in filtered if player.get("rank_info", {}).get("slug") == rank_filter]

    top_players = filtered[:100]
    try:
        confirmed_matches = list_matches(status="confirmed")
    except Exception as exc:
        print(f"ranking list_matches warning: {exc}")
        confirmed_matches = []

    recent_form_map = _build_recent_form_map(
        confirmed_matches,
        player_ids={player.get("id") for player in top_players},
        limit=5,
    )

    for player in top_players:
        total_matches = calculated_total_matches(player)
        wins = int(player.get("wins") or 0)
        draws = int(player.get("draws") or 0)
        losses = int(player.get("losses") or 0)
        player["winrate"] = round((wins / total_matches) * 100, 1) if total_matches else 0
        player["record_text"] = f"{wins}T • {draws}H • {losses}B"
        player["recent_form"] = recent_form_map.get(player.get("id"), [])

    template_name = "ranking.html" if user else "public_ranking.html"
    return render_template(
        template_name,
        players=filtered,
        current_player=current_player,
        current_position=current_position,
        q=request.args.get("q", ""),
        rank_filter=rank_filter,
    )


# Hồ sơ cá nhân đã tách sang modules/profile.


# =========================
# Invites
# =========================
@app.route("/invites")
@login_required
def invites():
    user = current_user()

    # Nếu người chơi đã có phòng active, không để mắc kẹt ở trang mời đấu.
    try:
        active_room = active_room_for_user(user["id"])
        if active_room:
            return redirect(url_for("room_detail", room_id=active_room["id"]))
    except Exception:
        flash("Đang kiểm tra phòng hiện tại hơi chậm, vui lòng thử lại sau vài giây.", "warning")

    all_players = list_players()
    available_players = [
        player for player in all_players
        if player["id"] != user["id"] and player.get("is_online")
    ]

    all_invites = list_invites()
    received = [i for i in all_invites if i["to_user_id"] == user["id"] and i["status"] == "pending"]
    sent = [i for i in all_invites if i["from_user_id"] == user["id"] and i["status"] == "pending"]
    history = [i for i in all_invites if i["from_user_id"] == user["id"] or i["to_user_id"] == user["id"]][:20]

    return render_template(
        "invites.html",
        players=available_players,
        received=received,
        sent=sent,
        history=history,
    )


@app.route("/invites/send", methods=["POST"])
@login_required
def send_invite():
    user = current_user()

    to_user_id = request.form.get("to_user_id")
    tier = SMART_RANDOM_MODE

    if not to_user_id or to_user_id == user["id"]:
        flash("Đối thủ không hợp lệ.", "danger")
        return redirect(url_for("players"))

    opponent = get_user(to_user_id)
    if not opponent:
        flash("Không tìm thấy đối thủ.", "danger")
        return redirect(url_for("players"))

    try:
        state = matchmaking_snapshot(user["id"], to_user_id)
    except Exception as exc:
        print(f"send_invite state ERROR from={user.get('id')} to={to_user_id}: {type(exc).__name__}: {exc}")
        flash("Không thể kiểm tra trạng thái phòng lúc này. Vui lòng thử lại sau vài giây.", "danger")
        return redirect(url_for("players"))

    sender_room = state.get("room_a")
    receiver_room = state.get("room_b")
    if state.get("match_a"):
        flash("Bạn đang có trận chưa hoàn tất nên chưa thể gửi lời mời.", "warning")
        return redirect(url_for("dashboard"))
    if sender_room and not is_solo_waiting_room(sender_room, user["id"]):
        flash("Phòng của bạn đã có đủ 2 người hoặc đã bắt đầu. Bạn không thể gửi thêm lời mời.", "warning")
        return redirect(url_for("dashboard"))
    if state.get("match_b"):
        flash("Người chơi này đang thi đấu hoặc còn trận chưa hoàn tất.", "warning")
        return redirect(url_for("players"))
    if receiver_room and not is_solo_waiting_room(receiver_room, to_user_id):
        flash("Phòng của người chơi này đã có đủ 2 người hoặc đã bắt đầu.", "warning")
        return redirect(url_for("players"))

    if not is_user_online_now(opponent):
        flash("Người chơi này vừa offline. Bạn hãy chọn một đối thủ đang online khác nhé.", "danger")
        return redirect(url_for("players"))

    if state.get("pair_pending"):
        flash("Hai người đang có lời mời chờ xử lý.", "warning")
        return redirect(url_for("players"))

    invite_result = execute_query(
        db.table("match_invites").insert({
            "from_user_id": user["id"],
            "to_user_id": to_user_id,
            "tier": tier,
            "status": "pending",
            "message": f'{user["display_name"]} mời {opponent["display_name"]} thi đấu hạng.',
            "expires_at": future_iso(INVITE_TIMEOUT_SECONDS),
            "updated_at": now_iso(),
        }),
        "send_match_invite",
    )
    invite = invite_result.data[0] if invite_result.data else None
    ttl_cache_delete("invites_raw")
    cache_delete("_rz_invites_all")
    cache_delete("_rz_current_pending_invites")
    if not invite:
        flash("Không thể gửi lời mời lúc này. Vui lòng thử lại.", "danger")
        return redirect(url_for("players"))

    # Chủ phòng phải được đưa vào phòng ngay sau khi bấm Mời đấu.
    # Nếu đã có phòng trống thì gắn lời mời vào phòng đó; nếu chưa có thì tạo phòng mới.
    if sender_room:
        room_result = execute_query(
            db.table("match_rooms").update({
                "invite_id": invite["id"],
                "note": f'Đã mời {opponent["display_name"]}. Đang chờ đối thủ chấp nhận.',
                "updated_at": now_iso(),
            }).eq("id", sender_room["id"]).eq("status", "waiting_ready"),
            "attach_invite_to_open_room",
        )
        room = room_result.data[0] if room_result.data else sender_room
    else:
        room_result = execute_query(
            db.table("match_rooms").insert({
                "invite_id": invite["id"],
                "host_user_id": user["id"],
                "guest_user_id": None,
                "team_tier": (SMART_RANDOM_MODE if system_feature_enabled("rank_standard_enabled") else FRIENDLY_RANDOM3_MODE),
                "match_mode": MATCH_MODE_RANKED,
                "friendly_tier": "A",
                "status": "waiting_ready",
                "guest_ready": False,
                "note": f'Đã mời {opponent["display_name"]}. Đang chờ đối thủ chấp nhận.',
                "state_expires_at": None,
                "updated_at": now_iso(),
            }),
            "create_room_for_invite",
        )
        room = room_result.data[0] if room_result.data else None

    if not room:
        # Tránh để lại lời mời treo nếu tạo phòng thất bại.
        execute_query(
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": now_iso(),
            }).eq("id", invite["id"]).eq("status", "pending"),
            "cancel_invite_after_room_error",
        )
        flash("Đã gửi lời mời nhưng không thể tạo phòng. Vui lòng thử lại.", "danger")
        return redirect(url_for("players"))

    flash(f'Đã mời {opponent["display_name"]}. Bạn đang ở trong phòng và chờ đối thủ chấp nhận.', "success")
    return redirect(url_for("room_detail", room_id=room["id"]))


def is_quick_match_invite(invite):
    """Return True only for invites generated by the Quick Match flow."""
    message = str((invite or {}).get("message") or "")
    return message.startswith("QUICK_MATCH|")


@app.route("/invites/quick-match", methods=["POST"])
@login_required
def quick_match_invite():
    user = current_user()
    payload = request.get_json(silent=True) or request.form or {}
    raw_excluded = payload.get("excluded_user_ids", [])
    if isinstance(raw_excluded, str):
        raw_excluded = [item for item in raw_excluded.split(",") if item]
    excluded_user_ids = {str(item).strip() for item in (raw_excluded or []) if str(item).strip()}
    if not system_feature_enabled("quick_match_enabled"):
        return jsonify({"ok": False, "message": "Tính năng Tìm Nhanh đang được Admin tắt."}), 403
    try:
        state = matchmaking_snapshot(user["id"])
    except Exception as exc:
        print(f"quick_match state ERROR user={user.get('id')}: {type(exc).__name__}: {exc}")
        return jsonify({"ok": False, "message": "Không thể kiểm tra trạng thái phòng lúc này."}), 503

    sender_room = state.get("room_a")
    if state.get("match_a") or not is_solo_waiting_room(sender_room, user["id"]):
        return jsonify({"ok": False, "message": "Tìm Nhanh chỉ dùng khi bạn đang ở phòng một mình."}), 409

    rooms = state.get("rooms") or []
    matches = state.get("matches") or []
    invites = state.get("invites") or []
    room_by_user = {}
    for room in rooms:
        for uid in (room.get("host_user_id"), room.get("guest_user_id")):
            if uid:
                room_by_user[str(uid)] = room
    busy_match_ids = {str(uid) for m in matches for uid in (m.get("player1_id"), m.get("player2_id")) if uid}
    # Chỉ người đang CHỦ ĐỘNG gửi một lời mời khác mới được xem là bận.
    # Người chỉ đang NHẬN một hay nhiều lời mời vẫn có thể tiếp tục nhận thêm
    # lời mời thủ công hoặc Tìm Nhanh. Khi họ chấp nhận một lời mời, luồng
    # respond_invite sẽ tự hủy các lời mời chờ còn lại để tránh vào hai phòng.
    outgoing_inviter_ids = {
        str(i.get("from_user_id"))
        for i in invites
        if i.get("from_user_id")
    }
    if str(user["id"]) in outgoing_inviter_ids:
        return jsonify({"ok": False, "message": "Bạn đang chờ một đối thủ phản hồi lời mời đã gửi."}), 409

    my_points = int(user.get("rank_points", 0) or 0)
    my_rank_level = get_rank_level(my_points)
    candidates = []
    online_total = 0
    busy_total = 0
    cooldown_total = 0

    # Tìm Nhanh phải đọc presence trực tiếp từ Supabase thay vì dùng danh sách
    # người chơi đã cache. Cache ngắn vẫn có thể làm một tài khoản vừa heartbeat
    # bị coi là offline trên instance Vercel khác. last_seen_at là nguồn xác thực
    # chính; is_online chỉ là cờ hiển thị nhanh.
    try:
        online_result = execute_query(
            db.table("users")
            .select("id,username,display_name,role,admin_level,account_status,rank_points,is_online,last_seen_at,matchmaking_cooldown_until"),
            "quick_match_live_players",
            attempts=3,
        )
        quick_players = [dict(row) for row in (online_result.data or [])]
    except Exception as exc:
        print(f"quick_match players ERROR user={user.get('id')}: {type(exc).__name__}: {exc}")
        return jsonify({"ok": False, "message": "Không thể đọc danh sách người chơi online lúc này."}), 503

    presence_cutoff = now_dt() - timedelta(seconds=max(ONLINE_TIMEOUT_SECONDS, 90))
    for opponent in quick_players:
        oid = str(opponent.get("id") or "")
        if not oid or oid == str(user["id"]) or oid in excluded_user_ids:
            continue
        role = str(opponent.get("role") or "").strip().lower()
        admin_level = str(opponent.get("admin_level") or "").strip().lower()
        if role not in {"player", "admin"} and admin_level not in {"owner", "admin"}:
            continue
        if opponent.get("account_status", "approved") != "approved":
            continue
        seen = parse_dt(opponent.get("last_seen_at"))
        # Admin chọn Offline vẫn có last_seen_at mới vì thao tác đổi trạng thái
        # và heartbeat ẩn tiếp tục cập nhật thời gian. Vì vậy Tìm Nhanh phải
        # kiểm tra đồng thời cờ is_online; chỉ dựa last_seen_at sẽ gửi lời mời
        # giả tới Admin đang ẩn trạng thái, trong khi phía nhận không nhận popup.
        if opponent.get("is_online") is not True or not seen or seen < presence_cutoff:
            continue
        online_total += 1
        # Có lời mời ĐẾN không làm người chơi bị loại khỏi danh sách.
        # Chỉ loại khi chính họ đang có lời mời ĐI chờ phản hồi.
        if oid in busy_match_ids or oid in outgoing_inviter_ids:
            busy_total += 1
            continue
        opponent_room = room_by_user.get(oid)
        if opponent_room and not is_solo_waiting_room(opponent_room, oid):
            busy_total += 1
            continue
        opponent_points = int(opponent.get("rank_points", 0) or 0)
        gap = abs(opponent_points - my_points)
        same_rank = get_rank_level(opponent_points) == my_rank_level

        # Thứ tự ưu tiên Tìm Nhanh:
        # 0. Cùng bậc Rank (luôn ưu tiên trước)
        # 1. Khác Rank, chênh tối đa 300 RP
        # 2. Khác Rank, chênh 301-500 RP
        # 3. Khác Rank, chênh 501-1.000 RP
        # 4. Khác Rank, chênh 1.001-2.000 RP
        # Người khác Rank chênh quá 2.000 RP không được chọn.
        priority_group = quick_match_priority_group(same_rank=same_rank, points_gap=gap)
        if priority_group is None:
            continue

        # Trong cùng nhóm: ưu tiên RP gần nhất, sau đó người hoạt động
        # gần đây hơn, cuối cùng mới dùng tên để kết quả ổn định.
        sort_key = build_candidate_sort_key(
            priority_group=priority_group,
            points_gap=gap,
            last_seen=seen,
            display_name=opponent.get("display_name") or opponent.get("username") or "",
        )
        candidates.append((*sort_key, opponent))

    if not candidates:
        if online_total == 0:
            message = "Hiện không có người chơi nào khác đang online."
        elif busy_total:
            message = "Người chơi đang online hiện đều bận, phòng đã đủ người hoặc đang chờ đối thủ phản hồi lời mời đã gửi."
        elif cooldown_total:
            message = "Người chơi đang online hiện đều trong thời gian chờ ghép trận."
        else:
            message = "Hiện chưa có đối thủ phù hợp đang online."
        return jsonify({"ok": False, "message": message}), 404

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    opponent = candidates[0][4]
    invite_result = execute_query(
        db.table("match_invites").insert({
            "from_user_id": user["id"], "to_user_id": opponent["id"],
            "tier": SMART_RANDOM_MODE, "status": "pending",
            "message": f'QUICK_MATCH|{user["display_name"]} tìm nhanh và mời {opponent["display_name"]} thi đấu hạng.',
            "expires_at": future_iso(INVITE_TIMEOUT_SECONDS), "updated_at": now_iso(),
        }), "quick_match_create_invite",
    )
    invite = invite_result.data[0] if invite_result.data else None
    if not invite:
        return jsonify({"ok": False, "message": "Không thể gửi lời mời lúc này."}), 500
    attach_result = execute_query(
        db.table("match_rooms").update({
            "invite_id": invite["id"],
            "note": "Đã tìm thấy đối thủ phù hợp. Đang chờ phản hồi.",
            "updated_at": now_iso(),
        }).eq("id", sender_room["id"]).eq("status", "waiting_ready").is_("guest_user_id", "null"),
        "quick_match_attach_invite",
    )
    if not attach_result.data:
        execute_query(
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": now_iso(),
            }).eq("id", invite["id"]).eq("status", "pending"),
            "quick_match_cancel_unattached_invite",
            attempts=2,
        )
        ttl_cache_delete("invites_raw")
        cache_delete("_rz_invites_all")
        return jsonify({
            "ok": False,
            "message": "Phòng vừa thay đổi nên lời mời chưa được gửi. Vui lòng bấm Tìm Nhanh lại.",
        }), 409
    ttl_cache_delete("invites_raw")
    cache_delete("_rz_invites_all")
    return jsonify({
        "ok": True,
        "invite_id": invite.get("id"),
        "opponent_id": opponent.get("id"),
        "message": "Đã tìm thấy đối thủ. Đang chờ phản hồi...",
    })


@app.route("/api/invites/quick-match/<invite_id>/status")
@login_required
def quick_match_invite_status(invite_id):
    """Return the live state of a Quick Match invitation.

    This endpoint also reconciles stale pending rows. A Quick Match chain must
    stop when the sender's room is already filled, and must move on when the
    selected opponent goes offline or becomes unavailable.
    """
    user = current_user()
    invite = get_invite(invite_id)
    if not invite or str(invite.get("from_user_id")) != str(user.get("id")) or not is_quick_match_invite(invite):
        return jsonify({
            "ok": False,
            "status": "missing",
            "continue_search": False,
            "message": "Không tìm thấy lượt Tìm Nhanh.",
        }), 404

    status = str(invite.get("status") or "")
    continue_search = status in {"rejected", "expired", "cancelled"}
    reason = status

    if status == "pending":
        sender_id = invite.get("from_user_id")
        opponent_id = invite.get("to_user_id")
        now = now_dt()

        room_result = execute_query(
            db.table("match_rooms")
              .select("id,host_user_id,guest_user_id,status,invite_id")
              .eq("host_user_id", sender_id)
              .in_("status", ["waiting_ready", "playing", "friendly_playing", "waiting_result_confirm"])
              .order("updated_at", desc=True)
              .limit(1),
            "quick_match_status_sender_room",
            attempts=2,
        )
        sender_room = dict(room_result.data[0]) if room_result.data else None

        # A different player has already entered the sender's room. The search
        # is complete and must not continue to invite more people.
        if sender_room and sender_room.get("guest_user_id"):
            guest_id = str(sender_room.get("guest_user_id"))
            next_status = "accepted" if guest_id == str(opponent_id) else "cancelled"
            execute_query(
                db.table("match_invites").update({
                    "status": next_status,
                    "updated_at": now_iso(),
                }).eq("id", invite_id).eq("status", "pending"),
                "quick_match_close_when_room_filled",
                attempts=2,
            )
            status = "room_filled"
            reason = "room_filled"
            continue_search = False
        elif not sender_room or not is_solo_waiting_room(sender_room, sender_id):
            execute_query(
                db.table("match_invites").update({
                    "status": "cancelled",
                    "updated_at": now_iso(),
                }).eq("id", invite_id).eq("status", "pending"),
                "quick_match_cancel_sender_unavailable",
                attempts=2,
            )
            status = "sender_unavailable"
            reason = "sender_unavailable"
            continue_search = False
        else:
            opponent_result = execute_query(
                db.table("users")
                  .select("id,is_online,last_seen_at,role,admin_level,account_status")
                  .eq("id", opponent_id)
                  .limit(1),
                "quick_match_status_opponent_presence",
                attempts=2,
            )
            opponent = dict(opponent_result.data[0]) if opponent_result.data else None
            seen = parse_dt((opponent or {}).get("last_seen_at"))
            presence_cutoff = now - timedelta(seconds=max(ONLINE_TIMEOUT_SECONDS, 90))
            opponent_online = bool(
                opponent
                and (opponent.get("account_status", "approved") == "approved")
                and seen
                and seen >= presence_cutoff
                and opponent.get("is_online") is not False
            )

            if not opponent_online:
                execute_query(
                    db.table("match_invites").update({
                        "status": "cancelled",
                        "updated_at": now_iso(),
                    }).eq("id", invite_id).eq("status", "pending"),
                    "quick_match_cancel_offline_opponent",
                    attempts=2,
                )
                status = "opponent_offline"
                reason = "opponent_offline"
                continue_search = True
            else:
                availability = matchmaking_snapshot(opponent_id)
                opponent_room = availability.get("room_a")
                opponent_busy = bool(
                    availability.get("match_a")
                    or (opponent_room and not is_solo_waiting_room(opponent_room, opponent_id))
                )
                other_pending = any(
                    str(row.get("id")) != str(invite_id)
                    and str(opponent_id) in {str(row.get("from_user_id")), str(row.get("to_user_id"))}
                    for row in (availability.get("invites") or [])
                )
                if opponent_busy or other_pending:
                    execute_query(
                        db.table("match_invites").update({
                            "status": "cancelled",
                            "updated_at": now_iso(),
                        }).eq("id", invite_id).eq("status", "pending"),
                        "quick_match_cancel_unavailable_opponent",
                        attempts=2,
                    )
                    status = "opponent_unavailable"
                    reason = "opponent_unavailable"
                    continue_search = True

    if status != "pending":
        ttl_cache_delete("invites_raw")
        cache_delete("_rz_invites_all")
        cache_delete("_rz_current_pending_invites")

    return jsonify({
        "ok": True,
        "status": status,
        "reason": reason,
        "continue_search": bool(continue_search),
        "opponent_id": invite.get("to_user_id"),
        "expires_in_seconds": int(invite.get("expires_in_seconds") or 0),
    })


@app.route("/invites/respond/<invite_id>", methods=["POST"])
@login_required
def respond_invite(invite_id):
    user = current_user()
    action = request.form.get("action")
    invite = get_invite(invite_id)

    if not invite:
        flash("Không tìm thấy lời mời.", "danger")
        return redirect(url_for("invites"))

    if invite["to_user_id"] != user["id"]:
        flash("Bạn không có quyền xử lý lời mời này.", "danger")
        return redirect(url_for("invites"))

    if invite["status"] == "expired":
        flash("Lời mời đã hết hạn sau 60 giây. Hãy nhờ đối thủ gửi lời mời mới.", "warning")
        return redirect(url_for("dashboard"))

    if invite["status"] != "pending":
        flash("Lời mời này đã được xử lý.", "warning")
        return redirect(url_for("invites"))

    if action == "reject":
        db.table("match_invites").update({"status": "rejected", "updated_at": now_iso()}).eq("id", invite_id).execute()
        ttl_cache_delete("invites_raw")
        cache_delete("_rz_invites_all")
        if is_quick_match_invite(invite):
            flash("Đã từ chối lời mời Tìm Nhanh.", "success")
        else:
            flash("Đã từ chối lời mời.", "success")
        return redirect(url_for("invites"))

    if action != "accept":
        flash("Hành động không hợp lệ.", "danger")
        return redirect(url_for("invites"))

    receiver_match = active_match_for_user(user["id"])
    receiver_room = active_room_for_user(user["id"])
    if receiver_match:
        flash("Bạn đang có trận chưa hoàn tất nên không thể nhận lời mời.", "warning")
        return redirect(url_for("dashboard"))
    if receiver_room and not is_solo_waiting_room(receiver_room, user["id"]):
        flash("Phòng của bạn đã có đủ 2 người hoặc đã bắt đầu nên không thể nhận lời mời khác.", "warning")
        return redirect(url_for("dashboard"))

    inviter_id = invite.get("from_user_id")
    inviter_room = active_room_for_user(inviter_id)
    if active_match_for_user(inviter_id) or (inviter_room and not is_solo_waiting_room(inviter_room, inviter_id)):
        execute_query(
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": now_iso(),
            }).eq("id", invite_id),
            "cancel_stale_invite_busy_sender",
        )
        flash("Người mời đang ở phòng hoặc trận khác. Lời mời này đã hết hiệu lực.", "warning")
        return redirect(url_for("dashboard"))

    room = None
    target_room_created = False
    try:
        if inviter_room:
            attach_result = execute_query(
                db.table("match_rooms").update({
                    "invite_id": invite_id,
                    "guest_user_id": invite["to_user_id"],
                    "guest_ready": False,
                    "note": "Đối thủ đã vào phòng. Khách chưa sẵn sàng.",
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                })
                .eq("id", inviter_room["id"])
                .eq("status", "waiting_ready")
                .is_("guest_user_id", "null"),
                "attach_guest_to_open_room",
            )
            room = attach_result.data[0] if attach_result.data else None
        else:
            create_result = execute_query(
                db.table("match_rooms").insert({
                    "invite_id": invite_id,
                    "host_user_id": invite["from_user_id"],
                    "guest_user_id": invite["to_user_id"],
                    "team_tier": (SMART_RANDOM_MODE if system_feature_enabled("rank_standard_enabled") else FRIENDLY_RANDOM3_MODE),
                    "match_mode": MATCH_MODE_RANKED,
                    "friendly_tier": "A",
                    "status": "waiting_ready",
                    "guest_ready": False,
                    "note": "Đối thủ đã vào phòng. Khách chưa sẵn sàng.",
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                }),
                "create_room_when_accepting_invite",
            )
            room = create_result.data[0] if create_result.data else None
            target_room_created = bool(room)

        if not room:
            flash("Phòng của người mời vừa có người khác tham gia. Lời mời không còn hiệu lực.", "warning")
            return redirect(url_for("dashboard"))

        # Người nhận có thể đang làm chủ một phòng trống. Khi nhận lời, đóng phòng cũ
        # trước khi hoàn tất lời mời để mỗi tài khoản chỉ còn đúng một phòng active.
        if receiver_room and str(receiver_room.get("id")) != str(room.get("id")):
            old_invite_id = receiver_room.get("invite_id")
            delete_result = execute_query(
                db.table("match_rooms").delete()
                .eq("id", receiver_room["id"])
                .eq("host_user_id", user["id"])
                .eq("status", "waiting_ready")
                .is_("guest_user_id", "null"),
                "close_receiver_solo_room_on_accept",
            )
            if not delete_result.data:
                raise RuntimeError("Không thể đóng phòng cũ của người nhận")
            if old_invite_id and str(old_invite_id) != str(invite_id):
                execute_query(
                    db.table("match_invites").update({
                        "status": "cancelled",
                        "updated_at": now_iso(),
                    }).eq("id", old_invite_id).eq("status", "pending"),
                    "cancel_receiver_old_room_invite",
                )

        execute_query(
            db.table("match_invites").update({
                "status": "accepted",
                "updated_at": now_iso(),
            }).eq("id", invite_id).eq("status", "pending"),
            "accept_match_invite",
        )
        # Hủy các lời mời chờ khác của người nhận để popup cũ không xuất hiện lại.
        execute_query(
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": now_iso(),
            }).eq("to_user_id", user["id"]).eq("status", "pending").neq("id", invite_id),
            "cancel_other_incoming_invites_after_accept",
            attempts=1,
        )
        execute_query(
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": now_iso(),
            }).eq("from_user_id", user["id"]).eq("status", "pending").neq("id", invite_id),
            "cancel_receiver_outgoing_invites_after_accept",
            attempts=1,
        )
        # Khi phòng của người mời đã có khách, mọi lời mời khác do người
        # mời gửi (bao gồm Tìm Nhanh) phải kết thúc để không còn trạng thái treo.
        execute_query(
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": now_iso(),
            }).eq("from_user_id", inviter_id).eq("status", "pending").neq("id", invite_id),
            "cancel_inviter_other_outgoing_after_accept",
            attempts=1,
        )
    except Exception as exc:
        print(f"respond_invite accept ERROR invite={invite_id}: {type(exc).__name__}: {exc}")
        # Hoàn tác việc gắn khách nếu đóng phòng cũ thất bại.
        try:
            if room:
                if target_room_created:
                    execute_query(
                        db.table("match_rooms").delete().eq("id", room["id"]),
                        "rollback_created_invite_room",
                        attempts=1,
                    )
                else:
                    execute_query(
                        db.table("match_rooms").update({
                            "guest_user_id": None,
                            "guest_ready": False,
                            "invite_id": invite_id,
                            "note": "Đang chờ đối thủ chấp nhận lời mời.",
                            "updated_at": now_iso(),
                        }).eq("id", room["id"]).eq("guest_user_id", user["id"]),
                        "rollback_attached_invite_guest",
                        attempts=1,
                    )
        except Exception as rollback_exc:
            print(f"respond_invite rollback warning: {rollback_exc}")
        flash("Không thể chuyển phòng an toàn lúc này. Phòng cũ của bạn vẫn được giữ nguyên; vui lòng thử lại.", "danger")
        return redirect(url_for("dashboard"))

    ttl_cache_delete("rooms_raw")
    ttl_cache_delete("invites_raw")
    cache_delete("_rz_rooms_all")
    cache_delete("_rz_invites_all")
    cache_delete("_rz_current_pending_invites")
    flash("Đã nhận lời mời. Phòng cũ một người của bạn đã được đóng và bạn đã vào phòng của đối thủ. Hãy bấm Sẵn sàng khi đã chuẩn bị xong.", "success")
    return redirect(url_for("room_detail", room_id=room["id"]))


@app.route("/invites/cancel/<invite_id>", methods=["POST"])
@login_required
def cancel_invite(invite_id):
    user = current_user()
    invite = get_invite(invite_id)

    if not invite:
        flash("Không tìm thấy lời mời.", "danger")
        return redirect(url_for("invites"))

    if invite["from_user_id"] != user["id"]:
        flash("Bạn không có quyền hủy lời mời này.", "danger")
        return redirect(url_for("invites"))

    if invite["status"] != "pending":
        flash("Lời mời này đã được xử lý.", "warning")
        return redirect(url_for("invites"))

    db.table("match_invites").update({"status": "cancelled", "updated_at": now_iso()}).eq("id", invite_id).execute()
    flash("Đã hủy lời mời.", "success")
    return redirect(url_for("invites"))


# =========================
# Rooms
# =========================


# =========================
# Legacy / history routes
# =========================









# =========================
# Đăng ký module chức năng
# =========================
def redirect_admin(tab="overview"):
    """Điểm điều hướng Admin dùng chung cho mọi module quản trị."""
    return redirect(url_for("admin") + f"#{tab}")


# Nạp dịch vụ theo thứ tự dependency: thông báo -> khóa -> kết quả -> phát lại -> xóa an toàn.
from modules import notification_service as _notification_service
from modules import forfeit_history_service as _forfeit_history_service
from modules import ranking_lock_service as _ranking_lock_service
from modules import weekly_rp_rewards_service as _weekly_rp_rewards_service
from modules import match_result_service as _match_result_service
from modules import ranking_rebuild_service as _ranking_rebuild_service
from modules import data_cleanup_service as _data_cleanup_service
from modules import inactivity_rp_service as _inactivity_rp_service
from modules import daily_rank_limit_service as _daily_rank_limit_service
from modules import repeat_opponent_rp_service as _repeat_opponent_rp_service
from modules import zcoin as _zcoin_module
from modules import daily_checkin as _daily_checkin_module
from modules.parsec_room import service as _parsec_room_service
from modules import gift_codes as _gift_codes_module

for _service_module in (
    _notification_service,
    _forfeit_history_service,
    _ranking_lock_service,
    _daily_rank_limit_service,
    _repeat_opponent_rp_service,
    _weekly_rp_rewards_service,
    _zcoin_module,
    _daily_checkin_module,
    _gift_codes_module,
    _parsec_room_service,
    _match_result_service,
    _ranking_rebuild_service,
    _data_cleanup_service,
    _inactivity_rp_service,
):
    _service_module.configure(globals())
    for _service_name in _service_module.EXPORTED_NAMES:
        globals()[_service_name] = getattr(_service_module, _service_name)


# Route phòng đấu.
from modules.room_access_routes import register_routes as _register_room_access_routes
from modules.room_rematch_routes import register_routes as _register_room_rematch_routes
from modules.room_team_routes import register_routes as _register_room_team_routes
from modules.room_result_routes import register_routes as _register_room_result_routes
from modules.match_history_routes import register_routes as _register_match_history_routes
from modules.zcoin import register_routes as _register_zcoin_routes
from modules.profile import register_routes as _register_profile_routes
from modules.parsec_room import register_routes as _register_parsec_room_routes
from modules.shop import register_routes as _register_shop_routes
from modules.inventory import register_routes as _register_inventory_routes
from modules.admin_shop import register_routes as _register_admin_shop_routes
from modules.daily_checkin import register_routes as _register_daily_checkin_routes
from modules.gift_codes import register_routes as _register_gift_code_routes
from modules.admin_economy import register_routes as _register_admin_economy_routes
from modules.luckybox import register_routes as _register_luckybox_routes

# Route Admin.
from modules.admin_system_routes import register_routes as _register_admin_system_routes
from modules.admin_dashboard_routes import register_routes as _register_admin_dashboard_routes
from modules.admin_account_routes import register_routes as _register_admin_account_routes
from modules.admin_match_routes import register_routes as _register_admin_match_routes
from modules.admin_player_routes import register_routes as _register_admin_player_routes
from modules.admin_data_routes import register_routes as _register_admin_data_routes

for _route_registrar in (
    _register_room_access_routes,
    _register_room_rematch_routes,
    _register_room_team_routes,
    _register_room_result_routes,
    _register_match_history_routes,
    _register_zcoin_routes,
    _register_profile_routes,
    _register_parsec_room_routes,
    _register_shop_routes,
    _register_inventory_routes,
    _register_admin_shop_routes,
    _register_daily_checkin_routes,
    _register_gift_code_routes,
    _register_admin_economy_routes,
    _register_luckybox_routes,
    _register_admin_system_routes,
    _register_admin_dashboard_routes,
    _register_admin_account_routes,
    _register_admin_match_routes,
    _register_admin_player_routes,
    _register_admin_data_routes,
):
    _route_registrar(globals())

del _service_module, _service_name, _route_registrar


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
