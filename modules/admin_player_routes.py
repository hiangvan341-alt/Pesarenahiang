"""Route Admin cập nhật, reset và xóa người chơi.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.route("/admin/presence-mode", methods=["POST"])
    @login_required
    @admin_required
    def admin_presence_mode():
        """Cho phép riêng Admin tự chọn hiển thị Online hoặc Offline."""
        mode = (request.form.get("presence_mode") or "online").strip().lower()
        if mode not in {"online", "offline"}:
            mode = "online"

        actor = current_user()
        session["admin_presence_mode"] = mode
        session.modified = True
        db.table("users").update({
            "is_online": mode == "online",
            "last_seen_at": now_iso(),
        }).eq("id", actor["id"]).execute()
        ttl_cache_delete("players_raw", f"user:{actor['id']}")
        cache_delete("_rz_players_all")
        cache_delete("_rz_current_user")
        log_admin_action(
            "Chọn trạng thái hiện diện",
            "user",
            actor["id"],
            actor.get("username"),
            f"presence_mode: {mode}",
        )
        flash(
            "Đã chuyển trạng thái Admin sang Online." if mode == "online" else "Đã ẩn trạng thái Admin (Offline).",
            "success",
        )
        next_url = request.form.get("next_url") or request.referrer or url_for("admin")
        return redirect(next_url)


    @app.route("/admin/player/<user_id>/duplicate-ip-trust", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("users_edit")
    def admin_toggle_duplicate_ip_trust(user_id):
        player = get_user(user_id)
        if not player:
            flash("Không tìm thấy tài khoản.", "danger")
            return redirect_admin("users")

        config = get_duplicate_ip_warning_config(force=True)
        trusted = {str(item) for item in (config.get("trusted_user_ids") or []) if item}
        make_trusted = request.form.get("trusted") == "1"
        if make_trusted:
            trusted.add(str(user_id))
        else:
            trusted.discard(str(user_id))
        config["trusted_user_ids"] = sorted(trusted)
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": IP_WARNING_SETTING_KEY,
                "setting_value": config,
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "update_duplicate_ip_trusted_user", attempts=2,
        )
        _ip_warning_config_cache.update({"value": dict(config), "expires_at": time.time() + 30})
        log_admin_action(
            "Đánh dấu tài khoản tin cậy IP" if make_trusted else "Bỏ tin cậy IP",
            "user", user_id, player.get("username"),
        )
        flash("Đã cập nhật trạng thái tin cậy IP của tài khoản.", "success")
        return redirect_admin("users")


    @app.route("/admin/toggle-online/<user_id>", methods=["POST"])
    @login_required
    @admin_required
    def admin_toggle_online(user_id):
        user = get_user(user_id)
        if not user:
            flash("Không tìm thấy player.", "danger")
            return redirect(url_for("players"))

        new_online = not bool(user.get("is_online"))
        db.table("users").update({"is_online": new_online}).eq("id", user_id).execute()
        log_admin_action("Đổi trạng thái online", "user", user_id, user.get("username"), f"is_online: {new_online}")
        flash("Đã đổi trạng thái online.", "success")
        return redirect(url_for("players"))


    @app.route("/admin/player/<user_id>/update", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("users_edit")
    def admin_update_player(user_id):
        player = get_user(user_id)
        actor = current_user()
        if not player:
            flash("Không tìm thấy tài khoản.", "danger")
            return redirect(url_for("admin") + "#users")

        if is_admin_user(player) and not is_owner_user(actor):
            flash("Chỉ Chủ hệ thống mới được sửa tài khoản Admin.", "danger")
            return redirect(url_for("admin") + "#users")

        display_name = request.form.get("display_name", "").strip()
        username = request.form.get("username", "").strip()
        rank_points = request.form.get("rank_points", "").strip()
        rp_adjustment_reason = request.form.get("rp_adjustment_reason", "").strip()[:200]
        zalo_name = request.form.get("zalo_name", "").strip()
        new_password = request.form.get("new_password", "").strip()

        if not display_name or not username:
            flash("Tên hiển thị và username không được để trống.", "danger")
            return redirect_admin("users")

        existing = get_user_by_username(username)
        if existing and existing["id"] != user_id:
            flash("Username này đã tồn tại.", "danger")
            return redirect_admin("users")

        update_data = {
            "display_name": display_name,
            "username": username,
            "zalo_name": zalo_name,
        }

        try:
            old_rank_points = max(0, int(player.get("rank_points") or 0))
            update_data["rank_points"] = max(0, int(rank_points))
        except (TypeError, ValueError):
            flash("Điểm rank phải là số.", "danger")
            return redirect_admin("users")

        rp_delta = update_data["rank_points"] - old_rank_points
        if rp_delta != 0 and not rp_adjustment_reason:
            flash("Hãy nhập lý do khi cộng hoặc trừ RP.", "danger")
            return redirect_admin("users")

        if new_password:
            if len(new_password) < minimum_password_length():
                flash(f"Mật khẩu mới phải có ít nhất {minimum_password_length()} ký tự.", "danger")
                return redirect(url_for("admin") + "#users")
            update_data["password_hash"] = hash_password(new_password)
            update_data["must_change_password"] = True
            update_data["password_changed_at"] = now_iso()

        db.table("users").update(update_data).eq("id", user_id).execute()

        if rp_delta != 0:
            delta_text = f"{rp_delta:+d}"
            create_user_notification(
                user_id,
                "Admin điều chỉnh RP",
                f"Admin {delta_text} RP vì lý do: {rp_adjustment_reason}",
                url_for("profile", user_id=user_id),
                "rp_adjustment",
            )

        if new_password:
            try:
                execute_query(
                    db.table("password_reset_requests").update({
                        "status": "resolved",
                        "admin_user_id": actor["id"],
                        "admin_note": "Admin đã reset mật khẩu từ danh sách user.",
                        "resolved_at": now_iso(),
                    }).eq("user_id", user_id).eq("status", "pending"),
                    "close_password_reset_from_user_admin",
                )
            except Exception as exc:
                print(f"close password reset from user admin warning: {exc}")
        changed = ["username", "display_name", "zalo_name"]
        if rp_delta != 0:
            changed.append(f"rank_points {rp_delta:+d} RP - lý do: {rp_adjustment_reason}")
        else:
            changed.append("rank_points không đổi")
        if new_password:
            changed.append("mật khẩu tạm (bắt buộc đổi)")
        log_admin_action(
            "Cập nhật tài khoản",
            "user",
            user_id,
            player.get("username"),
            "Các mục: " + ", ".join(changed),
        )
        flash(f"Đã cập nhật tài khoản {player.get('username')}.", "success")
        return redirect(url_for("admin") + "#users")


    @app.route("/admin/player/<user_id>/reset-stats", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("users_edit")
    def admin_reset_player_stats(user_id):
        player = get_user(user_id)
        if not player or is_admin_user(player):
            flash("Không tìm thấy player hợp lệ.", "danger")
            return redirect_admin("users")

        db.table("users").update({
            "rank_points": DEFAULT_POINTS,
            "total_matches": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "streak": 0,
        }).eq("id", user_id).execute()

        log_admin_action("Reset chỉ số", "user", user_id, player.get("username"), "Đưa điểm và W-D-L về mặc định.")
        flash("Đã reset chỉ số player.", "success")
        return redirect_admin("users")


    @app.route("/admin/player/<user_id>/delete", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("users_delete")
    def admin_delete_player(user_id):
        player = get_user(user_id)
        player_label = player.get("username") if player else "Không xác định"
        ok, error = delete_player_safe(user_id)
        if not ok:
            flash(error, "danger")
            return redirect_admin("users")

        log_admin_action("Xóa tài khoản", "user", user_id, player_label, "Đã xóa account và dữ liệu liên quan.")
        flash("Đã xóa account player và dữ liệu liên quan.", "success")
        return redirect_admin("users")

