"""Route Admin hệ thống: maintenance, công tắc tính năng, backup/restore RP và chuyển chủ sở hữu.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

from flask import abort

def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.context_processor
    def inject_admin_feature_context():
        user = current_user()
        return {
            "app_name": APP_NAME, "app_version": APP_VERSION,
            "system_features": get_system_features(),
            "can_admin": lambda code: has_admin_permission(user, code),
            "admin_display_role": "Admin" if is_admin_user(user) else "",
            "is_test_mode": is_test_mode(),
            "simple_test_passwords_enabled": simple_test_passwords_enabled(),
            "minimum_password_length": minimum_password_length(),
            "maintenance_status": get_maintenance_status(),
            "rank_daily_limits_enabled": daily_rank_limits_enabled(),
            "quick_match_config": get_quick_match_config(),
            "repeat_opponent_rp_config": get_repeat_opponent_rp_config(),
            "weekly_rp_reward_config": get_weekly_rp_reward_config(),
            "duplicate_ip_warning_config": get_duplicate_ip_warning_config(),
        }

    @app.route("/admin/system/maintenance", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("system_features_manage")
    def admin_update_maintenance():
        close_at = _normalize_maintenance_input(request.form.get("close_at"))
        open_at = _normalize_maintenance_input(request.form.get("open_at"))
        close_dt = _parse_maintenance_time(close_at)
        open_dt = _parse_maintenance_time(open_at)
        if close_dt and open_dt and open_dt <= close_dt:
            flash("Thời gian mở máy chủ phải sau thời gian đóng máy chủ.", "danger")
            return redirect_admin("system")

        config = {
            "manual_closed": request.form.get("manual_closed") == "1",
            "close_at": close_at,
            "open_at": open_at,
            "message": (request.form.get("message") or "").strip()[:500]
                or _maintenance_default_config()["message"],
            "updated_at": now_iso(),
        }
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": MAINTENANCE_SETTING_KEY,
                "setting_value": config,
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "update_server_maintenance_config",
            attempts=2,
        )
        _maintenance_cache["value"] = dict(config)
        _maintenance_cache["expires_at"] = time.time() + 15
        log_admin_action("Cập nhật trạng thái bảo trì máy chủ", "system", details=config)
        flash("Đã lưu trạng thái và lịch bảo trì máy chủ.", "success")
        return redirect_admin("system")


    @app.route("/admin/system/duplicate-ip-warning", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("system_features_manage")
    def admin_update_duplicate_ip_warning():
        current = get_duplicate_ip_warning_config(force=True)
        payload = {
            "enabled": request.form.get("enabled") == "1",
            "ignore_admin_managed": request.form.get("ignore_admin_managed") == "1",
            "trusted_user_ids": list(current.get("trusted_user_ids") or []),
        }
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": IP_WARNING_SETTING_KEY,
                "setting_value": payload,
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "update_duplicate_ip_warning_config", attempts=2,
        )
        _ip_warning_config_cache.update({"value": dict(payload), "expires_at": time.time() + 30})
        log_admin_action("Cập nhật cảnh báo trùng IP", "system", details=payload)
        flash("Đã lưu thiết lập cảnh báo IP.", "success")
        return redirect_admin("users")


    @app.route("/admin/system/quick-match", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("system_features_manage")
    def admin_update_quick_match():
        color = (request.form.get("color") or QUICK_MATCH_COLOR_DEFAULT).strip().lower()
        if color not in QUICK_MATCH_COLOR_VALUES:
            color = QUICK_MATCH_COLOR_DEFAULT
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": QUICK_MATCH_SETTING_KEY,
                "setting_value": {"color": color},
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "update_quick_match_config",
        )
        ttl_cache_delete("quick_match_config")
        cache_delete("_quick_match_config_cached")
        log_admin_action("Cập nhật màu nút Tìm Nhanh", "system", details={"color": color})
        flash("Đã lưu màu nút Tìm Nhanh.", "success")
        return redirect_admin("system")


    @app.route("/admin/system/repeat-opponent-rp", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("system_features_manage")
    def admin_update_repeat_opponent_rp_config():
        try:
            winner_values = [int(request.form.get(f"winner_factor_{index}", "")) for index in range(1, 5)]
            loser_values = [int(request.form.get(f"loser_factor_{index}", "")) for index in range(1, 5)]
        except (TypeError, ValueError):
            flash("Các hệ số phải là số nguyên từ 0 đến 100%.", "danger")
            return redirect_admin("system")
        for label, values in (("người thắng", winner_values), ("người thua", loser_values)):
            if any(value < 0 or value > 100 for value in values):
                flash(f"Mỗi hệ số của {label} phải nằm trong khoảng 0–100%.", "danger")
                return redirect_admin("system")
            if not all(values[index] >= values[index + 1] for index in range(3)):
                flash(f"Hệ số của {label} phải giảm dần hoặc bằng nhau từ lần 1 đến lần 4.", "danger")
                return redirect_admin("system")
        payload = {"winner_factors": winner_values, "loser_factors": loser_values}
        execute_query(
            db.table("system_settings").upsert({
                "setting_key": REPEAT_OPPONENT_CONFIG_SETTING_KEY,
                "setting_value": payload,
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "update_repeat_opponent_rp_config",
        )
        ttl_cache_delete("repeat_opponent_rp_config")
        cache_delete("_repeat_opponent_rp_config_cached")
        log_admin_action("Cập nhật hệ số RP gặp lại đối thủ", "system", details=payload)
        flash(
            "Đã lưu hệ số gặp lại đối thủ — thắng: "
            + " → ".join(f"{value}%" for value in winner_values)
            + "; thua: " + " → ".join(f"{value}%" for value in loser_values) + ".",
            "success",
        )
        return redirect_admin("system")

    @app.route("/admin/system/weekly-rp-rewards", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("system_features_manage")
    def admin_update_weekly_rp_rewards():
        fields = (
            "opponents_5_threshold", "opponents_5_rp",
            "opponents_10_threshold", "opponents_10_rp",
            "opponents_20_threshold", "opponents_20_rp",
            "matches_threshold", "matches_rp",
        )
        try:
            payload = {field: int(request.form.get(field, "")) for field in fields}
        except (TypeError, ValueError):
            flash("Các mốc và RP thưởng phải là số nguyên.", "danger")
            return redirect_admin("system")

        thresholds = [
            payload["opponents_5_threshold"],
            payload["opponents_10_threshold"],
            payload["opponents_20_threshold"],
        ]
        if any(value < 1 or value > 500 for value in thresholds + [payload["matches_threshold"]]):
            flash("Mỗi mốc phải nằm trong khoảng 1–500.", "danger")
            return redirect_admin("system")
        if not thresholds[0] < thresholds[1] < thresholds[2]:
            flash("Ba mốc đối thủ phải tăng dần.", "danger")
            return redirect_admin("system")
        reward_values = [
            payload["opponents_5_rp"], payload["opponents_10_rp"],
            payload["opponents_20_rp"], payload["matches_rp"],
        ]
        if any(value < 0 or value > 1000 for value in reward_values):
            flash("RP thưởng mỗi mốc phải nằm trong khoảng 0–1000.", "danger")
            return redirect_admin("system")

        execute_query(
            db.table("system_settings").upsert({
                "setting_key": "weekly_rp_reward_config",
                "setting_value": payload,
                "updated_at": now_iso(),
            }, on_conflict="setting_key"),
            "update_weekly_rp_reward_config",
            attempts=2,
        )
        get_weekly_rp_reward_config(force_refresh=True)
        log_admin_action("Cập nhật thưởng RP theo tuần", "system", details=payload)
        flash("Đã lưu các mốc thưởng RP theo tuần.", "success")
        return redirect_admin("system")


    @app.route("/admin/system/features", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("system_features_manage")
    def admin_update_system_features():
        previous_features = get_system_features()
        features = {key: request.form.get(key) == "1" for key in SYSTEM_FEATURE_DEFAULTS}
        # Luôn phải còn ít nhất một chế độ Rank để phòng không bị kẹt.
        if not features.get("rank_standard_enabled", True):
            features["friendly_random3_enabled"] = True

        execute_query(
            db.table("system_settings").upsert(
                {
                    "setting_key": "admin_system_features",
                    "setting_value": features,
                    "updated_at": now_iso(),
                },
                on_conflict="setting_key",
            ),
            "update_system_features",
        )
        ttl_cache_delete("system_features")
        cache_delete("_system_features_cached")

        cleanup_errors = []

        def best_effort(query, operation_name):
            try:
                execute_query(query, operation_name, attempts=2)
            except Exception as exc:
                cleanup_errors.append(f"{operation_name}: {exc}")
                print(f"{operation_name} warning: {exc}")

        # Các thao tác dọn phòng chỉ là hậu xử lý. Nếu schema production cũ thiếu
        # một cột phụ, việc lưu công tắc vẫn phải thành công thay vì trả HTTP 500.
        if previous_features.get("friendly_enabled", True) and not features.get("friendly_enabled", False):
            best_effort(
                db.table("match_rooms").update({
                    "status": "waiting_ready",
                    "match_mode": MATCH_MODE_RANKED,
                    "host_team": None,
                    "guest_team": None,
                    "note": "Giao hữu đã được Admin tắt. Phòng đã trở về trạng thái chờ.",
                    "updated_at": now_iso(),
                }).eq("status", "friendly_playing").neq("team_tier", FRIENDLY_RANDOM3_MODE),
                "disable_active_friendly_rooms",
            )

        if previous_features.get("friendly_random3_enabled", True) and not features.get("friendly_random3_enabled", False):
            # Khi Rank thường đang bật, chuyển phòng Random 3 chưa bắt đầu về Rank thường.
            best_effort(
                db.table("match_rooms").update({
                    "status": "waiting_ready",
                    "match_mode": MATCH_MODE_RANKED,
                    "team_tier": SMART_RANDOM_MODE,
                    "host_team": None,
                    "guest_team": None,
                    "note": "Random 3 chọn 1 đã được Admin tắt. Phòng chuyển về Rank thường.",
                    "updated_at": now_iso(),
                }).eq("team_tier", FRIENDLY_RANDOM3_MODE).eq("status", "waiting_ready"),
                "disable_random3_waiting_rooms",
            )

        if previous_features.get("rank_standard_enabled", True) and not features.get("rank_standard_enabled", True):
            for query, operation_name in (
                (
                    db.table("match_rooms").update({
                        "team_tier": FRIENDLY_RANDOM3_MODE,
                        "friendly_tier": None,
                        "note": "Rank thường đã tắt. Phòng chuyển sang Random 3 chọn 1.",
                        "updated_at": now_iso(),
                    }).eq("status", "waiting_ready").eq("match_mode", MATCH_MODE_RANKED).eq("team_tier", SMART_RANDOM_MODE),
                    "migrate_smart_rank_rooms_to_random3",
                ),
                (
                    db.table("match_rooms").update({
                        "team_tier": FRIENDLY_RANDOM3_MODE,
                        "friendly_tier": None,
                        "note": "Rank thường đã tắt. Phòng chuyển sang Random 3 chọn 1.",
                        "updated_at": now_iso(),
                    }).eq("status", "waiting_ready").eq("match_mode", MATCH_MODE_RANKED).is_("team_tier", "null"),
                    "migrate_null_rank_rooms_to_random3",
                ),
            ):
                best_effort(query, operation_name)

        log_admin_action("Cập nhật công tắc hệ thống", "system", details={
            **features,
            "cleanup_warnings": cleanup_errors[:3],
        })
        if cleanup_errors:
            flash("Đã lưu công tắc. Một số phòng cũ chưa tự chuyển trạng thái; hệ thống sẽ xử lý khi phòng được mở lại.", "warning")
        else:
            flash("Đã cập nhật các tính năng hệ thống.", "success")
        return redirect_admin("system")




    @app.route("/admin/system/rank-daily-limits", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("daily_rank_limits_manage")
    def admin_update_rank_daily_limits():
        enabled = request.form.get("enabled") == "1"
        set_daily_rank_limits_enabled(enabled, actor_id=(current_user() or {}).get("id"))
        log_admin_action(
            "Cập nhật giới hạn Rank theo ngày",
            "system",
            details={
                "enabled": enabled,
                "weekday_game_limit": 10,
                "weekend_game_limit": 15,
                "daily_positive_rp_limit": 150,
            },
        )
        flash(
            "Đã bật giới hạn RP theo số trận: 10 trận tính RP từ Thứ 2 đến Thứ 6, 15 trận tính RP vào cuối tuần. Các trận chơi thêm vẫn lưu lịch sử nhưng nhận 0 RP; tối đa +150 RP mỗi ngày."
            if enabled else
            "Đã tắt giới hạn RP theo số trận Rank và trần RP cộng theo ngày.",
            "success",
        )
        return redirect_admin("system")

    @app.route("/admin/system/rank-daily-limits/reset-user", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("daily_rank_limits_manage")
    def admin_reset_user_rank_daily_games():
        if not daily_rank_limits_enabled():
            flash("Hãy bật Giới hạn thi đấu Rank mỗi ngày trước khi reset lượt cho người chơi.", "danger")
            return redirect_admin("system")
        user_id = str(request.form.get("user_id") or "").strip()
        target = get_user(user_id) if user_id else None
        if not target:
            flash("Không tìm thấy người chơi cần reset lượt Rank.", "danger")
            return redirect_admin("system")
        games_before = ranked_games_today(user_id)
        result = reset_user_daily_rank_games(user_id, actor_id=(current_user() or {}).get("id"))
        log_admin_action(
            "Reset lượt thi đấu Rank trong ngày",
            "user",
            target_id=user_id,
            details={
                "display_name": target.get("display_name"),
                "games_before": games_before,
                "new_game_limit": result.get("game_limit"),
                "reset_at": result.get("reset_at"),
                "positive_rp_cap_reset": False,
            },
        )
        flash(
            f"Đã reset số trận Rank hôm nay của {target.get('display_name') or 'người chơi'}. "
            f"Tài khoản có thể chơi lại tối đa {result.get('game_limit')} trận; trần +150 RP không được reset.",
            "success",
        )
        return redirect_admin("system")

    RP_USER_FIELDS = (
        "id", "rank_points", "wins", "draws", "losses", "total_matches",
        "goals_for", "goals_against", "streak", "loss_streak",
    )
    RP_MATCH_FIELDS = (
        "id", "delta1", "delta2", "rp_formula_version", "rp_details",
    )
    RP_BACKUP_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
    RP_BACKUP_MAX_ROWS = 100000


    def _select_all_rows(table_name, columns="*", page_size=1000):
        rows = []
        start = 0
        while True:
            result = execute_query(
                db.table(table_name).select(columns).range(start, start + page_size - 1),
                f"rp_backup_{table_name}_{start}", attempts=2,
            )
            batch = result.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                return rows
            start += page_size


    def _build_rp_backup_payload(actor):
        users = _select_all_rows("users", ",".join(RP_USER_FIELDS))
        matches = _select_all_rows("matches", ",".join(RP_MATCH_FIELDS))
        return {
            "metadata": {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "backup_type": "rp_only",
                "format_version": 1,
                "created_at": now_iso(),
                "created_by_user_id": actor.get("id"),
                "created_by_username": actor.get("username"),
                "environment": APP_ENV,
            },
            "users": users,
            "matches": matches,
        }


    @app.route("/admin/rp/backup/download", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("rp_backup_restore")
    def admin_download_rp_backup():
        actor = current_user()
        if not is_test_mode() and request.form.get("confirm_text", "").strip() != "SAO LUU RP":
            flash("Trên Production, hãy nhập đúng: SAO LUU RP", "danger")
            return redirect_admin("rp-tools")
        try:
            payload = _build_rp_backup_payload(actor)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            json_name = f"PES_Arena_RP_Backup_{timestamp}.json"
            zip_name = f"PES_Arena_RP_Backup_{timestamp}.zip"
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(json_name, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
                archive.writestr(
                    "README.txt",
                    "PES Arena RP-only backup. Restores RP/statistics/streaks and stored match deltas only.\n",
                )
            output.seek(0)
            log_admin_action("Sao lưu RP toàn hệ thống", "rp", details={
                "users": len(payload["users"]), "matches": len(payload["matches"]),
            })
            return send_file(output, mimetype="application/zip", as_attachment=True, download_name=zip_name)
        except Exception as exc:
            app.logger.exception("RP backup failed")
            flash(f"Không thể sao lưu RP: {exc}", "danger")
            return redirect_admin("rp-tools")


    def _read_rp_backup_upload(upload):
        if not upload or not upload.filename:
            raise ValueError("Hãy chọn file PES Arena RP Backup ZIP hoặc JSON.")
        raw = upload.read(RP_BACKUP_UPLOAD_MAX_BYTES + 1)
        if len(raw) > RP_BACKUP_UPLOAD_MAX_BYTES:
            raise ValueError("File RP Backup vượt quá giới hạn 10 MB.")
        filename = upload.filename.lower().strip()
        if filename.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
                candidates = [i for i in archive.infolist() if i.filename.lower().endswith(".json") and not i.is_dir()]
                if len(candidates) != 1:
                    raise ValueError("ZIP phải chứa đúng một file JSON RP Backup.")
                if candidates[0].file_size > RP_BACKUP_UPLOAD_MAX_BYTES * 5:
                    raise ValueError("JSON giải nén quá lớn.")
                raw = archive.read(candidates[0])
        elif not filename.endswith(".json"):
            raise ValueError("Chỉ chấp nhận file .zip hoặc .json.")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except Exception as exc:
            raise ValueError("Không đọc được JSON trong file RP Backup.") from exc
        metadata = payload.get("metadata") or {}
        if metadata.get("app_name") != APP_NAME or metadata.get("backup_type") != "rp_only":
            raise ValueError("Đây không phải file PES Arena RP Backup hợp lệ.")
        users = payload.get("users")
        matches = payload.get("matches")
        if not isinstance(users, list) or not isinstance(matches, list):
            raise ValueError("File thiếu danh sách users hoặc matches.")
        if len(users) + len(matches) > RP_BACKUP_MAX_ROWS:
            raise ValueError("File RP Backup vượt quá 100.000 bản ghi.")
        return payload


    def _restore_rp_backup_payload(payload):
        user_count = 0
        match_count = 0
        missing_users = 0
        missing_matches = 0
        for row in payload.get("users", []):
            user_id = row.get("id")
            if not user_id:
                continue
            values = {key: row.get(key) for key in RP_USER_FIELDS if key != "id"}
            result = execute_query(
                db.table("users").update(values).eq("id", user_id),
                "restore_rp_user", attempts=2,
            )
            if result.data:
                user_count += 1
            else:
                missing_users += 1
        for row in payload.get("matches", []):
            match_id = row.get("id")
            if not match_id:
                continue
            values = {key: row.get(key) for key in RP_MATCH_FIELDS if key != "id"}
            result = execute_query(
                db.table("matches").update(values).eq("id", match_id),
                "restore_rp_match", attempts=2,
            )
            if result.data:
                match_count += 1
            else:
                missing_matches += 1
        ttl_cache_delete("players_raw", "rooms_raw", "achievement_map")
        cache_delete("all_users")
        return {
            "users": user_count, "matches": match_count,
            "missing_users": missing_users, "missing_matches": missing_matches,
        }


    @app.route("/admin/rp/backup/restore", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("rp_backup_restore")
    def admin_restore_rp_backup():
        actor = current_user()
        if not is_test_mode():
            if not is_owner_user(actor):
                flash("Chỉ tài khoản sở hữu được khôi phục RP trên Production.", "danger")
                return redirect_admin("rp-tools")
            if actor.get("password_hash") != hash_password(request.form.get("current_password", "")):
                flash("Mật khẩu hiện tại không đúng.", "danger")
                return redirect_admin("rp-tools")
            if request.form.get("confirm_text", "").strip() != "KHOI PHUC RP":
                flash("Hãy nhập đúng: KHOI PHUC RP", "danger")
                return redirect_admin("rp-tools")
        try:
            payload = _read_rp_backup_upload(request.files.get("backup_file"))
            report = _restore_rp_backup_payload(payload)
            log_admin_action("Khôi phục RP toàn hệ thống", "rp", details={
                "source": payload.get("metadata", {}), "report": report,
            })
            flash(
                f"Đã khôi phục RP cho {report['users']} tài khoản và delta của {report['matches']} trận. "
                f"Không tìm thấy: {report['missing_users']} tài khoản, {report['missing_matches']} trận.",
                "success",
            )
        except Exception as exc:
            app.logger.exception("RP restore failed")
            log_admin_action("Khôi phục RP thất bại", "rp", details={"error": str(exc)[:500]})
            flash(f"Không thể khôi phục RP: {exc}", "danger")
        return redirect_admin("rp-tools")


    # Các route Backup toàn bộ dữ liệu từ V1.13.4 đã ngừng sử dụng.
    @app.route("/admin/backup/download", methods=["POST"])
    @app.route("/admin/backup/preview", methods=["POST"])
    @app.route("/admin/backup/restore", methods=["POST"])
    @login_required
    @admin_required
    def retired_full_database_backup_routes():
        abort(404)


    @app.route("/admin/ownership/transfer", methods=["POST"])
    @login_required
    @owner_required
    def admin_transfer_ownership():
        abort(404)
        actor = current_user()
        target_id = (request.form.get("target_user_id") or "").strip()
        current_password = request.form.get("current_password", "").strip()
        confirm_text = request.form.get("confirm_text", "").strip()
        if actor.get("password_hash") != hash_password(current_password):
            flash("Mật khẩu hiện tại của tài khoản sở hữu không đúng.", "danger")
            return redirect_admin("overview")
        if confirm_text != "CHUYEN GIAO":
            flash("Hãy nhập đúng CHUYEN GIAO để xác nhận.", "danger")
            return redirect_admin("overview")
        target = get_user(target_id)
        if not target or target.get("account_status") != "approved" or target.get("id") == actor.get("id"):
            flash("Tài khoản nhận chuyển giao không hợp lệ.", "danger")
            return redirect_admin("overview")

        full_permissions = {code: True for codes in ADMIN_PERMISSION_GROUPS.values() for code in codes}
        try:
            execute_query(db.table("users").update({
                "admin_level": "owner", "admin_permissions": full_permissions,
                "updated_at": now_iso(),
            }).eq("id", target["id"]), "transfer_owner_to_target")
            execute_query(db.table("users").update({
                "admin_level": "admin", "admin_permissions": full_permissions,
                "updated_at": now_iso(),
            }).eq("id", actor["id"]), "transfer_owner_from_actor")
        except Exception:
            app.logger.exception("Ownership transfer failed")
            # Best-effort restore the actor as owner if the second write failed.
            execute_query(db.table("users").update({"admin_level": "owner"}).eq("id", actor["id"]), "restore_owner_after_transfer_failure", attempts=2)
            raise
        log_admin_action("Chuyển giao quyền sở hữu", "user", target["id"], target.get("username"), f"Từ {actor.get('username')} sang {target.get('username')}")
        session.clear()
        flash("Đã chuyển giao quyền sở hữu. Hãy đăng nhập lại.", "success")
        return redirect(url_for("login"))

