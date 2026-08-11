"""Tính lại BXH sau khi Admin sửa tỷ số hoặc trạng thái trận.

Mọi trận được phát lại theo ``created_at`` gốc. Module chỉ tính toán, không truy cập
Flask/Supabase, nhờ đó có thể kiểm thử độc lập và không làm thay đổi thời điểm trận.

Điểm quan trọng:
- Giữ nguyên phần thống kê/RP gốc không được tạo bởi các dòng trong bảng ``matches``
  (ví dụ dữ liệu import/legacy hoặc điều chỉnh thủ công).
- Tính lại toàn bộ các trận confirmed theo đúng thứ tự thời gian.
- Không bao giờ đưa ``created_at`` vào payload cập nhật.
"""
from __future__ import annotations

import random
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _sort_key(match: Mapping[str, Any]) -> tuple[str, str]:
    return (str(match.get("created_at") or ""), str(match.get("id") or ""))


def _vn_day_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=7))).date().isoformat()
    except Exception:
        return text[:10] or "unknown"


def _outcome_for_player(match: Mapping[str, Any], user_id: str) -> str | None:
    p1_id = str(match.get("player1_id") or "")
    p2_id = str(match.get("player2_id") or "")
    if user_id not in {p1_id, p2_id}:
        return None
    score1 = _int(match.get("score1"), -1)
    score2 = _int(match.get("score2"), -1)
    if score1 < 0 or score2 < 0:
        return None
    own, opp = (score1, score2) if user_id == p1_id else (score2, score1)
    if own > opp:
        return "win"
    if own < opp:
        return "loss"
    return "draw"


def _derive_initial_streaks(
    user: Mapping[str, Any],
    old_confirmed_matches: list[Mapping[str, Any]],
) -> tuple[int, int]:
    """Suy ra chuỗi trước trận đầu tiên sao cho lịch sử cũ phát lại không đổi kết quả cuối.

    Nếu chuỗi có điểm reset (thua đối với win streak; thắng/hòa đối với loss streak),
    trạng thái ban đầu không còn ảnh hưởng nên dùng 0. Nếu không có điểm reset, trừ số
    trận liên quan khỏi giá trị cuối hiện tại để bảo toàn dữ liệu legacy.
    """
    user_id = str(user.get("id") or "")
    outcomes = [
        outcome
        for match in sorted(old_confirmed_matches, key=_sort_key)
        if (outcome := _outcome_for_player(match, user_id)) is not None
    ]
    current_streak = max(0, _int(user.get("streak")))
    current_loss_streak = max(0, _int(user.get("loss_streak")))
    if not outcomes:
        return current_streak, current_loss_streak

    if "loss" in outcomes:
        initial_streak = 0
    else:
        initial_streak = max(0, current_streak - sum(1 for item in outcomes if item == "win"))

    if any(item in {"win", "draw"} for item in outcomes):
        initial_loss_streak = 0
    else:
        initial_loss_streak = max(0, current_loss_streak - sum(1 for item in outcomes if item == "loss"))

    return initial_streak, initial_loss_streak


def _old_contributions(
    matches: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, int], dict[str, dict[str, int]], dict[str, list[Mapping[str, Any]]]]:
    delta_sums: dict[str, int] = {}
    stat_sums: dict[str, dict[str, int]] = {}
    confirmed_by_user: dict[str, list[Mapping[str, Any]]] = {}

    def stats_for(user_id: str) -> dict[str, int]:
        return stat_sums.setdefault(user_id, {
            "total_matches": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
        })

    for raw in matches:
        match = dict(raw)
        if str(match.get("status") or "") != "confirmed":
            continue
        score1 = _int(match.get("score1"), -1)
        score2 = _int(match.get("score2"), -1)
        if score1 < 0 or score2 < 0:
            continue
        p1_id = str(match.get("player1_id") or "")
        p2_id = str(match.get("player2_id") or "")
        if p1_id:
            delta_sums[p1_id] = delta_sums.get(p1_id, 0) + _int(match.get("delta1"))
            row = stats_for(p1_id)
            row["total_matches"] += 1
            row["wins"] += int(score1 > score2)
            row["draws"] += int(score1 == score2)
            row["losses"] += int(score1 < score2)
            row["goals_for"] += score1
            row["goals_against"] += score2
            confirmed_by_user.setdefault(p1_id, []).append(match)
        if p2_id:
            delta_sums[p2_id] = delta_sums.get(p2_id, 0) + _int(match.get("delta2"))
            row = stats_for(p2_id)
            row["total_matches"] += 1
            row["wins"] += int(score2 > score1)
            row["draws"] += int(score2 == score1)
            row["losses"] += int(score2 < score1)
            row["goals_for"] += score2
            row["goals_against"] += score1
            confirmed_by_user.setdefault(p2_id, []).append(match)

    return delta_sums, stat_sums, confirmed_by_user


