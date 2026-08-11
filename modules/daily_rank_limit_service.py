"""Giới hạn số trận Rank và RP dương theo ngày Việt Nam.

Số trận trong ngày được tính ngay từ lúc trận Rank được tạo, không chờ xác nhận
kết quả. Vì vậy các trạng thái playing, waiting_confirm, disputed và confirmed
đều chiếm một lượt. Trận bỏ cuộc hợp lệ cũng chiếm một lượt Rank.
"""
from datetime import datetime, timedelta, timezone

EXPORTED_NAMES = [
    "daily_rank_limits_enabled",
    "set_daily_rank_limits_enabled",
    "current_daily_game_limit",
    "ranked_games_today",
    "positive_rp_today",
    "rank_daily_status",
    "user_reached_daily_rank_limit",
    "daily_rank_block_message",
    "daily_rank_match_rp_status",
    "assert_can_start_ranked_match",
    "apply_daily_positive_rp_cap",
    "reset_user_daily_rank_games",
    "get_user_daily_rank_reset",
]

SETTING_KEY = "rank_daily_limits_config"
WEEKDAY_GAME_LIMIT = 10
WEEKEND_GAME_LIMIT = 15
DAILY_POSITIVE_RP_LIMIT = 150
VN_TZ = timezone(timedelta(hours=7))
COUNTED_MATCH_STATUSES = {"playing", "waiting_confirm", "waiting_result_confirm", "processing_result", "disputed", "confirmed"}


def configure(context):
    globals().update(context)


def _now_vn():
    return datetime.now(VN_TZ)


def current_daily_game_limit(moment=None):
    """Trả về giới hạn trận theo ngày Việt Nam: T2-T6 là 10, T7-CN là 15."""
    current = moment or _now_vn()
    return WEEKEND_GAME_LIMIT if current.weekday() in {5, 6} else WEEKDAY_GAME_LIMIT


def _day_bounds_utc_iso(moment=None):
    current = moment or _now_vn()
    start_vn = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end_vn = start_vn + timedelta(days=1)
    return start_vn.astimezone(timezone.utc).isoformat(), end_vn.astimezone(timezone.utc).isoformat()




def _load_daily_rank_config():
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", SETTING_KEY).limit(1),
            "get_rank_daily_limits_full_config",
            attempts=2,
        )
        row = (result.data or [{}])[0]
        raw = row.get("setting_value")
        return dict(raw) if isinstance(raw, dict) else {}
    except Exception as exc:
        print(f"_load_daily_rank_config warning: {exc}")
        return {}


def get_user_daily_rank_reset(user_id, moment=None):
    """Trả mốc reset lượt của người chơi nếu mốc đó thuộc ngày Việt Nam hiện tại."""
    if not user_id:
        return None
    config = _load_daily_rank_config()
    resets = config.get("user_game_resets") or {}
    raw = resets.get(str(user_id)) if isinstance(resets, dict) else None
    if not raw:
        return None
    try:
        reset_dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
        current = moment or _now_vn()
        if reset_dt.astimezone(VN_TZ).date() != current.astimezone(VN_TZ).date():
            return None
        return reset_dt.astimezone(timezone.utc)
    except Exception:
        return None


def reset_user_daily_rank_games(user_id, actor_id=None):
    """Đặt mốc đếm mới cho riêng số trận Rank hôm nay, không xóa lịch sử và không reset trần +150 RP."""
    if not user_id:
        raise ValueError("Thiếu người chơi cần reset.")
    config = _load_daily_rank_config()
    config.update({
        "enabled": bool(config.get("enabled", True)),
        "weekday_game_limit": WEEKDAY_GAME_LIMIT,
        "weekend_game_limit": WEEKEND_GAME_LIMIT,
        "daily_positive_rp_limit": DAILY_POSITIVE_RP_LIMIT,
    })
    resets = config.get("user_game_resets")
    if not isinstance(resets, dict):
        resets = {}
    # Chỉ giữ mốc reset của ngày hiện tại để setting không phình theo thời gian.
    today = _now_vn().date()
    cleaned = {}
    for key, value in resets.items():
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.astimezone(VN_TZ).date() == today:
                cleaned[str(key)] = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            continue
    reset_at = now_iso()
    cleaned[str(user_id)] = reset_at
    config["user_game_resets"] = cleaned
    config["updated_by"] = actor_id
    config["updated_at"] = reset_at
    execute_query(
        db.table("system_settings").upsert({
            "setting_key": SETTING_KEY,
            "setting_value": config,
            "updated_at": reset_at,
        }, on_conflict="setting_key"),
        "reset_user_rank_daily_games",
        attempts=2,
    )
    return {"user_id": str(user_id), "reset_at": reset_at, "game_limit": current_daily_game_limit()}


