from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import UnityPy


def _iter_objects(env: Any) -> Iterable[Any]:
    """遍历 bundle 中的所有 Unity 对象。"""
    assets = env.assets
    containers = assets if isinstance(assets, list) else assets.values()
    for asset in containers:
        yield from asset.objects.values()


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
    objects = [o for o in _iter_objects(env) if o.type.name == "TextAsset"]

    stem = Path(file_name).stem
    # 名称与文件名主干一致的对象优先
    for obj in objects:
        data = obj.read()
        if (getattr(data, "name", "") or getattr(data, "m_Name", "")) == stem:
            write_text_asset(obj, dest)
            return "TextAsset"

    if objects:
        write_text_asset(objects[0], dest)
        return "TextAsset"

    raise RuntimeError(f"No supported object found for {file_name} in {bundle_path.name}")
