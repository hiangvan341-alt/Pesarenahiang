# HOTFIX V1.14.39.1: legacy Zcoin context removed from /admin
"""Route trang tổng quan Admin và tải dữ liệu quản trị an toàn.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.route("/admin")
    @login_required
    @admin_required
    def admin():
        # Trang Admin chứa nhiều khối dữ liệu độc lập. Một truy vấn phụ lỗi không được
        # làm sập toàn bộ trang; khối lỗi sẽ tạm trả danh sách rỗng và ghi log Vercel.
        def admin_safe_load(label, loader, default):
            try:
                value = loader()
                return default if value is None else value
            except Exception as exc:
                app.logger.exception("Admin load failed [%s]: %s", label, exc)
                return default

        all_rooms = admin_safe_load("rooms", list_rooms, [])

        # Dọn các phòng chờ bị nhân đôi do double-click hoặc nhiều Vercel instance
        # xử lý đồng thời. Chỉ xóa waiting_ready chưa có match_id nên không ảnh hưởng
        # trận đang đá, kết quả, RP hay tranh chấp.
        duplicate_cleanup_count = 0
        participant_ids = {
            str(value)
            for room in all_rooms
            for value in (room.get("host_user_id"), room.get("guest_user_id"))
            if value
        }
        for participant_id in participant_ids:
            duplicate_cleanup_count += admin_safe_load(
                f"cleanup_duplicate_rooms:{participant_id}",
                lambda uid=participant_id: cleanup_duplicate_waiting_rooms(uid),
                0,
            )
        if duplicate_cleanup_count:
            all_rooms = admin_safe_load("rooms_after_duplicate_cleanup", list_rooms, [])

        all_matches = admin_safe_load("matches", list_matches, [])

        # Báo cáo số trận theo múi giờ Việt Nam. Dùng dữ liệu matches đã tải để
        # tránh phát sinh thêm nhiều truy vấn và giữ kết quả thống nhất với tab Trận đấu.
        from datetime import datetime, timedelta, timezone

        vn_tz = timezone(timedelta(hours=7))
        now_vn = datetime.now(vn_tz)
        today_vn = now_vn.date()
        report_range = str(request.args.get("match_report_range") or "today").strip().lower()
        allowed_ranges = {"today", "yesterday", "3days", "7days", "30days", "all"}
        if report_range not in allowed_ranges:
            report_range = "today"

        report_range_labels = {
            "today": "Hôm nay",
            "yesterday": "Hôm qua",
            "3days": "3 ngày gần đây",
            "7days": "1 tuần",
            "30days": "1 tháng",
            "all": "Toàn thời gian",
        }

        if report_range == "today":
            report_start_date = report_end_date = today_vn
        elif report_range == "yesterday":
            report_start_date = report_end_date = today_vn - timedelta(days=1)
        elif report_range == "3days":
            report_start_date, report_end_date = today_vn - timedelta(days=2), today_vn
        elif report_range == "7days":
            report_start_date, report_end_date = today_vn - timedelta(days=6), today_vn
        elif report_range == "30days":
            report_start_date, report_end_date = today_vn - timedelta(days=29), today_vn
        else:
            report_start_date = report_end_date = None

        def _match_vn_date(match):
            raw = (match or {}).get("created_at")
            if not raw:
                return None
            try:
                value = str(raw).strip().replace("Z", "+00:00")
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(vn_tz).date()
            except Exception:
                return None

        report_matches = []
        for match in all_matches:
            match_date = _match_vn_date(match)
            if match_date is None:
                continue
            if report_start_date and not (report_start_date <= match_date <= report_end_date):
                continue
            row = dict(match)
            row["_report_date"] = match_date
            report_matches.append(row)

        report_status_keys = ("playing", "waiting_confirm", "waiting_result_confirm", "disputed", "confirmed", "cancelled")
        report_status_counts = {key: 0 for key in report_status_keys}
        report_unique_players = set()
        report_confirmed_goals = 0
        report_positive_rp = 0
        report_mode_counts = {"rank_random": 0, "random3_pick1": 0}

        # Khi kết quả được xác nhận, các phiên bản cũ ghi đè note của trận thành
        # "Đã xác nhận.", làm mất dấu Random 3 chọn 1. Ưu tiên đọc team_tier
        # của phòng liên kết để thống kê đúng cả các trận lịch sử đã bị mất note.
        room_mode_by_match_id = {}
        for room in all_rooms:
            match_id = str((room or {}).get("match_id") or "").strip()
            if not match_id:
                continue
            team_tier = str((room or {}).get("team_tier") or "").strip().lower()
            room_mode_by_match_id[match_id] = team_tier

        def _report_match_mode(match):
            match = match or {}
            match_id = str(match.get("id") or "").strip()
            if room_mode_by_match_id.get(match_id) == "random3_pick1":
                return "random3_pick1"

            details = match.get("rp_details")
            if isinstance(details, dict):
                stored_mode = str(details.get("match_mode") or "").strip().lower()
                if stored_mode == "random3_pick1":
                    return "random3_pick1"

            note = str(match.get("note") or "").casefold()
            if "random 3 chọn 1" in note or "random3_pick1" in note:
                return "random3_pick1"
            return "rank_random"

        for match in report_matches:
            status = str(match.get("status") or "").strip().lower()
            if status in report_status_counts:
                report_status_counts[status] += 1
            report_mode_counts[_report_match_mode(match)] += 1
            for key in ("player1_id", "player2_id"):
                if match.get(key):
                    report_unique_players.add(str(match.get(key)))
            if status == "confirmed":
                report_confirmed_goals += int(match.get("score1") or 0) + int(match.get("score2") or 0)
                report_positive_rp += max(0, int(match.get("delta1") or 0)) + max(0, int(match.get("delta2") or 0))

        daily_map = {}
        for match in report_matches:
            day = match.get("_report_date")
            if day is None:
                continue
            bucket = daily_map.setdefault(day, {
                "date": day,
                "total": 0,
                "confirmed": 0,
                "playing": 0,
                "waiting": 0,
                "disputed": 0,
                "cancelled": 0,
                "rank_random": 0,
                "random3_pick1": 0,
                "players": set(),
            })
            bucket["total"] += 1
            bucket[_report_match_mode(match)] += 1
            status = str(match.get("status") or "").strip().lower()
            if status == "confirmed":
                bucket["confirmed"] += 1
            elif status == "playing":
                bucket["playing"] += 1
            elif status in {"waiting_confirm", "waiting_result_confirm"}:
                bucket["waiting"] += 1
            elif status == "disputed":
                bucket["disputed"] += 1
            elif status == "cancelled":
                bucket["cancelled"] += 1
            for key in ("player1_id", "player2_id"):
                if match.get(key):
                    bucket["players"].add(str(match.get(key)))

        match_report_daily = []
        for day in sorted(daily_map.keys(), reverse=True):
            bucket = daily_map[day]
            bucket["player_count"] = len(bucket.pop("players"))
            bucket["date_label"] = day.strftime("%d/%m/%Y")
            match_report_daily.append(bucket)

        match_report = {
            "range": report_range,
            "range_label": report_range_labels[report_range],
            "range_labels": report_range_labels,
            "total": len(report_matches),
            "confirmed": report_status_counts.get("confirmed", 0),
            "playing": report_status_counts.get("playing", 0),
            "waiting": report_status_counts.get("waiting_confirm", 0) + report_status_counts.get("waiting_result_confirm", 0),
            "disputed": report_status_counts.get("disputed", 0),
            "cancelled": report_status_counts.get("cancelled", 0),
            "unique_players": len(report_unique_players),
            "confirmed_goals": report_confirmed_goals,
            "positive_rp": report_positive_rp,
            "rank_random": report_mode_counts["rank_random"],
            "random3_pick1": report_mode_counts["random3_pick1"],
            "rank_random_percent": round((report_mode_counts["rank_random"] * 100 / len(report_matches)), 1) if report_matches else 0,
            "random3_pick1_percent": round((report_mode_counts["random3_pick1"] * 100 / len(report_matches)), 1) if report_matches else 0,
            "popular_mode": (
                "Random 3 chọn 1" if report_mode_counts["random3_pick1"] > report_mode_counts["rank_random"]
                else "Rank Random" if report_mode_counts["rank_random"] > report_mode_counts["random3_pick1"]
                else "Hai chế độ ngang nhau"
            ),
        }

        raw_users = admin_safe_load("users", list_all_users, [])
        admin_users = admin_safe_load(
            "decorate_users", lambda: decorate_admin_users(raw_users), []
        )

        # Ưu tiên nhóm IP trùng lên đầu và đặt các tài khoản cùng IP cạnh nhau.
        admin_users.sort(key=lambda item: (
            0 if item.get("duplicate_ips") else 1,
            (item.get("duplicate_ips") or [item.get("latest_ip") or "~"])[0],
            (item.get("username") or "").lower(),
        ))
        players = [u for u in admin_users if u.get("role") == "player"]
        admins = [u for u in admin_users if is_admin_user(u)]
        pending_users = [u for u in players if u.get("account_status") == "pending"]

        password_reset_requests = admin_safe_load(
            "password_resets", lambda: list_password_reset_requests("pending"), []
        )
        raw_disputes = admin_safe_load(
            "match_disputes", lambda: list_match_disputes("pending"), []
        )
        pending_disputes = []
        for item in raw_disputes:
            try:
                pending_disputes.append(decorate_match_dispute(item, all_matches))
            except Exception as exc:
                app.logger.exception("Admin dispute decoration failed: %s", exc)

        audit_logs = (
            admin_safe_load("audit_logs", list_admin_activity_logs, [])
            if is_owner_user(current_user()) else []
        )
        duplicate_ip_groups = admin_safe_load(
            "duplicate_ips", lambda: build_duplicate_ip_groups(admin_users), []
        )
        duplicate_ip_user_count = len({
            str(account.get("id"))
            for group in duplicate_ip_groups
            for account in group.get("accounts", [])
            if account.get("id")
        })
        ip_device_status = dict(getattr(list_user_devices, "last_status", {}) or {})
        ip_device_status.setdefault("ok", None)
        ip_device_status.setdefault("row_count", 0)
        ip_device_status["account_ip_count"] = sum(1 for user in admin_users if user.get("known_ips"))
        ip_device_status["duplicate_group_count"] = len(duplicate_ip_groups)

        return render_template(
            "admin.html",
            admin_users=admin_users,
            players=players,
            admins=admins,
            pending_users=pending_users,
            all_matches=all_matches[:80],
            disputed=[m for m in all_matches if m.get("status") == "disputed"],
            playing=[m for m in all_matches if m.get("status") == "playing"],
            rooms=[r for r in all_rooms if r.get("status") in ["waiting_ready", "playing", "friendly_playing", "waiting_result_confirm", "waiting_confirm", "disputed"]],
            recent_closed_rooms=[
                r for r in all_rooms
                if r.get("status") == "cancelled"
                and (
                    "đóng trình duyệt" in str(r.get("note") or "").casefold()
                    or "host_browser_offline" in str(r.get("note") or "").casefold()
                    or "chủ phòng" in str(r.get("note") or "").casefold()
                )
            ][:30],
            all_rooms=all_rooms[:80],
            invites=admin_safe_load("invites", lambda: list_invites("pending"), []),
            active_announcement=admin_safe_load("announcement", get_active_announcement, None),
            password_reset_requests=password_reset_requests,
            audit_logs=audit_logs,
            duplicate_ip_groups=duplicate_ip_groups,
            duplicate_ip_user_count=duplicate_ip_user_count,
            ip_device_status=ip_device_status,
            pending_disputes=pending_disputes,
            can_create_test_account=has_admin_permission(current_user(), "users_edit"),
            can_import_accounts_csv=has_admin_permission(current_user(), "accounts_import"),
            admin_permission_groups=ADMIN_PERMISSION_GROUPS,
            admin_permission_labels=ADMIN_PERMISSION_LABELS,
            current_admin_permissions=_admin_permissions(current_user()),
            system_features=admin_safe_load("system_features", get_system_features, dict(SYSTEM_FEATURE_DEFAULTS)),
            maintenance_config=admin_safe_load("maintenance_config", get_maintenance_config, _maintenance_default_config()),
            maintenance_status=admin_safe_load("maintenance_status", get_maintenance_status, {"closed": False, "countdown": None}),
            match_report=match_report,
            match_report_daily=match_report_daily,
        )

