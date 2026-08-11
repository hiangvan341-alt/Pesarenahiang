"""Route truy cập phòng: danh sách phòng, tham gia bằng link, xem phòng và rời phòng.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.route("/rooms")
    @login_required
    def rooms():
        user = current_user()
        all_rooms = list_rooms()
        my_rooms = [r for r in all_rooms if user["id"] in [r["host_user_id"], r["guest_user_id"]]]
        return render_template("rooms.html", rooms=my_rooms)


    @app.route("/room/join/<room_id>", methods=["GET"])
    def room_join_shared(room_id):
        """Cho phép một tài khoản tham gia phòng trống từ link được chia sẻ.

        Route này tự ghi nhớ phòng nếu người mở link chưa đăng nhập. Việc nhận chỗ
        khách được cập nhật có điều kiện để hai người bấm cùng lúc không thể cùng
        chiếm một phòng.
        """
        if not session.get("user_id"):
            session["pending_room_join_id"] = str(room_id)
            flash("Hãy đăng nhập để tham gia phòng đấu được chia sẻ.", "warning")
            return redirect(url_for("login"))

        user = current_user()
        if not user:
            session.clear()
            session["pending_room_join_id"] = str(room_id)
            flash("Phiên đăng nhập không hợp lệ. Hãy đăng nhập lại để vào phòng.", "warning")
            return redirect(url_for("login"))

        if user.get("account_status", "approved") != "approved":
            session.clear()
            flash("Tài khoản chưa được phép tham gia phòng đấu.", "danger")
            return redirect(url_for("login"))

        try:
            room = get_room(room_id)
        except Exception as exc:
            app.logger.warning("Shared room join load failed room=%s: %s", room_id, exc)
            flash("Phòng đang tải chậm. Vui lòng mở lại link sau vài giây.", "warning")
            return redirect(url_for("dashboard"))

        if not room:
            flash("Link phòng không còn tồn tại hoặc phòng đã bị xóa.", "danger")
            return redirect(url_for("dashboard"))

        user_id = user.get("id")
        if user_id in {room.get("host_user_id"), room.get("guest_user_id")} or is_admin_user(user):
            return redirect(url_for("room_detail", room_id=room_id))

        if room.get("status") != "waiting_ready":
            flash("Phòng đã bắt đầu hoặc không còn nhận người tham gia.", "warning")
            return redirect(url_for("dashboard"))

        if room.get("guest_user_id"):
            flash("Phòng đã có đủ hai người chơi.", "warning")
            return redirect(url_for("dashboard"))

        if is_player_in_cooldown(user):
            flash(f"Bạn đang trong thời gian chờ {cooldown_text(user)}.", "warning")
            return redirect(url_for("dashboard"))

        limit_message = daily_rank_block_message(room.get("host_user_id"), user_id)
        if limit_message:
            flash(limit_message, "warning")
            return redirect(url_for("dashboard"))

        existing_room = active_room_for_user(user_id)
        if existing_room:
            flash("Bạn đang có một phòng chưa hoàn tất. Hãy xử lý phòng đó trước.", "warning")
            return redirect(url_for("room_detail", room_id=existing_room.get("id")))

        if active_match_for_user(user_id):
            flash("Bạn đang có trận chưa hoàn tất nên chưa thể vào phòng khác.", "warning")
            return redirect(url_for("dashboard"))

        host_id = room.get("host_user_id")
        host_other_room = active_room_for_user(host_id, exclude_room_id=room_id)
        if active_match_for_user(host_id) or host_other_room:
            flash("Chủ phòng đang ở một phòng hoặc trận khác. Link này không còn hiệu lực.", "warning")
            return redirect(url_for("dashboard"))

        joined_at = now_iso()
        update_result = execute_query(
            db.table("match_rooms").update({
                "invite_id": None,
                "guest_user_id": user_id,
                "guest_ready": False,
                "guest_team": None,
                "guest_team_overall": None,
                "guest_team_logo_url": None,
                "guest_team_league": None,
                "note": f'{user.get("display_name") or user.get("username") or "Người chơi"} đã tham gia qua link chia sẻ. Khách chưa sẵn sàng.',
                "state_expires_at": None,
                "updated_at": joined_at,
            })
            .eq("id", room_id)
            .eq("status", "waiting_ready")
            .is_("guest_user_id", "null"),
            "join_shared_room",
            attempts=3,
        )

        joined_rows = update_result.data or []
        if not joined_rows:
            latest_room = get_room(room_id)
            if latest_room and latest_room.get("guest_user_id") == user_id:
                return redirect(url_for("room_detail", room_id=room_id))
            flash("Có người khác vừa tham gia trước bạn hoặc phòng đã thay đổi trạng thái.", "warning")
            return redirect(url_for("dashboard"))

        # Link chia sẻ có thể được dùng khi chủ phòng từng gửi lời mời riêng.
        # Hủy lời mời đang treo để người được mời cũ không thể nhận chỗ lần nữa.
        old_invite_id = room.get("invite_id")
        if old_invite_id:
            try:
                execute_query(
                    db.table("match_invites").update({
                        "status": "cancelled",
                        "updated_at": joined_at,
                    }).eq("id", old_invite_id).eq("status", "pending"),
                    "cancel_invite_after_shared_join",
                    attempts=2,
                )
            except Exception as exc:
                app.logger.warning("Shared room stale invite cleanup failed invite=%s: %s", old_invite_id, exc)

        cache_delete("_rz_rooms_all")
        cache_delete("_rz_invites_all")
        cache_delete("_rz_current_pending_invites")
        ttl_cache_delete("rooms_raw")
        ttl_cache_delete("invites_raw")

        flash("Bạn đã tham gia phòng qua link chia sẻ. Hãy bấm Sẵn Sàng khi đã chuẩn bị xong.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    def build_room_template_context(room):
        viewer = current_user() or {}
        daily_limit_message = None
        if room.get("status") == "waiting_ready" and room.get("match_mode") != MATCH_MODE_FRIENDLY:
            daily_limit_message = daily_rank_block_message(
                room.get("host_user_id"), room.get("guest_user_id")
            )
        return {
            "room": room,
            "initial_room_state_key": build_room_state_key(room),
            "friendly_tiers": get_available_team_tiers(),
            "room_head_to_head": build_room_head_to_head(room),
            # Luôn truyền cấu hình Tìm Nhanh vào cả trang đầy đủ và HTML polling.
            # Nếu thiếu, Jinja dùng màu mặc định và có thể không phản ánh lựa chọn Admin.
            "quick_match_config": get_quick_match_config(),
            "daily_rank_limit_blocked": bool(daily_limit_message),
            "daily_rank_limit_message": daily_limit_message,
            # Supabase có thể trả ID ở kiểu khác session. Dùng so sánh chuẩn hóa
            # để giao diện không làm mất nút hành động của chủ/khách.
            "viewer_is_host": _same_user_id(viewer.get("id"), room.get("host_user_id")),
            "viewer_is_guest": _same_user_id(viewer.get("id"), room.get("guest_user_id")),
            "parsec_room": build_room_parsec_context(room, viewer),
        }


    @app.route("/room/<room_id>")
    @login_required

    def room_detail(room_id):
        user = current_user()

        try:
            room = get_room(room_id)
        except Exception:
            flash("Phòng đang tải chậm hoặc Supabase vừa ngắt kết nối. Vui lòng thử lại sau vài giây.", "warning")
            return redirect(url_for("rooms"))

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("rooms"))

        if close_room_if_host_browser_offline(room):
            flash("Chủ phòng đã Offline nên phòng được đóng. Khách không bị ảnh hưởng.", "warning")
            return redirect(url_for("rooms"))

        if user["id"] not in [room["host_user_id"], room["guest_user_id"]] and not is_admin_user(user):
            flash("Bạn không thuộc phòng này.", "danger")
            return redirect(url_for("rooms"))

        return render_template("room_detail.html", **build_room_template_context(room))


    @app.route("/api/room/<room_id>/view")
    @login_required
    def api_room_view(room_id):
        """HTML động của phòng, chỉ tải khi state_key thật sự thay đổi."""
        user = current_user()
        try:
            room = get_room(room_id)
        except Exception:
            return "", 503

        if not room:
            return "", 404
        if close_room_if_host_browser_offline(room):
            response = make_response("", 204)
            response.headers["X-PES-Polling-Stop"] = "host_browser_offline"
            return response
        is_room_member = (
            _same_user_id(user.get("id"), room.get("host_user_id"))
            or _same_user_id(user.get("id"), room.get("guest_user_id"))
        )
        if not is_room_member and not is_admin_user(user):
            return "", 403

        response = make_response(
            render_template("_room_live_content.html", **build_room_template_context(room))
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["X-PES-Room-Partial"] = "1"
        return response


    @app.route("/room/<room_id>/kick-guest", methods=["POST"], endpoint="room_kick_guest")
    @login_required
    def room_kick_guest(room_id):
        """Cho phép chủ phòng đưa khách ra khỏi phòng trước khi trận bắt đầu."""
        user = current_user()
        room = get_room(room_id)

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("dashboard"))

        if not _same_user_id(user.get("id"), room.get("host_user_id")):
            flash("Chỉ chủ phòng mới có thể đưa đối thủ ra khỏi phòng.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        if room.get("status") != "waiting_ready":
            flash("Chỉ có thể đưa đối thủ ra khi trận chưa bắt đầu.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        # Phòng đã liên kết match tuyệt đối không được dùng thao tác kích khách,
        # kể cả khi status bị sai do dữ liệu cũ, để tránh làm mồ côi trận/RP.
        if room.get("match_id"):
            flash("Phòng đã có trận đấu liên kết nên không thể đưa người chơi ra.", "danger")
            return redirect(url_for("room_detail", room_id=room_id))

        guest_id = room.get("guest_user_id")
        if not guest_id:
            flash("Phòng hiện chưa có đối thủ.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        guest = get_user(guest_id) or {}
        guest_name = guest.get("display_name") or guest.get("username") or "Đối thủ"
        host_name = user.get("display_name") or user.get("username") or "Chủ phòng"
        old_invite_id = room.get("invite_id")
        updated_at = now_iso()
        result = execute_query(
            db.table("match_rooms").update({
                "guest_user_id": None,
                "guest_ready": False,
                "guest_team": None,
                "guest_team_overall": None,
                "guest_team_logo_url": None,
                "guest_team_league": None,
                "host_team": None,
                "host_team_overall": None,
                "host_team_logo_url": None,
                "host_team_league": None,
                "match_id": None,
                "invite_id": None,
                "status": "waiting_ready",
                "note": f"{guest_name} đã được chủ phòng đưa ra khỏi phòng.",
                "state_expires_at": None,
                "updated_at": updated_at,
            })
            .eq("id", room_id)
            .eq("host_user_id", user.get("id"))
            .eq("status", "waiting_ready")
            .eq("guest_user_id", guest_id),
            "host_kick_room_guest",
            attempts=2,
        )

        if not (result.data or []):
            flash("Phòng vừa thay đổi trạng thái. Vui lòng tải lại và kiểm tra.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        # Đóng lời mời gắn với lượt vào phòng này. Trước đây chỉ xóa invite_id
        # trong room nên lời mời có thể vẫn hiện là đang xử lý ở nơi khác.
        if old_invite_id:
            try:
                execute_query(
                    db.table("match_invites").update({
                        "status": "cancelled",
                        "updated_at": updated_at,
                    }).eq("id", old_invite_id),
                    "cancel_invite_after_host_kick",
                    attempts=2,
                )
            except Exception as exc:
                app.logger.warning("Kick guest invite cleanup failed room=%s invite=%s: %s", room_id, old_invite_id, exc)

        try:
            create_user_notification(
                guest_id,
                "Bạn đã bị đưa khỏi phòng đấu",
                f"{host_name} đã đưa bạn ra khỏi phòng #{room.get('room_code') or str(room_id)[:6].upper()}. Trận chưa bắt đầu nên bạn không bị trừ RP.",
                url_for("players"),
                "system",
            )
        except Exception as exc:
            app.logger.warning("Kick guest notification failed room=%s guest=%s: %s", room_id, guest_id, exc)

        cache_delete("_rz_rooms_all")
        cache_delete("_rz_invites_all")
        cache_delete("_rz_current_pending_invites")
        ttl_cache_delete("rooms_raw")
        ttl_cache_delete("invites_raw")

        flash(f"Đã đưa {guest_name} ra khỏi phòng. Không ai bị trừ RP và bạn có thể mời đối thủ khác.", "success")
        return redirect(url_for("room_detail", room_id=room_id))


    @app.route("/room/<room_id>/leave", methods=["POST"])
    @login_required
    def room_leave(room_id):
        user = current_user()
        room = get_room(room_id)

        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect(url_for("dashboard"))

        if user["id"] not in [room.get("host_user_id"), room.get("guest_user_id")]:
            flash("Bạn không thuộc phòng này.", "danger")
            return redirect(url_for("dashboard"))

        if room.get("status") not in {"waiting_ready", "friendly_playing"}:
            flash("Không thể rời phòng khi trận xếp hạng đang thi đấu hoặc đang chờ xác nhận kết quả.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        # Nếu một trong hai người đã chạm giới hạn Rank ngày thì phòng không còn
        # được phép bắt đầu trận mới. Mọi người được rời phòng an toàn, kể cả khi
        # giao diện cũ vẫn còn guest_ready=true, để tránh bị trừ RP oan.
        daily_limit_blocked = bool(
            room.get("status") == "waiting_ready"
            and daily_rank_block_message(room.get("host_user_id"), room.get("guest_user_id"))
        )
        if daily_limit_blocked:
            room["guest_ready"] = False

        # Ở bước chờ Sẵn Sàng, luồng rời phòng không phạt chỉ hợp lệ khi khách
        # chưa Sẵn Sàng. Kiểm tra tại backend để không thể né phạt bằng POST
        # trực tiếp vào endpoint /leave hoặc do giao diện vừa bị thay đổi trạng thái.
        if room.get("status") == "waiting_ready" and bool(room.get("guest_ready")):
            if user["id"] == room.get("guest_user_id"):
                flash("Bạn đã Sẵn Sàng. Thoát lúc này được tính là bỏ cuộc và trừ 20 RP.", "warning")
            else:
                flash("Khách đã Sẵn Sàng. Chủ phòng thoát lúc này được tính là bỏ cuộc và trừ 20 RP.", "warning")
            return redirect(url_for("room_detail", room_id=room_id))

        if user["id"] == room.get("guest_user_id"):
            execute_query(
                db.table("match_rooms").update({
                    "guest_user_id": None,
                    "guest_ready": False,
                    "guest_team": None,
                    "guest_team_overall": None,
                    "guest_team_logo_url": None,
                    "host_team": None,
                    "host_team_overall": None,
                    "host_team_logo_url": None,
                    "host_team_league": None,
                    "guest_team_league": None,
                    "status": "waiting_ready",
                    "match_id": None,
                    "invite_id": None,
                    "note": f'{user["display_name"]} đã rời phòng. Chủ phòng có thể mời đối thủ khác.',
                    "state_expires_at": None,
                    "updated_at": now_iso(),
                }).eq("id", room_id),
                "guest_leave_keep_room",
            )
            flash("Bạn đã rời phòng. Phòng vẫn được giữ cho chủ phòng và không ảnh hưởng điểm rank.", "success")
            return redirect(url_for("dashboard"))

        execute_query(
            db.table("match_rooms").update({
                "status": "cancelled",
                "guest_ready": False,
                "note": f'{user["display_name"]} đã đóng phòng.',
                "state_expires_at": None,
                "updated_at": now_iso(),
            }).eq("id", room_id),
            "host_close_room",
        )
        flash("Bạn đã thoát và đóng phòng đấu.", "success")
        return redirect(url_for("dashboard"))

