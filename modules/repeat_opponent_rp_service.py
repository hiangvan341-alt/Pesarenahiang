"""Quy tắc RP khi hai người gặp lại nhau trong cùng ngày Việt Nam."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

EXPORTED_NAMES = [
    "repeat_opponent_context",
    "apply_repeat_opponent_rules",
    "repeat_opponent_rules_enabled",
    "repeat_opponent_winner_factors",
    "repeat_opponent_loser_factors",
]

VN_TZ = timezone(timedelta(hours=7))
COUNTED_STATUSES = {"playing", "waiting_confirm", "processing_result", "disputed", "confirmed"}


def configure(context):
    globals().update(context)



def repeat_opponent_winner_factors():
    getter = globals().get("get_repeat_opponent_rp_config")
    if callable(getter):
        try:
            values = (getter() or {}).get("winner_factors") or [100, 60, 30, 0]
            if len(values) == 4:
                return [max(0.0, min(1.0, float(value) / 100.0)) for value in values]
        except Exception:
            pass
    return [1.0, 0.6, 0.3, 0.0]


def repeat_opponent_loser_factors():
    getter = globals().get("get_repeat_opponent_rp_config")
    if callable(getter):
        try:
            values = (getter() or {}).get("loser_factors") or [100, 70, 40, 10]
            if len(values) == 4:
                return [max(0.0, min(1.0, float(value) / 100.0)) for value in values]
        except Exception:
            pass
    return [1.0, 0.7, 0.4, 0.1]


def repeat_opponent_rules_enabled():
    checker = globals().get("system_feature_enabled")
    if callable(checker):
        try:
            return bool(checker("repeat_opponent_rp_enabled"))
        except Exception:
            return True
    return True


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _day_bounds(value):
    current_vn = _parse_dt(value).astimezone(VN_TZ)
    start_vn = current_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    end_vn = start_vn + timedelta(days=1)
    return start_vn.astimezone(timezone.utc).isoformat(), end_vn.astimezone(timezone.utc).isoformat()


def _pair_key(a, b):
    return tuple(sorted((str(a or ""), str(b or ""))))


def _is_counted(match):
    status = str((match or {}).get("status") or "").lower()
    if status in COUNTED_STATUSES:
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


def _is_earlier(row, current):
    return (str(row.get("created_at") or ""), str(row.get("id") or "")) < (
        str(current.get("created_at") or ""), str(current.get("id") or "")
    )


def _winner_id(match):
    winner = match.get("winner_id")
    if winner:
        return str(winner)
    try:
        s1, s2 = int(match.get("score1")), int(match.get("score2"))
    except (TypeError, ValueError):
        return ""
    if s1 == s2:
        return ""
    return str(match.get("player1_id") if s1 > s2 else match.get("player2_id"))


def _had_draw_bonus(match):
    details = match.get("rp_details") or {}
    if isinstance(details, dict):
        repeat = details.get("repeat_opponent") or {}
        if isinstance(repeat, dict) and repeat.get("draw_bonus_applied"):
            return True
    try:
        return int(match.get("delta1") or 0) in {5, 6} or int(match.get("delta2") or 0) in {5, 6}
    except Exception:
        return False


def repeat_opponent_context(match):
    """Đọc các trận trước đó của đúng cặp trong ngày của trận hiện tại."""
    p1 = str((match or {}).get("player1_id") or "")
    p2 = str((match or {}).get("player2_id") or "")
    if not p1 or not p2:
        return {"prior_encounters": 0, "wins": {p1: 0, p2: 0}, "draw_bonus_used": False}
    start_iso, end_iso = _day_bounds(match.get("created_at"))
    result = execute_query(
        db.table("matches")
        .select("id,player1_id,player2_id,score1,score2,winner_id,status,delta1,delta2,rp_details,note,created_at")
        .gte("created_at", start_iso).lt("created_at", end_iso)
        .or_(f"and(player1_id.eq.{p1},player2_id.eq.{p2}),and(player1_id.eq.{p2},player2_id.eq.{p1})"),
        "repeat_opponent_daily_pair",
        attempts=2,
    )
    pair = _pair_key(p1, p2)
    prior = []
    for row in result.data or []:
        if str(row.get("id")) == str(match.get("id")):
            continue
        if _pair_key(row.get("player1_id"), row.get("player2_id")) != pair:
            continue
        if not _is_counted(row) or not _is_earlier(row, match):
            continue
        prior.append(row)
    wins = {p1: 0, p2: 0}
    draw_bonus_used = False
    for row in prior:
        winner = _winner_id(row)
        if winner in wins:
            wins[winner] += 1
        if _had_draw_bonus(row):
            draw_bonus_used = True
    return {
        "prior_encounters": len(prior),
        "encounter_number": len(prior) + 1,
        "wins": wins,
        "draw_bonus_used": draw_bonus_used,
    }


def _round_scaled(value, factor):
    sign = -1 if int(value) < 0 else 1
    return sign * int(math.floor(abs(int(value)) * float(factor) + 0.5))


def apply_repeat_opponent_rules(
    match, player1, player2, score1, score2, delta1, delta2, context=None,
    streak_bonus1=0, streak_bonus2=0,
):
    """Áp dụng hệ số cặp đấu và trả delta + metadata điều khiển chuỗi."""
    if not repeat_opponent_rules_enabled():
        return int(delta1), int(delta2), {
            "enabled": False,
            "counted_for_rp": True,
            "streak_eligible": True,
            "reason": "disabled_by_admin",
        }
    context = dict(context or repeat_opponent_context(match))
    encounter = int(context.get("encounter_number") or 1)
    p1, p2 = str(match.get("player1_id") or ""), str(match.get("player2_id") or "")
    details = {
        "enabled": True,
        "encounter_number": encounter,
        "prior_encounters": max(0, encounter - 1),
        "counted_for_rp": encounter <= 6,
        "streak_eligible": True,
        "winner_repeat_win_number": None,
        "winner_factor": None,
        "loser_factor": 1.0,
        "draw_bonus_applied": False,
    }

    if encounter >= 7:
        details.update({"counted_for_rp": False, "streak_eligible": False, "reason": "pair_daily_limit"})
        return 0, 0, details

    if int(score1) == int(score2):
        # Trận hòa vẫn tính trong giới hạn đối đầu; chỉ các trận thứ 7 trở đi mới nhận 0 RP.
        details["streak_eligible"] = True
        rp1 = int(player1.get("rank_points") or 0)
        rp2 = int(player2.get("rank_points") or 0)
        if abs(rp1 - rp2) >= 500:
            if rp1 < rp2:
                delta1, delta2 = 6, 0
            elif rp2 < rp1:
                delta1, delta2 = 0, 6
            else:
                delta1, delta2 = 3, 3
            details["draw_bonus_applied"] = bool(delta1 == 6 or delta2 == 6)
        else:
            delta1, delta2 = 3, 3
        details["reason"] = "draw"
        return int(delta1), int(delta2), details

    p1_won = int(score1) > int(score2)
    winner_id = p1 if p1_won else p2
    repeat_win_number = int((context.get("wins") or {}).get(winner_id, 0)) + 1
    details["winner_repeat_win_number"] = repeat_win_number

    winner_factors = repeat_opponent_winner_factors()
    loser_factors = repeat_opponent_loser_factors()
    factor_index = min(max(repeat_win_number, 1), 4) - 1
    winner_factor = winner_factors[factor_index]
    loser_factor = loser_factors[factor_index]
    details["configured_winner_factors"] = winner_factors
    details["configured_loser_factors"] = loser_factors
    details["winner_factor"] = winner_factor
    details["loser_factor"] = loser_factor
    if repeat_win_number >= 4:
        details["streak_eligible"] = False

    # Hệ số gặp lại chỉ giảm phần RP thắng cơ bản. Thưởng chuỗi là thành tích
    # độc lập nên luôn được cộng đủ, không bị nhân 60%/30%/0%.
    original_streak_bonus1 = max(0, int(streak_bonus1 or 0))
    original_streak_bonus2 = max(0, int(streak_bonus2 or 0))
    # Từ lần thắng thứ 4, trận không được tính chuỗi nên cũng không được nhận
    # thưởng mốc chuỗi dù delta gốc đã tạm tính trước đó.
    streak_bonus1 = original_streak_bonus1 if details["streak_eligible"] else 0
    streak_bonus2 = original_streak_bonus2 if details["streak_eligible"] else 0
    if p1_won:
        base_delta1 = max(0, int(delta1) - original_streak_bonus1)
        delta1 = _round_scaled(base_delta1, winner_factor) + streak_bonus1
        delta2 = _round_scaled(delta2, loser_factor)
        details["winner_base_before_factor"] = base_delta1
        details["winner_streak_bonus"] = streak_bonus1
    else:
        base_delta2 = max(0, int(delta2) - original_streak_bonus2)
        delta2 = _round_scaled(base_delta2, winner_factor) + streak_bonus2
        delta1 = _round_scaled(delta1, loser_factor)
        details["winner_base_before_factor"] = base_delta2
        details["winner_streak_bonus"] = streak_bonus2
    details["streak_bonus_scaled"] = False
    details["reason"] = "repeat_win"
    return int(delta1), int(delta2), details
