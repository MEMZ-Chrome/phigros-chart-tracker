from __future__ import annotations

from pathlib import Path
from typing import Any

import UnityPy


def write_text_asset(obj: Any, dest: Path) -> None:
    """导出 Unity TextAsset。"""
    data = obj.read()
    script = data.m_Script
    if isinstance(script, str):
        dest.write_text(script, encoding="utf-8")
    else:
        dest.write_bytes(bytes(script))


def extract_asset(bundle_path: Path, file_name: str, dest: Path) -> str:
    """从单个 bundle 中导出一个资源文件。"""
    env = UnityPy.load(str(bundle_path))
    objects = []
    for asset in env.assets if isinstance(env.assets, list) else env.assets.values():
        objects.extend(asset.objects.values())

    suffix = Path(file_name).suffix.lower()
    stem = Path(file_name).stem

    if suffix == ".json":
        for obj in objects:
            if obj.type.name == "TextAsset":
                data = obj.read()
                if (getattr(data, "name", "") or getattr(data, "m_Name", "")) == stem:
                    write_text_asset(obj, dest)
                    return "TextAsset"
        for obj in objects:
            if obj.type.name == "TextAsset":
                write_text_asset(obj, dest)
                return "TextAsset"

    raise RuntimeError(f"No supported object found for {file_name} in {bundle_path.name}")
