"""URL tài nguyên tĩnh có thể chuyển sang Supabase Storage.

Biến môi trường hỗ trợ:
- STATIC_ASSET_BASE_URL: URL public cho tài nguyên tĩnh chung.
- SHOP_ASSET_BASE_URL: URL public riêng cho ``static/shop``.
- LUCKYBOX_ASSET_BASE_URL: URL public riêng cho thư mục Lucky Box.

Khi biến tương ứng để trống, hệ thống tự dùng file trong ``/static``. Việc tách
Shop ra thành URL riêng cho phép chuyển dần ảnh nặng lên Storage mà không ảnh
hưởng logo hoặc tài nguyên giao diện thiết yếu.
"""
from __future__ import annotations

import os
from urllib.parse import quote

from flask import url_for


def _clean_base(value: str | None) -> str:
    return (value or "").strip().rstrip("/")


def asset_base_url() -> str:
    return _clean_base(os.getenv("STATIC_ASSET_BASE_URL"))


def shop_asset_base_url() -> str:
    return _clean_base(os.getenv("SHOP_ASSET_BASE_URL"))


def luckybox_asset_base_url() -> str:
    return _clean_base(os.getenv("LUCKYBOX_ASSET_BASE_URL"))


def asset_url(filename: str) -> str:
    clean = str(filename or "").strip().lstrip("/")
    encoded = quote(clean, safe="/")

    if clean == "luckybox" or clean.startswith("luckybox/"):
        luckybox_base = luckybox_asset_base_url()
        if luckybox_base:
            relative = clean[9:] if clean.startswith("luckybox/") else ""
            return f"{luckybox_base}/{quote(relative, safe='/')}" if relative else luckybox_base
        return url_for("static", filename=clean)

    if clean == "shop" or clean.startswith("shop/"):
        shop_base = shop_asset_base_url()
        if shop_base:
            relative = clean[5:] if clean.startswith("shop/") else ""
            return f"{shop_base}/{quote(relative, safe='/')}" if relative else shop_base
        return url_for("static", filename=clean)

    base = asset_base_url()
    if base:
        return f"{base}/{encoded}"
    return url_for("static", filename=clean)
