from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .catalog import load_catalog
from .constants import CHART_LEVELS, IMAGE_FILES
from .extractors import extract_asset
from .utils import safe_name


def resource_kind(file_name: str) -> dict[str, str]:
    """根据 Track 文件名识别资源类型。"""
    lower_name = file_name.lower()
    stem = file_name.rsplit(".", 1)[0]

    if lower_name in {"music.wav", "music.ogg", "music.mp3", "music.fsb"}:
        return {"kind": "music"}
    if lower_name.startswith("music."):
        return {"kind": "music"}

    if file_name in IMAGE_FILES.values():
        return {"kind": "image", "image": file_name}
    if lower_name.endswith((".jpg", ".jpeg", ".png", ".tga", ".webp")):
        return {"kind": "image", "image": file_name}
    if "illustration" in lower_name or "cover" in lower_name:
        return {"kind": "image", "image": file_name}

    if lower_name.startswith("chart_") and lower_name.endswith(".json"):
        return {
            "kind": "chart",
            "chart": stem[len("Chart_"):],
        }
    return {"kind": "other"}


def catalog_resource_info(entry: dict[str, Any]) -> dict[str, Any]:
    """把 catalog 条目整理成对外返回的资源信息。"""
    return {
        "asset_key": entry["asset_key"],
        "file_name": entry["file_name"],
        "bundle": entry["bundle"],
        "provider": entry["provider"],
        "resource_type": entry["resource_type"],
        "internal_id": entry["internal_id"],
        "dependency_hash": entry["dependency_hash"],
        "data": entry["data"],
        **resource_kind(entry["file_name"]),
    }


def group_catalog_resources(
    track_entries: dict[str, dict[str, Any]],
    song_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """按 songsId 聚合 catalog 中的 Track 资源。"""
    by_song: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in track_entries.values():
        if entry["song_id"].startswith("#"):
            continue
        if song_ids and entry["song_id"] not in song_ids:
            continue
        by_song[entry["song_id"]].append(entry)

    return [
        {
            "song_id": song_id,
            "resources": [
                catalog_resource_info(entry)
                for entry in sorted(by_song[song_id], key=lambda item: item["file_name"])
            ],
        }
        for song_id in sorted(by_song)
    ]


def list_catalog_resources(apk_dir: Path, song_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """只基于 Addressables catalog 返回可拆资源列表。"""
    track_entries, _ = load_catalog(apk_dir.resolve())
    return group_catalog_resources(track_entries, song_ids)


def extract_catalog_resource(apk_dir: Path, resource: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """按 list_catalog_resources 返回的单个资源信息精确导出资源。"""
    apk_dir = apk_dir.resolve()
    output_dir = output_dir.resolve()
    song_id = resource["asset_key"].split("/")[-2]
    dest = output_dir / safe_name(song_id, song_id) / safe_name(resource["file_name"], resource["file_name"])
    bundle_path = apk_dir / "assets" / "aa" / "Android" / resource["bundle"]

    dest.parent.mkdir(parents=True, exist_ok=True)
    object_type = extract_asset(bundle_path, resource["file_name"], dest)
    return {
        "song_id": song_id,
        "file_name": resource["file_name"],
        "kind": resource["kind"],
        "bundle": resource["bundle"],
        "output": str(dest.relative_to(output_dir)),
        "object_type": object_type,
        "status": "ok",
    }