def _initial_player_state(
    user: Mapping[str, Any],
    confirmed_delta_sum: int,
    confirmed_stats: Mapping[str, int],
    old_confirmed_matches: list[Mapping[str, Any]],
    default_points: int,
) -> dict[str, Any]:
    current_points = _int(user.get("rank_points"), default_points)
    initial_streak, initial_loss_streak = _derive_initial_streaks(user, old_confirmed_matches)
    initial_wins = max(0, _int(user.get("wins")) - _int(confirmed_stats.get("wins")))
    initial_draws = max(0, _int(user.get("draws")) - _int(confirmed_stats.get("draws")))
    initial_losses = max(0, _int(user.get("losses")) - _int(confirmed_stats.get("losses")))
    return {
        "id": user.get("id"),
        "rank_points": max(0, current_points - int(confirmed_delta_sum)),
        "total_matches": initial_wins + initial_draws + initial_losses,
        "wins": initial_wins,
        "draws": initial_draws,
        "losses": initial_losses,
        "goals_for": max(0, _int(user.get("goals_for")) - _int(confirmed_stats.get("goals_for"))),
        "goals_against": max(0, _int(user.get("goals_against")) - _int(confirmed_stats.get("goals_against"))),
        "streak": initial_streak,
        "loss_streak": initial_loss_streak,
        "loss_recovery_win_step": 0,
    }


def _apply_state(state: dict[str, Any], delta: int, goals_for: int, goals_against: int, affect_streak: bool = True) -> None:
    won = goals_for > goals_against
    drew = goals_for == goals_against
    lost = goals_for < goals_against
    state["rank_points"] = max(0, _int(state.get("rank_points")) + _int(delta))
    state["wins"] = _int(state.get("wins")) + int(won)
    state["draws"] = _int(state.get("draws")) + int(drew)
    state["losses"] = _int(state.get("losses")) + int(lost)
    state["total_matches"] = state["wins"] + state["draws"] + state["losses"]
    state["goals_for"] = _int(state.get("goals_for")) + _int(goals_for)
    state["goals_against"] = _int(state.get("goals_against")) + _int(goals_against)
    if not affect_streak:
        return
    if won:
        previous_losses = _int(state.get("loss_streak"))
        previous_recovery = _int(state.get("loss_recovery_win_step"))
        state["streak"] = _int(state.get("streak")) + 1
        state["loss_streak"] = 0
        if previous_losses >= 5:
            state["loss_recovery_win_step"] = 2
        elif previous_recovery == 2:
            state["loss_recovery_win_step"] = 0
    elif lost:
        state["streak"] = 0
        state["loss_streak"] = _int(state.get("loss_streak")) + 1
        state["loss_recovery_win_step"] = 0
    else:
        # Hòa làm gián đoạn chuỗi thắng liên tiếp và kết thúc chuỗi thua.
        state["streak"] = 0
        state["loss_streak"] = 0


def _winner_loser(match: Mapping[str, Any], score1: int, score2: int) -> tuple[Any, Any]:
    if score1 > score2:
        return match.get("player1_id"), match.get("player2_id")
    if score2 > score1:
        return match.get("player2_id"), match.get("player1_id")
    return None, None


