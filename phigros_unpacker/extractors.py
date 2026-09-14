from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import UnityPy

# 可导出的资源后缀 -> Unity 对象类型
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tga", ".webp", ".bmp"}
AUDIO_SUFFIXES = {".wav", ".ogg", ".mp3", ".fsb"}
IMAGE_TYPES = ("Texture2D", "Sprite")
AUDIO_TYPES = ("AudioClip", "AudioResource")


def _iter_objects(env: Any) -> Iterable[Any]:
    """遍历 bundle 中的所有 Unity 对象。"""
    assets = env.assets
    containers = assets if isinstance(assets, list) else assets.values()
    for asset in containers:
        yield from asset.objects.values()


def _object_name(obj: Any) -> str:
    """读取对象名（读失败返回空串）。"""
    try:
        data = obj.read()
    except Exception:
        return ""
    return getattr(data, "name", "") or getattr(data, "m_Name", "") or ""


def _prefer_name_match(objects: list[Any], stem: str) -> list[Any]:
    """名称与文件名主干一致的对象排前面，其余保持原顺序。"""
    matched = [o for o in objects if _object_name(o) == stem]
    matched_ids = {id(o) for o in matched}
    return matched + [o for o in objects if id(o) not in matched_ids]


def write_text_asset(obj: Any, dest: Path) -> None:
    """导出 Unity TextAsset。"""
    data = obj.read()
    script = data.m_Script
    if isinstance(script, str):
        dest.write_text(script, encoding="utf-8")
    else:
        dest.write_bytes(bytes(script))


def write_image_asset(obj: Any, dest: Path) -> None:
    """导出 Unity Texture2D / Sprite 为图片文件。"""
    data = obj.read()
    image = getattr(data, "image", None)
    if image is None:
        raise RuntimeError("无法解码纹理（缺少 Pillow 或纹理解码器）")

    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = dest.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        image.convert("RGB").save(dest, format="JPEG", quality=95)
    elif suffix == ".webp":
        image.save(dest, format="WEBP", quality=95)
    else:
        image.save(dest)


def write_audio_asset(obj: Any, dest: Path, stem: str) -> None:
    """导出 Unity AudioClip 为音频文件。"""
    data = obj.read()
    samples = getattr(data, "samples", None)
    if not samples:
        raise RuntimeError("音频数据为空")

    if isinstance(samples, dict):
        key = stem if stem in samples else next(iter(samples))
        payload = samples[key]
    else:
        payload = samples

    if isinstance(payload, str):
        payload = payload.encode("utf-8", "surrogateescape")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)


def extract_asset(bundle_path: Path, file_name: str, dest: Path) -> str:
    """从单个 bundle 中导出一个资源文件。"""
    env = UnityPy.load(str(bundle_path))
    objects = list(_iter_objects(env))

    suffix = Path(file_name).suffix.lower()
    stem = Path(file_name).stem

    if suffix == ".json":
        candidates = [o for o in objects if o.type.name == "TextAsset"]
        for obj in _prefer_name_match(candidates, stem):
            write_text_asset(obj, dest)
            return "TextAsset"

    elif suffix in IMAGE_SUFFIXES:
        candidates = [o for o in objects if o.type.name in IMAGE_TYPES]
        for obj in _prefer_name_match(candidates, stem):
            try:
                write_image_asset(obj, dest)
            except Exception:
                continue
            return obj.type.name

    elif suffix in AUDIO_SUFFIXES:
        candidates = [o for o in objects if o.type.name in AUDIO_TYPES]
        for obj in _prefer_name_match(candidates, stem):
            try:
                write_audio_asset(obj, dest, stem)
            except Exception:
                continue
            return obj.type.name

    raise RuntimeError(f"No supported object found for {file_name} in {bundle_path.name}")
