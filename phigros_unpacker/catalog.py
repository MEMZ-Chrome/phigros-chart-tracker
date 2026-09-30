from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

from .constants import TRACK_RE


def parse_key_data(encoded: str) -> list[Any]:
    """解析 Addressables catalog 里的 m_KeyDataString。"""
    data = base64.b64decode(encoded)
    count = struct.unpack_from("<I", data, 0)[0]
    pos = 4
    keys: list[Any] = []

    for _ in range(count):
        if pos >= len(data):
            break
        type_id = data[pos]
        pos += 1

        if type_id not in (0, 1):
            break

        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        raw = data[pos : pos + length]
        pos += length
        encoding = "utf-8" if type_id == 0 else "utf-16le"
        keys.append(raw.decode(encoding, "replace"))

    return keys


def parse_entries(encoded: str) -> list[tuple[int, int, int, int, int, int, int]]:
    """解析 Addressables catalog 里的 m_EntryDataString。"""
    data = base64.b64decode(encoded)
    count = struct.unpack_from("<I", data, 0)[0]
    return [struct.unpack_from("<7i", data, 4 + i * 28) for i in range(count)]


def expand_internal_id(raw: Any, prefixes: list[Any]) -> str | None:
    """展开 m_InternalIds 里的条目（新版 Addressables 会用 "<前缀序号>:<路径>" 压缩）。"""
    if not isinstance(raw, str):
        return None
    if prefixes and ":" in raw:
        head, _, tail = raw.partition(":")
        if head.isdigit() and int(head) < len(prefixes):
            return f"{prefixes[int(head)]}{tail}"
    return raw


def resolve_bundle_name(internal_ids: list[Any], prefixes: list[Any], index: int, fallback: str) -> str:
    """取 bundle 在 APK 里的**真实文件名**。

    Addressables 的 entry.dependency_key 是 m_InternalIds 的下标，对应值形如
    ``{UnityEngine.AddressableAssets.Addressables.RuntimePath}/Android/<file>.bundle``，
    取 basename 才是磁盘上的文件名。

    从 Phigros v4.0.0 起，AssetBundle 名变成了 ``<hash>_<file>.bundle``（哈希前缀 + 文件名），
    与真实文件名不再一致，因此不能再用 m_KeyDataString 里的名字直接当文件名。
    """
    if 0 <= index < len(internal_ids):
        raw = expand_internal_id(internal_ids[index], prefixes)
        if raw and raw.endswith(".bundle"):
            return raw.rsplit("/", 1)[-1]
    return fallback


def load_catalog(apk_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """读取 Addressables catalog，并筛出 Assets/Tracks 下的本地资源。"""
    catalog_path = apk_dir / "assets" / "aa" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    keys = parse_key_data(catalog["m_KeyDataString"])
    entries = parse_entries(catalog["m_EntryDataString"])
    internal_ids = catalog.get("m_InternalIds") or []
    prefixes = catalog.get("m_InternalIdPrefixes") or []

    track_entries: dict[str, dict[str, Any]] = {}
    bundle_for_key: dict[str, str] = {}

    for internal_id, provider, dependency_key, dep_hash, data, primary_key, resource_type in entries:
        if primary_key < 0 or primary_key >= len(keys):
            continue
        asset_key = keys[primary_key]

        if not isinstance(asset_key, str) or not asset_key.startswith("Assets/Tracks/"):
            continue
        if dependency_key < 0 or dependency_key >= len(keys):
            continue
        bundle_key = keys[dependency_key]
        if not isinstance(bundle_key, str) or not bundle_key.endswith(".bundle"):
            continue
        bundle_name = resolve_bundle_name(internal_ids, prefixes, dependency_key, bundle_key)
        if not bundle_name.endswith(".bundle"):
            continue

        match = TRACK_RE.match(asset_key)
        if not match:
            continue
        song_id, file_name = match.groups()

        track_entries[asset_key] = {
            "asset_key": asset_key,
            "song_id": song_id,
            "file_name": file_name,
            "bundle": bundle_name,
            "bundle_key": bundle_key,
            "provider": provider,
            "resource_type": resource_type,
            "internal_id": internal_id,
            "dependency_hash": dep_hash,
            "data": data,
        }
        bundle_for_key[asset_key] = bundle_name

    return track_entries, bundle_for_key