def daily_rank_limits_enabled():
    try:
        result = execute_query(
            db.table("system_settings").select("setting_value")
            .eq("setting_key", SETTING_KEY).limit(1),
            "get_rank_daily_limits_config",
            attempts=2,
        )
        row = (result.data or [{}])[0]
        raw = row.get("setting_value")
        if isinstance(raw, dict):
            return bool(raw.get("enabled", True))
        if isinstance(raw, bool):
            return raw
    except Exception as exc:
        print(f"daily_rank_limits_enabled warning: {exc}")
    return True


def set_daily_rank_limits_enabled(enabled, actor_id=None):
    current = _load_daily_rank_config()
    payload = {
        "enabled": bool(enabled),
        "weekday_game_limit": WEEKDAY_GAME_LIMIT,
        "weekend_game_limit": WEEKEND_GAME_LIMIT,
        "daily_positive_rp_limit": DAILY_POSITIVE_RP_LIMIT,
        "user_game_resets": current.get("user_game_resets") or {},
        "updated_by": actor_id,
        "updated_at": now_iso(),
    }
    execute_query(
        db.table("system_settings").upsert({
            "setting_key": SETTING_KEY,
            "setting_value": payload,
            "updated_at": now_iso(),
        }, on_conflict="setting_key"),
        "set_rank_daily_limits_config",
        attempts=2,
    )
    return payload


def _matches_today(user_id):
    if not user_id:
        return []
    start_iso, end_iso = _day_bounds_utc_iso()
    result = execute_query(
        db.table("matches")
        .select("id,player1_id,player2_id,delta1,delta2,status,note,loser_id,created_at,rp_details")
        .gte("created_at", start_iso)
        .lt("created_at", end_iso)
        .or_(f"player1_id.eq.{user_id},player2_id.eq.{user_id}"),
        "rank_daily_matches_started",
        attempts=2,
    )
    return list(result.data or [])


def _is_counted_rank_match(match):
    status = str((match or {}).get("status") or "").strip().lower()
    if status in COUNTED_MATCH_STATUSES:
        return True
    if status == "cancelled":
        checker = globals().get("is_forfeit_match")
        if callable(checker):
            try:
                return bool(checker(match))
            except Exception:
                pass
        note = str((match or {}).get("note") or "").casefold()
        return "[forfeit:" in note or "bỏ cuộc" in note
    return False




def _match_counts_for_user(match, user_id):
    """Cho biết trận có chiếm lượt Rank ngày của riêng người chơi hay không.

    Các trận bị chuyển thành không tính RP vì một trong hai người đã hết lượt
    sẽ lưu ``counted_user_ids=[]`` trong rp_details. Nhờ vậy người còn lượt
    không bị mất lượt và người đã hết lượt cũng không tăng số đếm vô hạn.
    """
    details = (match or {}).get("rp_details")
    if not isinstance(details, dict):
        return True
    daily = details.get("daily_rank_limits")
    if not isinstance(daily, dict):
        return True
    counted_user_ids = daily.get("counted_user_ids")
    if counted_user_ids is None:
        return True
    return str(user_id) in {str(value) for value in (counted_user_ids or [])}

