from pathlib import Path

root = Path(__file__).parent
app = (root / "app.py").read_text(encoding="utf-8")
service = (root / "modules/weekly_rp_rewards_service.py").read_text(encoding="utf-8")
sql = (root / "docs/update_weekly_rp_rewards_v1_14_41_58.sql").read_text(encoding="utf-8")
match_service = (root / "modules/match_result_service.py").read_text(encoding="utf-8")
admin_routes = (root / "modules/admin_match_routes.py").read_text(encoding="utf-8")

assert 'APP_VERSION = "V1.14.41.58"' in app
assert '("matches_10", "Hoàn thành 10 trận trong tuần", 20)' in service
assert '("opponents_5", "Gặp 5 đối thủ khác nhau trong tuần", 30)' in service
assert '("opponents_10", "Gặp 10 đối thủ khác nhau trong tuần", 50)' in service
assert '("opponents_20", "Gặp 20 đối thủ khác nhau trong tuần", 50)' in service
assert '.eq("status", "confirmed")' in service
assert 'unique (user_id, week_start, reward_code)' in sql.casefold()
assert 'grant_weekly_rp_rewards_for_users([player1_id, player2_id])' in match_service
assert 'grant_weekly_rp_rewards_for_users([match.get("player1_id"), match.get("player2_id")])' in admin_routes
print("9/9 weekly RP reward checks passed")
