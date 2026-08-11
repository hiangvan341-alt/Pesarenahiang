"""Ghi nhận các trận thua do bỏ cuộc vào bảng ``matches``.

Module không thay đổi công thức RP. Payload ghi lịch sử chỉ dùng các cột đã
được dự án sử dụng từ trước, tránh phụ thuộc vào các cột SQL nâng cấp tùy chọn.
Lỗi ghi lịch sử được cô lập để không làm route Bỏ cuộc trả về HTTP 500 sau khi
phòng và RP đã được xử lý.
"""

EXPORTED_NAMES = [
    "record_room_forfeit_match",
    "is_forfeit_match",
    "forfeit_loser_id",
    "forfeit_display_note",
]


def configure(context):
    """Liên kết dependency từ app.py mà không tạo import vòng."""
    globals().update(context)


def _safe_delta(value):
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_overall(value):
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _forfeit_marker(offender_role):
    role = "host" if str(offender_role or "").lower() == "host" else "guest"
    return f"[FORFEIT:{role}]"


def forfeit_display_note(match_or_note):
    """Bỏ marker kỹ thuật trước khi đưa ghi chú ra giao diện."""
    if isinstance(match_or_note, dict):
        note = str(match_or_note.get("note") or "")
    else:
        note = str(match_or_note or "")
    for marker in ("[FORFEIT:host]", "[FORFEIT:guest]"):
        if note.lower().startswith(marker.lower()):
            return note[len(marker):].strip()
    return note


def is_forfeit_match(match):
    """Nhận diện bản ghi bỏ cuộc mới và dữ liệu bỏ cuộc từ các bản cũ."""
    if not isinstance(match, dict) or str(match.get("status") or "") != "cancelled":
        return False
    note = str(match.get("note") or "").casefold()
    if "[forfeit:host]" in note or "[forfeit:guest]" in note:
        return True
    if match.get("loser_id"):
        return True
    if any(token in note for token in ("bỏ cuộc", "bỏ trận", "bỏ dở", "rời phòng sau khi")):
        return True
    delta1 = _safe_delta(match.get("delta1"))
    delta2 = _safe_delta(match.get("delta2"))
    return (delta1 < 0) != (delta2 < 0)


def forfeit_loser_id(match):
    """Trả về ID người bị tính thua, hỗ trợ cả dữ liệu cũ không có loser_id."""
    if not is_forfeit_match(match):
        return None
    if match.get("loser_id"):
        return match.get("loser_id")

    note = str(match.get("note") or "").casefold()
    if "[forfeit:host]" in note:
        return match.get("player1_id")
    if "[forfeit:guest]" in note:
        return match.get("player2_id")

    delta1 = _safe_delta(match.get("delta1"))
    delta2 = _safe_delta(match.get("delta2"))
    if delta1 < delta2 and delta1 < 0:
        return match.get("player1_id")
    if delta2 < delta1 and delta2 < 0:
        return match.get("player2_id")
    return None


def _base_forfeit_values(room, offender_role, penalty_delta, reason):
    """Tạo dữ liệu bỏ cuộc chỉ bằng các cột chắc chắn có trong dự án."""
    role = "host" if str(offender_role or "").lower() == "host" else "guest"
    delta = _safe_delta(penalty_delta)
    if delta > 0:
        delta = -delta

    return role, delta, {
        "status": "cancelled",
        "delta1": delta if role == "host" else 0,
        "delta2": delta if role == "guest" else 0,
        "note": f"{_forfeit_marker(role)} {str(reason or 'Người chơi bỏ cuộc.').strip()}",
        "updated_at": now_iso(),
    }


def _existing_match_payload(room, offender_role, penalty_delta, reason):
    """Payload cập nhật trận đã tạo khi quay đội."""
    _role, _delta, payload = _base_forfeit_values(
        room, offender_role, penalty_delta, reason,
    )

    optional_room_fields = {
        "team1": "host_team",
        "team2": "guest_team",
        "team1_overall": "host_team_overall",
        "team2_overall": "guest_team_overall",
        "team1_logo_url": "host_team_logo_url",
        "team2_logo_url": "guest_team_logo_url",
        "team1_league": "host_team_league",
        "team2_league": "guest_team_league",
    }
    for match_field, room_field in optional_room_fields.items():
        value = room.get(room_field)
        if value not in (None, ""):
            payload[match_field] = value
    return payload