def _ranked_matches_started_today(user_id):
    reset_at = get_user_daily_rank_reset(user_id)
    matches = []
    for match in _matches_today(user_id):
        if not _is_counted_rank_match(match):
            continue
        if not _match_counts_for_user(match, user_id):
            continue
        if reset_at:
            try:
                created = datetime.fromisoformat(str(match.get("created_at") or "").replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created.astimezone(timezone.utc) < reset_at:
                    continue
            except Exception:
                pass
        matches.append(match)
    return matches


def ranked_games_today(user_id):
    """Đếm mọi trận Rank đã bắt đầu trong ngày, kể cả chưa xác nhận."""
    return len(_ranked_matches_started_today(user_id))


def positive_rp_today(user_id, exclude_match_id=None):
    """Chỉ RP dương đã chốt ở trận confirmed mới được cộng vào trần +150."""
    total = 0
    for match in _matches_today(user_id):
        if str(match.get("status") or "") != "confirmed":
            continue
        if exclude_match_id and str(match.get("id")) == str(exclude_match_id):
            continue
        if str(match.get("player1_id")) == str(user_id):
            delta = int(match.get("delta1") or 0)
        else:
            delta = int(match.get("delta2") or 0)
        if delta > 0:
            total += delta
    return total


def rank_daily_status(user_id):
    enabled = daily_rank_limits_enabled()
    games = ranked_games_today(user_id) if enabled else 0
    positive = positive_rp_today(user_id) if enabled else 0
    return {
        "enabled": enabled,
        "games": games,
        "game_limit": current_daily_game_limit(),
        "games_remaining": max(0, current_daily_game_limit() - games),
        "is_weekend": _now_vn().weekday() in {5, 6},
        "positive_rp": positive,
        "positive_rp_limit": DAILY_POSITIVE_RP_LIMIT,
        "positive_rp_remaining": max(0, DAILY_POSITIVE_RP_LIMIT - positive),
    }



def user_reached_daily_rank_limit(user_id):
    """True khi người chơi đã chạm giới hạn trận Rank của ngày hiện tại."""
    if not user_id or not daily_rank_limits_enabled():
        return False
    return ranked_games_today(user_id) >= current_daily_game_limit()


def daily_rank_match_rp_status(*user_ids):
    """Cho biết trận hiện tại còn được tính RP hay đã vượt lượt ngày.

    Trận được tạo trước khi chốt kết quả nên trận thứ 10/15 vẫn hợp lệ; chỉ từ
    trận thứ 11/16 trở đi mới bị chuyển thành trận không tính RP.
    """
    game_limit = current_daily_game_limit()
    if not daily_rank_limits_enabled():
        return {
            "enabled": False,
            "rp_eligible": True,
            "game_limit": game_limit,
            "players": {},
            "reason": "disabled_by_admin",
        }
    players = {}
    exceeded = False
    for user_id in user_ids:
        if not user_id:
            continue
        games = ranked_games_today(user_id)
        over_limit = games > game_limit
        players[str(user_id)] = {
            "games_today": games,
            "game_limit": game_limit,
            "over_limit": over_limit,
        }
        exceeded = exceeded or over_limit
    return {
        "enabled": True,
        "rp_eligible": not exceeded,
        "game_limit": game_limit,
        "players": players,
        "reason": "daily_game_limit_exceeded" if exceeded else "within_daily_limit",
    }


def daily_rank_block_message(*user_ids):
    """Không còn chặn thi đấu; giới hạn chỉ quyết định trận có được tính RP."""
    return None


def assert_can_start_ranked_match(*user_ids):
    """Giữ API tương thích cũ nhưng luôn cho phép bắt đầu trận Rank."""
    return True


def apply_daily_positive_rp_cap(user_id, delta, exclude_match_id=None):
    delta = int(delta or 0)
    if delta <= 0 or not daily_rank_limits_enabled():
        return delta, None
    earned = positive_rp_today(user_id, exclude_match_id=exclude_match_id)
    remaining = max(0, DAILY_POSITIVE_RP_LIMIT - earned)
    applied = min(delta, remaining)
    detail = {
        "enabled": True,
        "earned_before": earned,
        "formula_delta": delta,
        "applied_delta": applied,
        "remaining_before": remaining,
        "limit": DAILY_POSITIVE_RP_LIMIT,
        "capped": applied < delta,
    }
    return applied, detail
