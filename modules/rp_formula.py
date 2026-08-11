"""Cấu hình công thức RP của PES Arena.

File này chỉ chứa phiên bản, hằng số và mô tả công thức. Không truy cập Flask,
Supabase hoặc dữ liệu người dùng. Mọi thay đổi công thức RP phải được thực hiện
ở đây và tăng ``RP_FORMULA_VERSION``.
"""
from __future__ import annotations

RP_FORMULA_VERSION = "RP_V1.14.5"
RP_FORMULA_NAME = "PES Arena RP – Tách thưởng chuỗi khỏi hệ số gặp lại"
RP_RANDOM_SEED_NAMESPACE = f"PES_ARENA|{RP_FORMULA_VERSION}"

PLACEMENT_MATCHES = 10
MAX_POSITIVE_POINTS_PER_MATCH = 50

# Người thắng
WIN_BASE_RANGE = (21, 23)
WIN_VARIATION_RANGE = (-1, 3)
PLACEMENT_WIN_BONUS_RANGE = (1, 4)
PLACEMENT_WIN_TOTAL_RANGE = (22, 29)
HOST_WIN_FACTOR = 0.95

# Phục hồi sau chuỗi thua từ 5 trận: thắng lần 1/2 nhận RP cố định.
LOSS_RECOVERY_MIN_STREAK = 5
LOSS_RECOVERY_WIN_POINTS = {1: 17, 2: 19}

# Người thua
PLACEMENT_LOSS_RANGE = (14, 19)
REGULAR_LOSS_RANGE = (19, 23)
LOSS_STREAK_START = 4
LOSS_STREAK_RANGES = {
    4: (22, 24),
    5: (23, 26),
    6: (25, 27),
}
LOSS_STREAK_SEVEN_PLUS_RANGE = (25, 30)

# Chuỗi thắng: chỉ thưởng đúng trận chạm mốc.
WIN_STREAK_BONUSES = {3: 5, 5: 10, 10: 15}

# Tương thích với một số phần giao diện/code cũ. Không dùng làm fallback RP.
BASE_WIN_POINTS = WIN_BASE_RANGE[0]
PLACEMENT_WIN_MULTIPLIER = 1.0
MIN_RANK_ADJUSTED_WIN_POINTS = WIN_BASE_RANGE[0]
MAX_RANK_ADJUSTED_WIN_POINTS = WIN_BASE_RANGE[1]


def formula_summary() -> dict:
    """Trả mô tả ngắn, có thể lưu vào rp_details hoặc hiển thị trong Admin."""
    return {
        "version": RP_FORMULA_VERSION,
        "name": RP_FORMULA_NAME,
        "winner": {
            "base": list(WIN_BASE_RANGE),
            "variation": list(WIN_VARIATION_RANGE),
            "placement_bonus": list(PLACEMENT_WIN_BONUS_RANGE),
            "placement_total": list(PLACEMENT_WIN_TOTAL_RANGE),
            "host_factor": HOST_WIN_FACTOR,
            "loss_recovery_min_streak": LOSS_RECOVERY_MIN_STREAK,
            "loss_recovery_win_points": dict(LOSS_RECOVERY_WIN_POINTS),
        },
        "loser": {
            "placement": list(PLACEMENT_LOSS_RANGE),
            "regular": list(REGULAR_LOSS_RANGE),
            "loss_streak_start": LOSS_STREAK_START,
        },
        "repeat_opponent": {
            "winner_factors": [1.0, 0.6, 0.3, 0.0],
            "loser_factors": [1.0, 0.7, 0.4, 0.1],
            "pair_rp_match_limit": 6,
            "streak_bonus_scaled_by_repeat_factor": False,
            "streak_bonus_scaled_by_host_factor": False,
            "draw_equal_points": 3,
            "draw_gap_bonus": 6,
            "draw_gap_threshold": 500,
        },
    }