def _new_match_payload(room, offender_role, penalty_delta, reason):
    """Payload tạo trận bỏ cuộc trước lúc quay đội.

    Các trường đội, overall và ``host_xp_factor`` được điền giống luồng tạo
    trận bình thường. Đây là phần còn thiếu khiến một số cấu trúc Supabase cũ
    từ chối INSERT và làm route trả về Internal Server Error.
    """
    _role, _delta, status_payload = _base_forfeit_values(
        room, offender_role, penalty_delta, reason,
    )

    host_team = str(room.get("host_team") or "Chưa quay đội")[:120]
    guest_team = str(room.get("guest_team") or "Chưa quay đội")[:120]
    host_factor = globals().get("HOST_XP_FACTOR", globals().get("HOST_WIN_FACTOR", 0.95))

    payload = {
        "player1_id": room.get("host_user_id"),
        "player2_id": room.get("guest_user_id"),
        "team1": host_team,
        "team2": guest_team,
        "team1_overall": _safe_overall(room.get("host_team_overall")),
        "team2_overall": _safe_overall(room.get("guest_team_overall")),
        "host_xp_factor": host_factor,
        **status_payload,
    }

    optional_fields = {
        "team1_logo_url": room.get("host_team_logo_url"),
        "team2_logo_url": room.get("guest_team_logo_url"),
        "team1_league": room.get("host_team_league"),
        "team2_league": room.get("guest_team_league"),
    }
    for key, value in optional_fields.items():
        if value not in (None, ""):
            payload[key] = value
    return payload


def _log_warning(message, *args):
    try:
        app.logger.warning(message, *args)
    except Exception:
        try:
            print(message % args if args else message)
        except Exception:
            pass


def record_room_forfeit_match(room, offender_role, penalty_delta, reason, event_type="manual_forfeit"):
    """Cập nhật hoặc tạo bản ghi lịch sử cho một lần bỏ cuộc.

    ``event_type`` được giữ trong chữ ký để tương thích các route hiện tại,
    nhưng không ghi vào cột SQL tùy chọn. Marker trong ``note`` đã đủ để xác
    định người bỏ cuộc và cách hiển thị lịch sử.

    Hàm luôn cô lập lỗi Supabase và trả về ``None`` thay vì ném exception ra
    route. Vì vậy lỗi ghi lịch sử phụ không thể làm nút Bỏ cuộc trả về HTTP 500.
    """
    del event_type  # Không phụ thuộc cột rp_details tùy chọn.

    if not isinstance(room, dict):
        return None
    if not room.get("host_user_id") or not room.get("guest_user_id"):
        return None

    match_id = room.get("match_id")
    try:
        if match_id:
            payload = _existing_match_payload(room, offender_role, penalty_delta, reason)
            result = execute_query(
                db.table("matches").update(payload).eq("id", match_id),
                "record_forfeit_existing_match",
                attempts=2,
            )
            cache_delete("_rz_matches_all")
            try:
                ttl_cache_delete("matches_raw")
            except Exception:
                pass
            return match_id if result is not None else None

        payload = _new_match_payload(room, offender_role, penalty_delta, reason)
        result = execute_query(
            db.table("matches").insert(payload),
            "record_forfeit_new_match",
            attempts=2,
        )
        created = (result.data or [None])[0] if result else None
        created_id = created.get("id") if isinstance(created, dict) else None

        if created_id:
            try:
                execute_query(
                    db.table("match_rooms").update({
                        "match_id": created_id,
                        "updated_at": now_iso(),
                    }).eq("id", room.get("id")),
                    "attach_forfeit_match_to_room",
                    attempts=2,
                )
            except Exception as exc:
                # Bản ghi lịch sử đã tồn tại; lỗi liên kết phòng chỉ được ghi log.
                _log_warning(
                    "Could not attach forfeit match to room %s: %s",
                    room.get("id"), exc,
                )

        cache_delete("_rz_matches_all")
        try:
            ttl_cache_delete("matches_raw")
        except Exception:
            pass
        return created_id
    except Exception as exc:
        _log_warning(
            "Could not record forfeit history for room %s; route will continue safely: %s",
            room.get("id"), exc,
        )
        return None
