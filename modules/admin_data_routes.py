"""Route Admin xóa trận, hủy/xóa phòng và xóa lời mời.

Module đăng ký route theo dependency của app.py để giữ nguyên endpoint và tránh import vòng.
"""

def register_routes(context):
    """Đăng ký nhóm route vào Flask app hiện tại."""
    globals().update(context)

    @app.route("/admin/match/<match_id>/delete", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("matches_delete")
    def admin_delete_match(match_id):
        """Giữ endpoint cũ nhưng khóa xóa trực tiếp để tránh làm lệch lịch sử."""
        flash(
            "Collap_V1.13.3a đã tắt xóa trực tiếp trận đấu. Admin chỉ được chuyển trạng thái sang Đã hủy.",
            "warning",
        )
        return redirect_admin("matches")


    @app.route("/admin/room/<room_id>/cancel", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("rooms_manage")
    def admin_cancel_room(room_id):
        """Chỉ giải phóng phòng; tuyệt đối không thay đổi kết quả hoặc RP của trận."""
        room = get_room(room_id)
        if not room:
            flash("Không tìm thấy phòng.", "danger")
            return redirect_admin("rooms")

        if room.get("status") == "cancelled":
            flash("Phòng này đã được hủy trước đó.", "warning")
            return redirect_admin("rooms")

        linked_match = get_match(room.get("match_id")) if room.get("match_id") else None
        old_match_status = linked_match.get("status") if linked_match else None
        updated_at = now_iso()

        # Không sửa bảng matches. Trận waiting_confirm vẫn chờ đủ 12 giờ rồi
        # tự xác nhận/cộng trừ RP; disputed vẫn chờ Admin xử lý riêng.
        db.table("match_rooms").update({
            "status": "cancelled",
            "note": "Admin đã hủy phòng để giải phóng người chơi. Kết quả trận được xử lý độc lập.",
            "state_expires_at": None,
            "updated_at": updated_at,
        }).eq("id", room_id).execute()

        if room.get("invite_id"):
            db.table("match_invites").update({
                "status": "cancelled",
                "updated_at": updated_at,
            }).eq("id", room.get("invite_id")).execute()

        cache_delete("_rz_rooms_all")
        cache_delete("_rz_invites_all")
        cache_delete("_rz_current_pending_invites")
        ttl_cache_delete("rooms_raw")
        ttl_cache_delete("invites_raw")

        log_admin_action(
            "Hủy phòng", "room", room_id,
            details=(
                f"Phòng cũ: {room.get('status')}; trận: {old_match_status or 'không có'}; "
                "chỉ giải phóng phòng, không sửa trạng thái trận và không đổi RP"
            ),
        )
        flash("Đã hủy phòng. Kết quả trận vẫn được xử lý riêng và RP không bị mất.", "success")
        return redirect_admin("rooms")


    @app.route("/admin/invite/<invite_id>/delete", methods=["POST"])
    @login_required
    @admin_required
    @admin_permission_required("invites_manage")
    def admin_delete_invite(invite_id):
        invite = get_invite(invite_id)
        if not invite:
            flash("Không tìm thấy lời mời.", "danger")
            return redirect_admin("rooms")

        db.table("match_invites").delete().eq("id", invite_id).execute()
        log_admin_action("Xóa lời mời", "invite", invite_id, details=f"{invite.get('from_name', '-')} → {invite.get('to_name', '-')}")
        flash("Đã xóa lời mời.", "success")
        return redirect_admin("rooms")

