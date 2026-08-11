from pathlib import Path

ROOT=Path(__file__).resolve().parent

def read(name): return (ROOT/name).read_text(encoding="utf-8")

def test_room_name_style_wired():
    app=read("app.py")
    assert 'host_name_style_class' in app and 'guest_name_style_class' in app
    assert 'str(room.get("host_name_style_class"))' in app
    tpl=read("templates/_room_live_content.html")
    assert 'room.host_name_style_class' in tpl and 'room.guest_name_style_class' in tpl

def test_admin_gift_notifications_wired():
    assert 'admin_gift_item' in read("modules/admin_shop/routes.py")
    assert 'admin_gift_zcoin' in read("modules/admin_economy/routes.py")
    assert 'admin_gift_code' in read("modules/admin_economy/routes.py")
    assert 'GIFT_CODE_RECIPIENT_ONLY' in read("docs/update_admin_gift_notification_v1_14_41_41.sql")

def test_deep_links_wired():
    assert 'focus_item_code' in read("modules/inventory/service.py")
    assert 'focus_transaction_id' in read("modules/zcoin/routes.py")
    assert 'gift_code_prefill' in read("modules/daily_checkin/routes.py")
