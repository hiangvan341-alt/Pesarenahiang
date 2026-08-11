from pathlib import Path


ROOT = Path(__file__).parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
ADMIN_ROUTES = (ROOT / "modules/admin_system_routes.py").read_text(encoding="utf-8")
ROOM_ROUTES = (ROOT / "modules/room_access_routes.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "templates/admin.html").read_text(encoding="utf-8")
ROOM_TEMPLATES = [
    (ROOT / "templates/room_detail.html").read_text(encoding="utf-8"),
    (ROOT / "templates/_room_live_content.html").read_text(encoding="utf-8"),
    (ROOT / "templates/partials/room_dynamic_state.html").read_text(encoding="utf-8"),
]
CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")


def test_five_new_styles_are_available_in_admin_and_css():
    styles = (
        "champions-night", "frozen-tech", "ember-rivalry",
        "royal-gold", "mono-tactical",
    )
    assert "ROOM_STYLE_OPTIONS" in APP
    assert "admin_update_room_visual_style" in ADMIN_ROUTES
    for style in styles:
        assert style in APP
        assert style in ADMIN
        assert f'data-room-style="{style}"' in CSS


def test_room_style_reaches_full_and_polling_room_views():
    assert '"room_visual_style": get_room_visual_style()' in ROOM_ROUTES
    assert "data-room-style=" in ROOM_TEMPLATES[0]
    assert "data-room-style=" in ROOM_TEMPLATES[2]


def test_rank_single_label_is_consistent_in_room_and_admin():
    visible_sources = ROOM_TEMPLATES + [ADMIN]
    assert all("RANK THƯỜNG" not in source for source in visible_sources)
    assert all("Rank thường" not in source for source in visible_sources)
    assert all("RANK ĐƠN" in source for source in ROOM_TEMPLATES)
    assert "Rank đơn" in ADMIN


def test_theme_styles_cover_room_actions_and_states():
    for selector in (
        ".room-mode-select-btn", ".room-center-action-btn",
        ".room-result-btn", ".room-submit-result-btn",
        ".room-center-random-trigger", ".room-guest-card-kick-btn",
        ".room-center-state-pill", ".room-result-review",
    ):
        assert selector in CSS


def test_styles_change_structure_but_keep_button_colors_and_stadiums_visible():
    assert "Room styles change structure, never the action color language" in CSS
    for marker in (
        "Broadcast Pro", "Hex Arena", "Glass Deck", "Tunnel Match", "Tactical Board",
    ):
        assert marker in APP
    for color in ("#0879ff", "#8b35ff", "#43d99a", "#ffd65e", "#ef7379"):
        assert color in CSS
    for asset in ("stadium-blue.webp", "stadium-red.webp", "center-stadium2.webp"):
        assert asset in CSS
    assert "background:rgba(3,11,23,.54)!important" in CSS


if __name__ == "__main__":
    test_five_new_styles_are_available_in_admin_and_css()
    test_room_style_reaches_full_and_polling_room_views()
    test_rank_single_label_is_consistent_in_room_and_admin()
    test_theme_styles_cover_room_actions_and_states()
    test_styles_change_structure_but_keep_button_colors_and_stadiums_visible()
