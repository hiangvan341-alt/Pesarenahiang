"""Route quay đội, quay lại giao hữu, kết thúc giao hữu và trạng thái sẵn sàng.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.route("/room/<room_id>/random-teams", methods=["POST"])
    @login_required
    def room_random_teams(room_id):
        user = current_user()
        room = get_room(room_id)

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("rooms"))
        if user["id"] != room["host_user_id"] and not is_admin_user(user):
            flash("Chỉ chủ phòng mới được quay đội.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if room["status"] != "waiting_ready":
            flash("Phòng không còn ở bước chờ quay đội.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if not room.get("guest_user_id"):
            flash("Phòng chưa có đối thủ. Hãy mời một người chơi vào phòng.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if not room.get("guest_ready"):
            flash("Đội khách chưa sẵn sàng. Hãy chờ khách bấm Sẵn sàng.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if decode_friendly_random3_state(room.get("note")):
            flash("Phòng đang ở bước Random 3 chọn 1. Hãy hoàn tất lựa chọn hiện tại.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if room.get("match_id") or room.get("host_team") or room.get("guest_team"):
            flash("Phòng đã được quay đội hoặc đã tạo trận.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        match_mode = (request.form.get("match_mode") or MATCH_MODE_RANKED).strip().lower()
        if match_mode == MATCH_MODE_FRIENDLY and not system_feature_enabled("friendly_enabled"):
            flash("Tính năng Giao hữu đang tạm tắt.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if match_mode not in {MATCH_MODE_RANKED, MATCH_MODE_FRIENDLY}:
            match_mode = MATCH_MODE_RANKED

        host = get_user(room["host_user_id"])
        guest = get_user(room["guest_user_id"])
        if not host or not guest:
            flash("Không tải được thông tin hai người chơi.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        if match_mode == MATCH_MODE_RANKED and not system_feature_enabled("rank_standard_enabled"):
            execute_query(
                db.table("match_rooms").update({
                    "team_tier": FRIENDLY_RANDOM3_MODE,
                    "friendly_tier": None,
                    "note": "Rank thường đang tắt. Hãy bắt đầu Random 3 chọn 1.",
                    "updated_at": now_iso(),
                }).eq("id", room_id).eq("status", "waiting_ready"),
                "force_disabled_rank_room_to_random3",
            )
            flash("Rank thường đang tắt. Phòng đã chuyển sang Random 3 chọn 1.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        if match_mode == MATCH_MODE_RANKED:
            try:
                assert_can_start_ranked_match(host.get("id"), guest.get("id"))
            except ValueError as exc:
                flash(str(exc), "warning")
                return redirect(url_for("room_detail", room_id=room_id))

        try:
            if match_mode == MATCH_MODE_FRIENDLY:
                selected_tier = (request.form.get("friendly_tier") or room.get("friendly_tier") or "A").strip().upper()
                result = friendly_random_team_pair(selected_tier)
                execute_query(
                    db.table("match_rooms").update({
                        "host_team": result["team_a"],
                        "guest_team": result["team_b"],
                        "host_team_overall": result["overall_a"],
                        "guest_team_overall": result["overall_b"],
                        "host_team_logo_url": result.get("logo_a") or None,
                        "guest_team_logo_url": result.get("logo_b") or None,
                        "host_team_league": result.get("league_a") or None,
                        "guest_team_league": result.get("league_b") or None,
                        "team_tier": selected_tier,
                        "friendly_tier": selected_tier,
                        "match_mode": MATCH_MODE_FRIENDLY,
                        "status": "friendly_playing",
                        "match_id": None,
                        "note": f"Giao hữu Tier {selected_tier}; không lưu lịch sử và không tính RP.",
                        "state_expires_at": None,
                        "updated_at": now_iso(),
                    }).eq("id", room_id).eq("status", "waiting_ready"),
                    "room_friendly_random",
                )
                flash(
                    f'Giao hữu Tier {selected_tier}: {result["team_a"]} ({result.get("league_a") or "Không rõ giải"}) vs '
                    f'{result["team_b"]} ({result.get("league_b") or "Không rõ giải"}). Không lưu lịch sử, không tính điểm.',
                    "success",
                )
                return redirect(url_for("room_detail", room_id=room_id))

            result = smart_random_team_pair(host, guest)
            match_result = execute_query(
                db.table("matches").insert({
                    "player1_id": room["host_user_id"],
                    "player2_id": room["guest_user_id"],
                    "team1": result["team_a"],
                    "team2": result["team_b"],
                    "team1_overall": result["overall_a"],
                    "team2_overall": result["overall_b"],
                    "team1_logo_url": result.get("logo_a") or None,
                    "team2_logo_url": result.get("logo_b") or None,
                    "team1_league": result.get("league_a") or None,
                    "team2_league": result.get("league_b") or None,
                    "host_xp_factor": HOST_XP_FACTOR,
                    "status": "playing",
                    "note": "",
                    "updated_at": now_iso(),
                }),
                "room_random_create_match",
            )
            match = match_result.data[0] if match_result.data else None
            if not match:
                flash("Không thể tạo trận sau khi quay đội. Vui lòng thử lại.", "danger")
                return redirect(url_for("room_detail", room_id=room_id))

            execute_query(
                db.table("match_rooms").update({
                    "host_team": result["team_a"],
                    "guest_team": result["team_b"],
                    "host_team_overall": result["overall_a"],
                    "guest_team_overall": result["overall_b"],
                    "host_team_logo_url": result.get("logo_a") or None,
                    "guest_team_logo_url": result.get("logo_b") or None,
                    "host_team_league": result.get("league_a") or None,
                    "guest_team_league": result.get("league_b") or None,
                    "team_tier": SMART_RANDOM_MODE,
                    "match_mode": MATCH_MODE_RANKED,
                    "status": "playing",
                    "match_id": match["id"],
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                }).eq("id", room_id).eq("status", "waiting_ready"),
                "room_random_start_match",
            )
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/select-ranked-mode", methods=["POST"])
    @login_required
    def room_select_ranked_mode(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room or (user["id"] != room.get("host_user_id") and not is_admin_user(user)):
            flash("Chỉ chủ phòng mới được chọn chế độ thi đấu.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if room.get("status") != "waiting_ready" or room.get("match_id") or room.get("host_team") or room.get("guest_team"):
            flash("Không thể đổi chế độ ở trạng thái hiện tại.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if "__RANK_MODE_LOCKED__" in (room.get("note") or ""):
            flash("Lượt đá tiếp giữ nguyên chế độ của trận trước, không cần chọn lại.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        selected_mode = (request.form.get("rank_mode") or SMART_RANDOM_MODE).strip()
        if not system_feature_enabled("rank_standard_enabled"):
            selected_mode = FRIENDLY_RANDOM3_MODE
        if selected_mode == FRIENDLY_RANDOM3_MODE:
            if not system_feature_enabled("friendly_random3_enabled"):
                flash("Chế độ Random 3 chọn 1 đang tạm tắt.", "warning")
                return redirect(url_for("room_detail", room_id=room_id))
            label = "Random 3 chọn 1"
        else:
            selected_mode = SMART_RANDOM_MODE
            label = "Rank thường"
        execute_query(
            db.table("match_rooms").update({
                "match_mode": MATCH_MODE_RANKED,
                "team_tier": selected_mode,
                "friendly_tier": None,
                "note": f"Chủ phòng đã chọn chế độ {label}. Chờ khách Sẵn sàng.",
                "updated_at": now_iso(),
            }).eq("id", room_id).eq("status", "waiting_ready"),
            "select_ranked_room_mode",
        )
        flash(f"Đã chọn chế độ {label}.", "success")
        return redirect(url_for("room_detail", room_id=room_id))

    @app.route("/room/<room_id>/start-random3-friendly", methods=["POST"])
    @login_required
    def room_start_random3_friendly(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room or (user["id"] != room.get("host_user_id") and not is_admin_user(user)):
            flash("Chỉ chủ phòng mới được mở chế độ này.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if room.get("status") != "waiting_ready" or not room.get("guest_user_id") or not room.get("guest_ready"):
            flash("Cần đủ hai người và khách đã Sẵn sàng.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if not system_feature_enabled("friendly_random3_enabled"):
            flash("Chế độ Random 3 chọn 1 đang tạm tắt.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        host = get_user(room.get("host_user_id"))
        guest = get_user(room.get("guest_user_id"))
        try:
            assert_can_start_ranked_match(room.get("host_user_id"), room.get("guest_user_id"))
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        try:
            state = build_friendly_random3_state(host, guest)
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        execute_query(db.table("match_rooms").update({"match_mode": MATCH_MODE_RANKED, "friendly_tier": None, "team_tier": FRIENDLY_RANDOM3_MODE, "note": encode_friendly_random3_state(state), "updated_at": now_iso()}).eq("id", room_id).eq("status", "waiting_ready"), "start_random3_ranked")
        flash("Đã random 3 CLB theo mức Rank riêng của mỗi người. Hãy chọn 1 CLB.", "success")
        return redirect(url_for("room_detail", room_id=room_id))

    @app.route("/room/<room_id>/choose-random3-friendly", methods=["POST"])
    @login_required
    def room_choose_random3_friendly(room_id):
        user = current_user()
        room = get_room(room_id)
        state = decode_friendly_random3_state(room.get("note") if room else None)
        if not system_feature_enabled("friendly_random3_enabled"):
            flash("Chế độ Random 3 chọn 1 đang tạm tắt.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if not room or not state or room.get("status") != "waiting_ready":
            flash("Lượt chọn CLB không còn hiệu lực.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        side = "host" if user["id"] == room.get("host_user_id") else "guest" if user["id"] == room.get("guest_user_id") else None
        if not side and not is_admin_user(user):
            flash("Bạn không thuộc phòng này.", "danger")
            return redirect(url_for("rooms"))
        side = side or (request.form.get("side") or "host")
        try: idx = int(request.form.get("choice_index", -1))
        except Exception: idx = -1
        options = state.get(f"{side}_options") or []
        if idx not in range(len(options)):
            flash("Lựa chọn CLB không hợp lệ.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        state[f"{side}_choice"] = idx
        update = {"note": encode_friendly_random3_state(state), "updated_at": now_iso()}
        match = None
        if state.get("host_choice") is not None and state.get("guest_choice") is not None:
            try:
                assert_can_start_ranked_match(room.get("host_user_id"), room.get("guest_user_id"))
            except ValueError as exc:
                flash(str(exc), "warning")
                return redirect(url_for("room_detail", room_id=room_id))
            h = state["host_options"][state["host_choice"]]
            g = state["guest_options"][state["guest_choice"]]
            match_result = execute_query(
                db.table("matches").insert({
                    "player1_id": room["host_user_id"],
                    "player2_id": room["guest_user_id"],
                    "team1": h["name"],
                    "team2": g["name"],
                    "team1_overall": h["overall"],
                    "team2_overall": g["overall"],
                    "team1_logo_url": h["logo"] or None,
                    "team2_logo_url": g["logo"] or None,
                    "team1_league": h["league"] or None,
                    "team2_league": g["league"] or None,
                    "host_xp_factor": HOST_XP_FACTOR,
                    "status": "playing",
                    "note": "Random 3 chọn 1 - trận xếp hạng tính RP.",
                    "updated_at": now_iso(),
                }),
                "create_random3_ranked_match",
            )
            match = match_result.data[0] if match_result.data else None
            if not match:
                flash("Không thể tạo trận Random 3 chọn 1. Vui lòng thử lại.", "danger")
                return redirect(url_for("room_detail", room_id=room_id))
            update.update({
                "host_team": h["name"], "guest_team": g["name"],
                "host_team_overall": h["overall"], "guest_team_overall": g["overall"],
                "host_team_logo_url": h["logo"] or None, "guest_team_logo_url": g["logo"] or None,
                "host_team_league": h["league"] or None, "guest_team_league": g["league"] or None,
                "status": "playing", "match_id": match["id"], "match_mode": MATCH_MODE_RANKED,
                "team_tier": FRIENDLY_RANDOM3_MODE,
                "note": "Random 3 chọn 1 - trận xếp hạng tính RP.",
                "state_expires_at": None,
            })
        result = execute_query(db.table("match_rooms").update(update).eq("id", room_id).eq("status", "waiting_ready"), "choose_random3_ranked")
        if match and not (result.data or []):
            execute_query(db.table("matches").delete().eq("id", match["id"]).eq("status", "playing"), "rollback_random3_ranked_match", attempts=1)
            flash("Trạng thái phòng vừa thay đổi. Trận chưa được bắt đầu.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        flash("Đã khóa lựa chọn của bạn." if update.get("status") != "playing" else "Cả hai đã chọn xong. Trận tính RP bắt đầu!", "success")
        return redirect(url_for("room_detail", room_id=room_id))

    @app.route("/room/<room_id>/reroll-friendly", methods=["POST"])
    @login_required
    def room_reroll_friendly(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("dashboard"))
        required_feature = "friendly_random3_enabled" if room.get("team_tier") == FRIENDLY_RANDOM3_MODE else "friendly_enabled"
        if not system_feature_enabled(required_feature):
            flash("Chế độ giao hữu này đang tạm tắt.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if user["id"] != room.get("host_user_id") and not is_admin_user(user):
            flash("Chỉ chủ phòng mới được quay lại đội giao hữu.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))
        if room.get("status") != "friendly_playing":
            flash("Phòng không có trận giao hữu đang diễn ra.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        selected_tier = (room.get("friendly_tier") or "A").strip().upper()
        try:
            result = friendly_random_team_pair(
                selected_tier,
                excluded_names=[room.get("host_team"), room.get("guest_team")],
            )
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        execute_query(
            db.table("match_rooms").update({
                "host_team": result["team_a"],
                "guest_team": result["team_b"],
                "host_team_overall": result["overall_a"],
                "guest_team_overall": result["overall_b"],
                "host_team_logo_url": result.get("logo_a") or None,
                "guest_team_logo_url": result.get("logo_b") or None,
                "host_team_league": result.get("league_a") or None,
                "guest_team_league": result.get("league_b") or None,
                "note": f"Đã quay lại đội giao hữu Tier {selected_tier}.",
                "updated_at": now_iso(),
            }).eq("id", room_id).eq("status", "friendly_playing"),
            "reroll_friendly_match",
        )
        flash("Đã tự random tiếp hai CLB giao hữu.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/finish-friendly", methods=["POST"])
    @login_required
    def room_finish_friendly(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("dashboard"))
        required_feature = "friendly_random3_enabled" if room.get("team_tier") == FRIENDLY_RANDOM3_MODE else "friendly_enabled"
        if not system_feature_enabled(required_feature):
            flash("Chế độ giao hữu này đang tạm tắt.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        if user["id"] not in [room.get("host_user_id"), room.get("guest_user_id")] and not is_admin_user(user):
            flash("Bạn không thuộc phòng này.", "danger")
            return redirect(url_for("dashboard"))
        if room.get("status") != "friendly_playing":
            flash("Phòng không có trận giao hữu đang diễn ra.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        execute_query(
            db.table("match_rooms").update({
                "host_team": None,
                "guest_team": None,
                "host_team_overall": None,
                "guest_team_overall": None,
                "host_team_logo_url": None,
                "guest_team_logo_url": None,
                "host_team_league": None,
                "guest_team_league": None,
                "guest_ready": bool(room.get("guest_user_id")),
                "status": "waiting_ready",
                "match_id": None,
                "note": "Trận giao hữu đã kết thúc. Đang chờ Chủ Phòng quay đội tiếp theo.",
                "updated_at": now_iso(),
            }).eq("id", room_id).eq("status", "friendly_playing"),
            "finish_friendly_match",
        )
        flash("Đã kết thúc giao hữu. Không lưu lịch sử và không thay đổi RP.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/guest-unready", methods=["POST"])
    @login_required
    def room_guest_unready(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room or user.get("id") != room.get("guest_user_id"):
            flash("Bạn không thuộc phòng đấu này.", "danger")
            return redirect(url_for("dashboard"))
        if room.get("status") != "waiting_ready":
            flash("Không thể hủy sẵn sàng ở trạng thái hiện tại.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        execute_query(
            db.table("match_rooms").update({
                "guest_ready": False,
                "note": "Khách đã hủy sẵn sàng.",
            }).eq("id", room_id).eq("status", "waiting_ready"),
            "room_guest_unready",
        )
        cache_delete("_rz_rooms_all")
        ttl_cache_delete("rooms_raw")
        flash("Đã hủy trạng thái sẵn sàng.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/guest-ready", methods=["POST"])
    @login_required
    def room_guest_ready(room_id):
        user = current_user()
        room = get_room(room_id)
        if not room or user.get("id") != room.get("guest_user_id"):
            flash("Bạn không thuộc phòng đấu này.", "danger")
            return redirect(url_for("dashboard"))
        if room.get("status") != "waiting_ready":
            flash("Không thể đổi trạng thái sẵn sàng lúc này.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        limit_message = daily_rank_block_message(room.get("host_user_id"), room.get("guest_user_id"))
        if limit_message:
            # Đảm bảo phòng không mắc kẹt ở trạng thái đã cam kết thi đấu.
            execute_query(
                db.table("match_rooms").update({
                    "guest_ready": False,
                    "note": "Đã chạm giới hạn trận Rank trong ngày. Có thể rời phòng không bị trừ RP.",
                    "updated_at": now_iso(),
                }).eq("id", room_id).eq("status", "waiting_ready"),
                "block_guest_ready_daily_limit",
                attempts=2,
            )
            flash(limit_message, "warning")
            return redirect(url_for("room_detail", room_id=room_id))
        execute_query(
            db.table("match_rooms").update({
                "guest_ready": True,
                "note": "Khách đã sẵn sàng. Chủ phòng có thể quay đội.",
            }).eq("id", room_id).eq("status", "waiting_ready"),
            "room_guest_ready",
        )
        cache_delete("_rz_rooms_all")
        ttl_cache_delete("rooms_raw")
        flash("Bạn đã sẵn sàng.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/start", methods=["POST"])
    @login_required
    def room_start(room_id):
        # Giữ endpoint để tương thích với trang cũ đang được cache.
        flash("V1.10.0 đã bỏ nút Sẵn sàng và Bắt đầu trận. Chủ phòng chỉ cần quay đội.", "warning")
        return redirect(url_for("room_detail", room_id=room_id))

