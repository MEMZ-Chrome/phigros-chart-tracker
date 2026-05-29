from __future__ import annotations

import re


def safe_name(name: str, fallback: str = "_") -> str:
    """把字符串转成 Windows 可用文件名。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    name = name.rstrip(". ")
    return name or fallback
