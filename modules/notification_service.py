"""Thông báo cá nhân: tối đa 20 mục/người, tự dọn sau 7 ngày.

Không yêu cầu thay đổi schema Supabase. Dữ liệu được dọn theo cơ chế ứng dụng khi
người chơi mở trang thông báo hoặc khi hệ thống tạo thông báo mới.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

EXPORTED_NAMES = [
    "create_user_notification",
    "create_notifications_for_users",
    "notify_admins",
    "cleanup_user_notifications",
    "list_unread_notifications",
    "list_bell_notifications",
    "list_user_notifications",
]

NOTIFICATION_MAX_ITEMS = 20
NOTIFICATION_RETENTION_DAYS = 7
DELETE_BATCH_SIZE = 100


def configure(context):
    globals().update(context)


def _cutoff_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=NOTIFICATION_RETENTION_DAYS)).isoformat()


def _delete_ids(user_id, row_ids) -> int:
    deleted = 0
    ids = [row_id for row_id in row_ids if row_id]
    for offset in range(0, len(ids), DELETE_BATCH_SIZE):
        chunk = ids[offset:offset + DELETE_BATCH_SIZE]
        result = execute_query(
            db.table("user_notifications")
            .delete()
            .eq("user_id", user_id)
            .in_("id", chunk),
            "delete_old_user_notifications",
            attempts=2,
        )
        deleted += len(result.data or [])
    return deleted


def cleanup_user_notifications(user_id=None) -> int:
    """Xóa thông báo quá 7 ngày và giữ tối đa 20 mục mới nhất.

    Việc dọn là best-effort: lỗi dọn dữ liệu không được làm hỏng luồng chính.
    """
    if db is None:
        return 0
    deleted = 0
    try:
        expired_query = db.table("user_notifications").delete().lt("created_at", _cutoff_iso())
        if user_id:
            expired_query = expired_query.eq("user_id", user_id)
        result = execute_query(expired_query, "cleanup_expired_user_notifications", attempts=2)
        deleted += len(result.data or [])
    except Exception as exc:
        print(f"cleanup expired notifications warning: {exc}")

    if not user_id:
        return deleted

    try:
        # Lặp cho đến khi không còn dòng vượt giới hạn, tránh bỏ sót tài khoản
        # từng có rất nhiều thông báo.
        while True:
            result = execute_query(
                db.table("user_notifications")
                .select("id")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .range(NOTIFICATION_MAX_ITEMS, NOTIFICATION_MAX_ITEMS + 499),
                "list_notifications_to_prune",
                attempts=2,
            )
            old_ids = [row.get("id") for row in (result.data or []) if row.get("id")]
            if not old_ids:
                break
            deleted += _delete_ids(user_id, old_ids)
            if len(old_ids) < 500:
                break
    except Exception as exc:
        print(f"prune notifications warning: {exc}")
    return deleted


def create_user_notification(user_id, title, message, link_url=None, notification_type="system"):
    if not user_id:
        return None
    try:
        result = execute_query(
            db.table("user_notifications").insert({
                "user_id": user_id,
                "notification_type": str(notification_type)[:50],
                "title": str(title)[:120],
                "message": str(message)[:500],
                "link_url": str(link_url)[:300] if link_url else None,
                "is_read": False,
            }),
            "create_user_notification",
            attempts=2,
        )
        cleanup_user_notifications(user_id)
        ttl_cache_delete(f"bell_notifications:{user_id}")
        return result.data[0] if result.data else None
    except Exception as exc:
        print(f"create_user_notification warning: {exc}")
        return None


def create_notifications_for_users(user_ids, title, message, link_url=None, notification_type="system"):
    seen = set()
    for user_id in user_ids or []:
        user_key = str(user_id or "")
        if not user_key or user_key in seen:
            continue
        seen.add(user_key)
        create_user_notification(user_id, title, message, link_url, notification_type)


def notify_admins(title, message, link_url="/admin#disputes"):
    try:
        admin_ids = [user.get("id") for user in list_all_users() if is_admin_user(user)]
        create_notifications_for_users(admin_ids, title, message, link_url, "dispute")
    except Exception as exc:
        print(f"notify_admins warning: {exc}")


def list_unread_notifications(user_id, limit=5):
    if not user_id:
        return []
    try:
        result = execute_query(
            db.table("user_notifications")
            .select("*")
            .eq("user_id", user_id)
            .eq("is_read", False)
            .gte("created_at", _cutoff_iso())
            .order("created_at", desc=True)
            .limit(max(1, min(int(limit), NOTIFICATION_MAX_ITEMS))),
            "list_unread_notifications",
            attempts=2,
        )
        return [dict(item) for item in (result.data or [])][:NOTIFICATION_MAX_ITEMS]
    except Exception as exc:
        print(f"list_unread_notifications warning: {exc}")
        return []


def list_bell_notifications(user_id, limit=20):
    """Trả thông báo cho chuông với cache RAM ngắn theo từng người dùng.

    Cache 8 giây giúp nhiều context processor/render liên tiếp không tạo các
    truy vấn giống nhau. Cache bị xóa ngay khi ứng dụng tạo hoặc đánh dấu đọc
    thông báo.
    """
    if not user_id:
        return []

    safe_limit = max(1, min(int(limit), NOTIFICATION_MAX_ITEMS))
    cache_key = f"bell_notifications:{user_id}"
    request_key = f"_bell_notifications_{user_id}"

    cached = cache_get(request_key)
    if isinstance(cached, list):
        return [dict(item) for item in cached[:safe_limit]]

    cached = ttl_cache_get(cache_key)
    if isinstance(cached, list):
        rows = [dict(item) for item in cached]
        cache_set(request_key, rows)
        return rows[:safe_limit]

    try:
        result = execute_query(
            db.table("user_notifications")
            .select("id,user_id,notification_type,title,message,link_url,is_read,read_at,created_at")
            .eq("user_id", user_id)
            .gte("created_at", _cutoff_iso())
            .order("created_at", desc=True)
            .limit(NOTIFICATION_MAX_ITEMS),
            "list_bell_notifications",
            attempts=2,
        )
        rows = [dict(item) for item in (result.data or [])][:NOTIFICATION_MAX_ITEMS]
        ttl_cache_set(cache_key, rows, 8)
        cache_set(request_key, rows)
        return rows[:safe_limit]
    except Exception as exc:
        print(f"list_bell_notifications warning: {exc}")
        return []


def list_user_notifications(user_id, page=1, per_page=20, unread_only=False):
    """Chỉ trả tối đa 20 thông báo còn hạn của đúng người dùng."""
    if not user_id:
        return [], False
    cleanup_user_notifications(user_id)
    try:
        query = (
            db.table("user_notifications")
            .select("*")
            .eq("user_id", user_id)
            .gte("created_at", _cutoff_iso())
            .order("created_at", desc=True)
            .limit(NOTIFICATION_MAX_ITEMS)
        )
        if unread_only:
            query = query.eq("is_read", False)
        result = execute_query(query, "list_user_notifications", attempts=2)
        rows = [dict(item) for item in (result.data or [])]
        return rows[:NOTIFICATION_MAX_ITEMS], False
    except Exception as exc:
        print(f"list_user_notifications warning: {exc}")
        return [], False
