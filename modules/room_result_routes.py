"""Route nhập, xác nhận, tranh chấp và rút tranh chấp kết quả phòng đấu.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

import uuid


def _result_error_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _parse_room_score(raw_value, label):
    value = str(raw_value if raw_value is not None else "").strip()
    if value == "":
        raise ValueError(f"Chưa nhập tỷ số {label}.")
    try:
        score = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Tỷ số {label} phải là số nguyên từ 0 đến 99.")
    if score < 0 or score > 99:
        raise ValueError(f"Tỷ số {label} phải nằm trong khoảng 0–99.")
    return score


def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.route("/room/<room_id>/submit-result", methods=["POST"])
    @login_required
    def room_submit_result(room_id):
        user = current_user()
        room = get_room(room_id)

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("rooms"))

        if user["id"] != room["host_user_id"] and not is_admin_user(user):
            flash("Chỉ chủ phòng mới được nhập kết quả.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        if room["status"] != "playing":
            flash("Chỉ trận đang đá mới được nhập kết quả.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        try:
            assert_ranking_rebuild_not_running()
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        try:
            host_score = _parse_room_score(request.form.get("host_score"), "Sân Nhà")
            guest_score = _parse_room_score(request.form.get("guest_score"), "Sân Khách")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        match = get_match(room["match_id"])
        if not match:
            flash("Không tìm thấy match gắn với phòng.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        if host_score > guest_score:
            winner_id = room["host_user_id"]
            loser_id = room["guest_user_id"]
        elif host_score < guest_score:
            winner_id = room["guest_user_id"]
            loser_id = room["host_user_id"]
        else:
            winner_id = None
            loser_id = None

        match_changed = False
        try:
            # Khôi phục an toàn nếu request trước đã lưu match nhưng chưa kịp đổi trạng thái phòng.
            already_waiting = (
                match.get("status") == "waiting_confirm"
                and str(match.get("score1")) == str(host_score)
                and str(match.get("score2")) == str(guest_score)
                and _same_user_id(match.get("submitted_by_id"), user.get("id"))
            )
            if not already_waiting:
                saved_match = execute_query(
                    db.table("matches").update({
                        "score1": host_score,
                        "score2": guest_score,
                        "submitted_by_id": user["id"],
                        "winner_id": winner_id,
                        "loser_id": loser_id,
                        "status": "waiting_confirm",
                        "updated_at": now_iso(),
                    }).eq("id", match["id"]).eq("status", "playing"),
                    "submit_room_match_result",
                )
                if not (saved_match.data or []):
                    fresh_match = get_match(match["id"])
                    if fresh_match and fresh_match.get("status") in {"waiting_confirm", "processing_result", "confirmed"}:
                        raise ValueError("Kết quả này đã được gửi hoặc đang được xử lý. Hãy tải lại phòng.")
                    raise ValueError("Trạng thái trận vừa thay đổi; kết quả chưa được lưu. Hãy tải lại phòng.")
                match_changed = True

            room_result = execute_query(
                db.table("match_rooms").update({
                    "host_score": host_score,
                    "guest_score": guest_score,
                    "submitted_by_id": user["id"],
                    "status": "waiting_result_confirm",
                    "state_expires_at": future_iso(RESULT_CONFIRM_TIMEOUT_SECONDS),
                    "updated_at": now_iso(),
                }).eq("id", room_id).eq("status", "playing"),
                "submit_room_result_state",
            )
            if not (room_result.data or []):
                fresh_room = get_room(room_id)
                if fresh_room and fresh_room.get("status") == "waiting_result_confirm":
                    ttl_cache_delete("rooms_raw")
                else:
                    raise RuntimeError("Không đồng bộ được trạng thái phòng sau khi lưu tỷ số.")
            ttl_cache_delete("rooms_raw")
        except ValueError as exc:
            print(f"room_submit_result validation room={room_id} match={match.get('id')}: {exc}")
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        except Exception as exc:
            error_id = _result_error_id("SCORE")
            # Nếu match vừa được đổi sang waiting_confirm nhưng phòng chưa đổi được, trả match về playing.
            if match_changed:
                try:
                    execute_query(
                        db.table("matches").update({
                            "score1": None, "score2": None, "submitted_by_id": None,
                            "winner_id": None, "loser_id": None, "status": "playing",
                            "updated_at": now_iso(),
                        }).eq("id", match["id"]).eq("status", "waiting_confirm"),
                        "rollback_submit_room_match_result", attempts=2,
                    )
                except Exception as rollback_exc:
                    print(f"{error_id} rollback ERROR match={match.get('id')}: {type(rollback_exc).__name__}: {rollback_exc}")
            print(f"{error_id} room_submit_result ERROR room={room_id} match={match.get('id')}: {type(exc).__name__}: {exc}")
            flash(f"Không thể lưu tỷ số. Mã lỗi {error_id}. Điểm chưa được xử lý; hãy thử lại sau vài giây.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        flash("Đã nhập kết quả. Đang chờ người được mời xác nhận.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/post-result-next", methods=["POST"])
    @login_required
    def room_post_result_next(room_id):
        """Cho chủ phòng rời màn hình sau khi đã gửi kết quả, không thay đổi trận/RP."""
        user = current_user()
        room = get_room(room_id)
        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("rooms"))
        if not _same_user_id(user.get("id"), room.get("host_user_id")):
            flash("Chỉ chủ phòng đã gửi kết quả mới dùng được lựa chọn này.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if room.get("status") != "waiting_result_confirm":
            flash("Chỉ có thể chọn Đá tiếp khi kết quả đang chờ Sân khách xác nhận.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        flash("Không cần chọn Đá Tiếp. Sau khi Sân khách xác nhận kết quả, phòng sẽ tự trở về Chờ Sẵn Sàng và giữ nguyên chế độ thi đấu.", "info")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/post-result-exit", methods=["POST"])
    @login_required
    def room_post_result_exit(room_id):
        """Cho chủ phòng về sảnh an toàn sau khi đã gửi kết quả."""
        user = current_user()
        room = get_room(room_id)
        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("dashboard"))
        if not _same_user_id(user.get("id"), room.get("host_user_id")):
            flash("Chỉ chủ phòng đã gửi kết quả mới dùng được lựa chọn này.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if room.get("status") not in {"waiting_result_confirm", "disputed"}:
            flash("Phòng không còn ở trạng thái chờ xác nhận hoặc tranh chấp.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        flash("Bạn đã rời phòng an toàn. Kết quả vẫn chờ xác nhận hoặc xử lý tranh chấp; không trừ RP.", "success")
        return redirect(url_for("dashboard"))


    @app.route("/room/<room_id>/confirm-result", methods=["POST"])
    @login_required
    def room_confirm_result(room_id):
        user = current_user()
        room = get_room(room_id)

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("rooms"))

        if user["id"] != room["guest_user_id"] and not is_admin_user(user):
            flash("Chỉ người được mời mới được xác nhận kết quả.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        if room["status"] != "waiting_result_confirm":
            flash("Phòng chưa có kết quả cần xác nhận.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        match = get_match(room["match_id"])
        if not match:
            flash("Không tìm thấy trận.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        try:
            try:
                users_before_streak_event = users_map()
            except Exception as exc:
                print(f"confirm streak snapshot warning room={room_id}: {type(exc).__name__}: {exc}")
                users_before_streak_event = {}
            delta1, delta2 = apply_match_result(match)
            try:
                streak_event = build_win_streak_event(match, room, users_before_streak_event)
            except Exception as exc:
                print(f"confirm streak event warning room={room_id}: {type(exc).__name__}: {exc}")
                streak_event = None
            previous_mode = room.get("team_tier") or SMART_RANDOM_MODE
            if not system_feature_enabled("rank_standard_enabled"):
                previous_mode = FRIENDLY_RANDOM3_MODE
            room_update = {
                "status": "waiting_ready",
                "guest_ready": False,
                "host_team": None,
                "guest_team": None,
                "host_team_overall": None,
                "guest_team_overall": None,
                "host_team_logo_url": None,
                "guest_team_logo_url": None,
                "host_team_league": None,
                "guest_team_league": None,
                "host_score": None,
                "guest_score": None,
                "match_id": None,
                "submitted_by_id": None,
                "confirmed_by_id": user["id"],
                "match_mode": MATCH_MODE_RANKED,
                "team_tier": previous_mode,
                "note": f"__RANK_MODE_LOCKED__|{previous_mode}",
                "state_expires_at": None,
                "updated_at": now_iso(),
            }
            room_update_result = execute_query(
                db.table("match_rooms").update(room_update).eq("id", room_id).eq("status", "waiting_result_confirm"),
                "confirm_result_reset_room_waiting_ready",
            )
            if not (room_update_result.data or []):
                raise ValueError("Trạng thái phòng vừa thay đổi; vui lòng tải lại phòng.")
            if streak_event:
                try:
                    publish_global_streak_event(streak_event)
                except Exception as exc:
                    print(f"publish streak event warning room={room_id}: {type(exc).__name__}: {exc}")
            flash("Đã xác nhận kết quả. Phòng đã trở về Chờ Sẵn Sàng và giữ nguyên chế độ thi đấu.", "success")
        except ValueError as exc:
            fresh_match = get_match(match.get("id"))
            if fresh_match and fresh_match.get("status") == "confirmed":
                error_id = _result_error_id("ROOM")
                print(f"{error_id} confirmed but room reset pending room={room_id}: {exc}")
                flash(f"Kết quả đã được ghi nhận, nhưng phòng chưa làm mới. Mã lỗi {error_id}. Hãy tải lại phòng.", "warning")
            else:
                print(f"room_confirm_result validation room={room_id} match={match.get('id')}: {exc}")
                flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        except Exception as exc:
            error_id = _result_error_id("CONFIRM")
            fresh_match = get_match(match.get("id"))
            if fresh_match and fresh_match.get("status") == "confirmed":
                print(f"{error_id} confirm completed but room reset failed room={room_id}: {type(exc).__name__}: {exc}")
                flash(f"Kết quả đã được ghi nhận, nhưng phòng chưa làm mới. Mã lỗi {error_id}. Hãy tải lại phòng.", "warning")
            else:
                print(f"{error_id} room_confirm_result ERROR room={room_id} match={match.get('id')}: {type(exc).__name__}: {exc}")
                flash(f"Không thể xác nhận kết quả. Mã lỗi {error_id}. Chưa ghi thêm điểm; hãy thử lại sau vài giây.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/dispute-result", methods=["POST"])
    @login_required
    def room_dispute_result(room_id):
        user = current_user()
        room = get_room(room_id)

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("rooms"))

        if user["id"] != room["guest_user_id"]:
            flash("Chỉ người được mời mới được báo tranh chấp.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        if room["status"] != "waiting_result_confirm":
            flash("Phòng chưa có kết quả cần xác nhận.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        try:
            assert_ranking_rebuild_not_running()
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        reason_code = request.form.get("reason_code", "").strip()
        details = request.form.get("details", "").strip()[:500]
        if reason_code not in {"wrong_score", "wrong_winner", "interrupted", "unilateral_entry", "other"}:
            flash("Hãy chọn lý do tranh chấp hợp lệ.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if reason_code == "other" and not details:
            flash("Hãy nhập ghi chú cho lý do khác.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        evidence_path = None
        evidence_file = request.files.get("evidence")
        if evidence_file and getattr(evidence_file, "filename", ""):
            try:
                evidence_bytes = prepare_dispute_evidence_bytes(evidence_file)
                evidence_path = upload_dispute_evidence(room.get("match_id"), user.get("id"), evidence_bytes)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("room_detail", room_id=room_id))
            except Exception as exc:
                print(f"room_dispute_evidence upload error: {exc}")
                flash("Không thể tải ảnh bằng chứng lúc này. Vui lòng thử lại hoặc gửi tranh chấp không kèm ảnh.", "danger")
                return redirect(url_for("room_detail", room_id=room_id))

        reason_label = dispute_reason_label(reason_code)
        note = f"{user.get('display_name', 'Khách')} không đồng ý kết quả: {reason_label}."
        disputed_match_id = room.get("match_id")
        previous_mode = room.get("team_tier") or SMART_RANDOM_MODE
        if not system_feature_enabled("rank_standard_enabled"):
            previous_mode = FRIENDLY_RANDOM3_MODE
        try:
            if disputed_match_id:
                match_update_result = execute_query(
                    db.table("matches").update({
                        "status": "disputed",
                        "note": note,
                        "updated_at": now_iso(),
                    }).eq("id", disputed_match_id).eq("status", "waiting_confirm"),
                    "match_dispute_update",
                )
                if not (match_update_result.data or []):
                    raise ValueError("Trạng thái trận vừa thay đổi; chưa thể mở tranh chấp.")

            dispute = create_or_update_match_dispute(
                room,
                user["id"],
                reason_code,
                details,
                "player",
                evidence_path=evidence_path,
            )

            # Tranh chấp chỉ thuộc về kết quả của trận cũ. Phòng được giải phóng ngay
            # để hai người tiếp tục Sẵn sàng/đá trận mới mà không chờ Admin xử lý.
            room_update_result = execute_query(
                db.table("match_rooms").update({
                    "status": "waiting_ready",
                    "guest_ready": False,
                    "host_team": None,
                    "guest_team": None,
                    "host_team_overall": None,
                    "guest_team_overall": None,
                    "host_team_logo_url": None,
                    "guest_team_logo_url": None,
                    "host_team_league": None,
                    "guest_team_league": None,
                    "host_score": None,
                    "guest_score": None,
                    "match_id": None,
                    "submitted_by_id": None,
                    "confirmed_by_id": None,
                    "match_mode": MATCH_MODE_RANKED,
                    "team_tier": previous_mode,
                    "note": f"__RANK_MODE_LOCKED__|{previous_mode}",
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                }).eq("id", room_id).eq("status", "waiting_result_confirm"),
                "room_dispute_release_room",
            )
            if not (room_update_result.data or []):
                raise ValueError("Trạng thái phòng vừa thay đổi; chưa thể giải phóng phòng.")
            ttl_cache_delete("rooms_raw")
        except Exception as exc:
            if evidence_path:
                remove_dispute_evidence_object(evidence_path)
            try:
                execute_query(
                    db.table("match_rooms").update({
                        "status": "waiting_result_confirm",
                        "match_id": disputed_match_id,
                        "host_score": room.get("host_score"),
                        "guest_score": room.get("guest_score"),
                        "submitted_by_id": room.get("submitted_by_id"),
                        "state_expires_at": future_iso(RESULT_CONFIRM_TIMEOUT_SECONDS),
                        "updated_at": now_iso(),
                    }).eq("id", room_id),
                    "rollback_room_dispute",
                    attempts=1,
                )
                if disputed_match_id:
                    execute_query(
                        db.table("matches").update({
                            "status": "waiting_confirm",
                            "updated_at": now_iso(),
                        }).eq("id", disputed_match_id),
                        "rollback_match_dispute",
                        attempts=1,
                    )
            except Exception as rollback_exc:
                print(f"room_dispute rollback warning: {rollback_exc}")
            print(f"room_dispute create error: {exc}")
            flash("Không thể gửi tranh chấp lúc này. Vui lòng thử lại sau vài giây.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        notify_admins(
            "⚠️ Có tranh chấp kết quả mới",
            f"{room.get('host_name')} {room.get('host_score')} - {room.get('guest_score')} {room.get('guest_name')} • {reason_label}",
        )
        create_user_notification(
            room.get("host_user_id"),
            "⚠️ Đối thủ đã mở tranh chấp",
            f"{room.get('guest_name')} không đồng ý kết quả. Lý do: {reason_label}.",
            f"/room/{room_id}",
            "dispute",
        )

        flash("Đã gửi tranh chấp. Trận cũ chưa tính RP; phòng đã trở về Chờ Sẵn Sàng để hai người tiếp tục thi đấu.", "warning")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/withdraw-dispute", methods=["POST"])
    @login_required
    def room_withdraw_dispute(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room or room.get("status") != "disputed":
            flash("Phòng không còn tranh chấp cần rút.", "warning")
            return redirect(url_for("dashboard"))

        dispute = get_match_dispute_by_match(room.get("match_id"), DISPUTE_PENDING_STATUSES)
        if not dispute or dispute.get("raised_by_id") != user.get("id"):
            flash("Chỉ người đã gửi tranh chấp mới có thể rút tranh chấp.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        try:
            resolve_match_dispute_with_result(
                dispute,
                room.get("host_score"),
                room.get("guest_score"),
                user.get("id"),
                "accepted_by_player",
                "Người gửi đã rút tranh chấp và chấp nhận kết quả ban đầu.",
                final_dispute_status="withdrawn",
            )
        except Exception as exc:
            flash(f"Không thể rút tranh chấp: {exc}", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        flash("Đã rút tranh chấp và chấp nhận kết quả. Điểm rank đã được cập nhật.", "success")
        return redirect(url_for("room_detail", room_id=room_id))

