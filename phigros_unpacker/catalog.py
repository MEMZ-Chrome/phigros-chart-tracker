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


def load_catalog(apk_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """读取 Addressables catalog，并筛出 Assets/Tracks 下的本地资源。"""
    catalog_path = apk_dir / "assets" / "aa" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    keys = parse_key_data(catalog["m_KeyDataString"])
    entries = parse_entries(catalog["m_EntryDataString"])

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
        bundle_name = keys[dependency_key]
        if not isinstance(bundle_name, str) or not bundle_name.endswith(".bundle"):
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
            "provider": provider,
            "resource_type": resource_type,
            "internal_id": internal_id,
            "dependency_hash": dep_hash,
            "data": data,
        }
        bundle_for_key[asset_key] = bundle_name

    return track_entries, bundle_for_key