def build_replay_plan(
    *,
    users: Iterable[Mapping[str, Any]],
    matches: Iterable[Mapping[str, Any]],
    overrides: Mapping[str, Mapping[str, Any]],
    calculate_deltas: Callable[..., tuple[int, int]],
    get_rank_level: Callable[[int], int],
    apply_host_factor: Callable[[int, float], int],
    host_by_match: Mapping[str, Any],
    default_points: int,
    placement_matches: int,
    host_win_factor: float,
    formula_version: str,
    formula_summary: Callable[[], Any],
    seed_namespace: str,
    daily_positive_rp_limit: int | None = None,
    repeat_opponent_rules_enabled: bool = True,
    repeat_opponent_winner_factors: tuple[float, float, float, float] = (1.0, 0.6, 0.3, 0.0),
    repeat_opponent_loser_factors: tuple[float, float, float, float] = (1.0, 0.7, 0.4, 0.1),
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Trả về payload cập nhật user/match sau khi phát lại lịch sử.

    ``created_at`` không bao giờ xuất hiện trong payload cập nhật.
    """
    original_matches = [dict(row) for row in matches]
    delta_sums, stat_sums, confirmed_by_user = _old_contributions(original_matches)

    states: dict[str, dict[str, Any]] = {}
    for user in users:
        user_id = str(user.get("id") or "")
        if user_id:
            states[user_id] = _initial_player_state(
                user,
                delta_sums.get(user_id, 0),
                stat_sums.get(user_id, {}),
                confirmed_by_user.get(user_id, []),
                default_points,
            )

    effective_matches: list[dict[str, Any]] = []
    for original in original_matches:
        match = dict(original)
        override = overrides.get(str(match.get("id") or ""))
        if override:
            match.update({key: value for key, value in dict(override).items() if key != "created_at"})
        effective_matches.append(match)

    match_updates: dict[str, dict[str, Any]] = {}
    positive_rp_by_day: dict[tuple[str, str], int] = {}
    ranked_games_by_day: dict[tuple[str, str], int] = {}
    pair_encounters: dict[tuple[str, tuple[str, str]], int] = {}
    pair_wins: dict[tuple[str, tuple[str, str], str], int] = {}
    pair_draw_bonus: set[tuple[str, tuple[str, str]]] = set()
    for match in sorted(effective_matches, key=_sort_key):
        match_id = str(match.get("id") or "")
        if not match_id:
            continue
        status = str(match.get("status") or "")
        if status != "confirmed":
            if match_id in overrides:
                match_updates[match_id] = {
                    **{key: value for key, value in dict(overrides[match_id]).items() if key != "created_at"},
                    "delta1": 0,
                    "delta2": 0,
                    "winner_id": None,
                    "loser_id": None,
                    "rp_formula_version": None,
                    "rp_details": None,
                }
            continue

        p1_id = str(match.get("player1_id") or "")
        p2_id = str(match.get("player2_id") or "")
        score1 = _int(match.get("score1"), -1)
        score2 = _int(match.get("score2"), -1)
        if score1 < 0 or score2 < 0:
            raise ValueError(f"Trận {match_id} đã xác nhận nhưng thiếu tỷ số.")
        if p1_id not in states or p2_id not in states:
            if match_id in overrides:
                raise ValueError(f"Trận {match_id} thiếu dữ liệu người chơi để tính lại kết quả mới.")
            # Dữ liệu legacy có thể còn trận của tài khoản đã bị xóa. Giữ nguyên delta
            # đã lưu và phát lại phần của người chơi còn tồn tại để không khóa cả BXH.
            if p1_id in states:
                _apply_state(states[p1_id], _int(match.get("delta1")), score1, score2)
            if p2_id in states:
                _apply_state(states[p2_id], _int(match.get("delta2")), score2, score1)
            continue

        player1, player2 = states[p1_id], states[p2_id]
        rng = random.Random(f"{seed_namespace}|{match_id}")
        delta1, delta2 = calculate_deltas(
            player1,
            player2,
            score1,
            score2,
            get_rank_level=get_rank_level,
            team_a=match.get("team1"),
            team_b=match.get("team2"),
            team_overall_a=match.get("team1_overall"),
            team_overall_b=match.get("team2_overall"),
            team_tier_a=match.get("team1_tier"),
            team_tier_b=match.get("team2_tier"),
            rng=rng,
        )
        delta1, delta2 = _int(delta1), _int(delta2)
        def streak_bonus(state, won):
            if not won:
                return 0
            next_streak = _int(state.get("streak")) + 1
            if next_streak == 3:
                return 5
            if next_streak == 5:
                return 10
            if next_streak >= 10 and next_streak % 5 == 0:
                return 15
            return 0
        streak_bonus1 = streak_bonus(player1, score1 > score2)
        streak_bonus2 = streak_bonus(player2, score2 > score1)
        host_id = str(match.get("host_user_id") or host_by_match.get(match_id) or "")
        factor = match.get("host_xp_factor", host_win_factor)
        if host_id == p1_id and score1 > score2:
            if delta1 not in (17, 19):
                delta1 = _int(apply_host_factor(max(0, delta1 - streak_bonus1), factor)) + streak_bonus1
            if (_int(player1.get("wins")) + _int(player1.get("draws")) + _int(player1.get("losses"))) < placement_matches:
                delta1 = max(22, min(29, delta1))
        elif host_id == p2_id and score2 > score1:
            if delta2 not in (17, 19):
                delta2 = _int(apply_host_factor(max(0, delta2 - streak_bonus2), factor)) + streak_bonus2
            if (_int(player2.get("wins")) + _int(player2.get("draws")) + _int(player2.get("losses"))) < placement_matches:
                delta2 = max(22, min(29, delta2))

        day_key = _vn_day_key(match.get("created_at"))
        # Áp dụng quy tắc gặp lại cùng đối thủ theo đúng thứ tự lịch sử khi Admin bật.
        repeat_details = {
            "enabled": bool(repeat_opponent_rules_enabled),
            "counted_for_rp": True,
            "streak_eligible": True,
            "reason": "disabled_by_admin" if not repeat_opponent_rules_enabled else None,
        }
        affect_streak = True
        if repeat_opponent_rules_enabled:
            pair_key = tuple(sorted((p1_id, p2_id)))
            pair_day_key = (day_key, pair_key)
            encounter_number = pair_encounters.get(pair_day_key, 0) + 1
            pair_encounters[pair_day_key] = encounter_number
            repeat_details = {
                "enabled": True,
                "encounter_number": encounter_number,
                "prior_encounters": encounter_number - 1,
                "counted_for_rp": encounter_number <= 6,
                "streak_eligible": True,
                "winner_repeat_win_number": None,
                "winner_factor": None,
                "loser_factor": 1.0,
                "draw_bonus_applied": False,
            }
            if encounter_number >= 7:
                delta1 = delta2 = 0
                affect_streak = False
                repeat_details.update({"counted_for_rp": False, "streak_eligible": False, "reason": "pair_daily_limit"})
            elif score1 == score2:
                affect_streak = True
                rp1 = _int(player1.get("rank_points"))
                rp2 = _int(player2.get("rank_points"))
                if abs(rp1 - rp2) >= 500:
                    if rp1 < rp2:
                        delta1, delta2 = 6, 0
                    elif rp2 < rp1:
                        delta1, delta2 = 0, 6
                    else:
                        delta1 = delta2 = 3
                    repeat_details["draw_bonus_applied"] = bool(delta1 == 6 or delta2 == 6)
                else:
                    delta1 = delta2 = 3
                repeat_details.update({"streak_eligible": True, "reason": "draw"})
            else:
                p1_won = score1 > score2
                winner_id = p1_id if p1_won else p2_id
                win_key = (day_key, pair_key, winner_id)
                repeat_win_number = pair_wins.get(win_key, 0) + 1
                pair_wins[win_key] = repeat_win_number
                configured_winner_factors = list(repeat_opponent_winner_factors or (1.0, 0.6, 0.3, 0.0))
                configured_loser_factors = list(repeat_opponent_loser_factors or (1.0, 0.7, 0.4, 0.1))
                while len(configured_winner_factors) < 4:
                    configured_winner_factors.append(0.0)
                while len(configured_loser_factors) < 4:
                    configured_loser_factors.append(0.0)
                factor_index = min(max(repeat_win_number, 1), 4) - 1
                winner_factor = float(configured_winner_factors[factor_index])
                loser_factor = float(configured_loser_factors[factor_index])
                repeat_details.update({
                    "winner_repeat_win_number": repeat_win_number,
                    "winner_factor": winner_factor,
                    "loser_factor": loser_factor,
                    "configured_winner_factors": configured_winner_factors[:4],
                    "configured_loser_factors": configured_loser_factors[:4],
                    "reason": "repeat_win",
                })
                def scaled(value, coefficient):
                    sign = -1 if _int(value) < 0 else 1
                    return sign * int(math.floor(abs(_int(value)) * coefficient + 0.5))
                effective_streak_bonus1 = streak_bonus1 if repeat_win_number < 4 else 0
                effective_streak_bonus2 = streak_bonus2 if repeat_win_number < 4 else 0
                if p1_won:
                    winner_base = max(0, delta1 - streak_bonus1)
                    delta1 = scaled(winner_base, winner_factor) + effective_streak_bonus1
                    delta2 = scaled(delta2, loser_factor)
                    repeat_details.update({
                        "winner_base_before_factor": winner_base,
                        "winner_streak_bonus": effective_streak_bonus1,
                        "streak_bonus_scaled": False,
                    })
                else:
                    winner_base = max(0, delta2 - streak_bonus2)
                    delta2 = scaled(winner_base, winner_factor) + effective_streak_bonus2
                    delta1 = scaled(delta1, loser_factor)
                    repeat_details.update({
                        "winner_base_before_factor": winner_base,
                        "winner_streak_bonus": effective_streak_bonus2,
                        "streak_bonus_scaled": False,
                    })
                if repeat_win_number >= 4:
                    affect_streak = False
                    repeat_details.update({"streak_eligible": False})

        daily_limit_details = None
        if daily_positive_rp_limit is not None and int(daily_positive_rp_limit) >= 0:
            # Khi bật giới hạn ngày, trận thứ 11 trong ngày thường hoặc thứ 16
            # vào cuối tuần vẫn được lưu nhưng không tính RP/chuỗi.
            try:
                day_dt = datetime.fromisoformat(str(day_key))
                game_limit = 15 if day_dt.weekday() in {5, 6} else 10
            except Exception:
                game_limit = 10
            p1_previous_games = ranked_games_by_day.get((p1_id, day_key), 0)
            p2_previous_games = ranked_games_by_day.get((p2_id, day_key), 0)
            p1_games = p1_previous_games + 1
            p2_games = p2_previous_games + 1
            rp_eligible = p1_games <= game_limit and p2_games <= game_limit
            # Chỉ trận còn đủ lượt cho cả hai mới chiếm lượt. Nếu một người đã
            # hết lượt, cả hai vẫn đá/lưu lịch sử nhưng người còn lượt không mất lượt.
            if rp_eligible:
                ranked_games_by_day[(p1_id, day_key)] = p1_games
                ranked_games_by_day[(p2_id, day_key)] = p2_games
            game_status = {
                "enabled": True,
                "rp_eligible": rp_eligible,
                "game_limit": game_limit,
                "players": {
                    p1_id: {"games_today": p1_games, "over_limit": p1_games > game_limit},
                    p2_id: {"games_today": p2_games, "over_limit": p2_games > game_limit},
                },
                "reason": "within_daily_limit",
            }
            if not game_status["rp_eligible"]:
                delta1 = delta2 = 0
                affect_streak = False
                repeat_details["streak_eligible"] = False
                game_status["reason"] = "daily_game_limit_exceeded"

            cap_details = {}
            for user_id, key, delta in ((p1_id, "player1", delta1), (p2_id, "player2", delta2)):
                if delta <= 0:
                    cap_details[key] = None
                    continue
                earned = positive_rp_by_day.get((user_id, day_key), 0)
                remaining = max(0, int(daily_positive_rp_limit) - earned)
                applied = min(delta, remaining)
                cap_details[key] = {
                    "enabled": True, "earned_before": earned,
                    "formula_delta": delta, "applied_delta": applied,
                    "remaining_before": remaining, "limit": int(daily_positive_rp_limit),
                    "capped": applied < delta,
                }
                positive_rp_by_day[(user_id, day_key)] = earned + max(0, applied)
                if key == "player1":
                    delta1 = applied
                else:
                    delta2 = applied
            daily_limit_details = {
                "game_limit": game_status,
                "counted_user_ids": [p1_id, p2_id] if game_status["rp_eligible"] else [],
                "count_rule": "both_players" if game_status["rp_eligible"] else "neither_player",
                "positive_rp_cap": cap_details,
            }

        _apply_state(player1, delta1, score1, score2, affect_streak=affect_streak)
        _apply_state(player2, delta2, score2, score1, affect_streak=affect_streak)
        winner_id, loser_id = _winner_loser(match, score1, score2)
        payload = {
            "delta1": delta1,
            "delta2": delta2,
            "winner_id": winner_id,
            "loser_id": loser_id,
            "rp_formula_version": formula_version,
            "rp_details": {
                "source": "admin_chronological_replay",
                "formula": formula_summary(),
                "seed": f"{seed_namespace}|{match_id}",
                "delta1": delta1,
                "delta2": delta2,
                "repeat_opponent": repeat_details,
                "daily_rank_limits": daily_limit_details,
            },
        }
        if match_id in overrides:
            payload.update({key: value for key, value in dict(overrides[match_id]).items() if key != "created_at"})
        match_updates[match_id] = payload

    user_updates = {
        user_id: {
            "rank_points": state["rank_points"],
            "total_matches": state["total_matches"],
            "wins": state["wins"],
            "draws": state["draws"],
            "losses": state["losses"],
            "goals_for": state["goals_for"],
            "goals_against": state["goals_against"],
            "streak": state["streak"],
            "loss_streak": state["loss_streak"],
        }
        for user_id, state in states.items()
    }
    return user_updates, match_updates
