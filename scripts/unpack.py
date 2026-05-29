#!/usr/bin/env python3
"""Phigros 自动解包脚本 - 用于 GitHub Actions CI。

流程：
1. 从好游快爆 API 获取最新版本信息
2. 与本地记录的版本比较
3. 若有更新：下载 APK → 解压 → 仅解包新增铺面 → 提交到仓库

增量策略：
- 不删除已有铺面，只补充新增的
- 通过比较 charts/ 目录已有的 song_id/Chart_*.json 与 catalog 中的铺面列表来确定新增
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

# 好游快爆 API
API_URL = "https://api.3839app.com/cdn/android/gameintro-home-1546-id-112696-packag--level-2.htm"

# 好游快爆客户端 UA
HYKB_UA = "Androidkb/1.5.8.007(android;PJX110;16;1080x2256;WiFi)"

# 项目路径
REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "version.json"
CHARTS_DIR = REPO_ROOT / "charts"

# 临时工作目录
WORK_DIR = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "phigros-unpack"


def fetch_latest_info() -> dict:
    """从好游快爆 API 获取 Phigros 最新版本信息。"""
    print("正在查询好游快爆 API...")
    resp = requests.get(API_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 100:
        raise RuntimeError(f"API 返回错误: {data}")

    downinfo = data["result"]["data"]["downinfo"]
    return {
        "version": downinfo["version"],
        "versioncode": downinfo["versioncode"],
        "apkurl": downinfo["apkurl"],
        "md5": downinfo["md5"],
        "size_byte": downinfo["size_byte"],
        "size_m": downinfo["size_m"],
    }


def load_local_version() -> dict | None:
    """读取本地记录的版本信息。"""
    if not VERSION_FILE.exists():
        return None
    return json.loads(VERSION_FILE.read_text(encoding="utf-8"))


def save_local_version(info: dict) -> None:
    """保存版本信息到本地。"""
    VERSION_FILE.write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def scan_existing_charts(charts_dir: Path) -> set[str]:
    """扫描已存在的铺面，返回 {song_id/Chart_EZ.json, ...} 集合。"""
    existing = set()
    if not charts_dir.exists():
        return existing
    for song_dir in charts_dir.iterdir():
        if not song_dir.is_dir():
            continue
        for chart_file in song_dir.glob("Chart_*.json"):
            existing.add(f"{song_dir.name}/{chart_file.name}")
    return existing


def download_apk(url: str, dest: Path, expected_md5: str | None = None, max_retries: int = 3) -> None:
    """使用好游快爆 UA 下载 APK，支持断点续传和 MD5 校验。"""
    print(f"正在下载 APK: {url}")
    print(f"目标路径: {dest}")

    headers = {"User-Agent": HYKB_UA}

    for attempt in range(1, max_retries + 1):
        try:
            existing_size = dest.stat().st_size if dest.exists() else 0
            req_headers = dict(headers)
            if existing_size > 0:
                req_headers["Range"] = f"bytes={existing_size}-"
                print(f"断点续传: 从 {existing_size} bytes 继续")

            resp = requests.get(url, headers=req_headers, stream=True, timeout=300)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            resume = existing_size > 0 and resp.status_code == 206
            if resume:
                total += existing_size
            elif resp.status_code == 200:
                existing_size = 0

            downloaded = existing_size
            md5 = hashlib.md5()

            mode = "r+b" if resume else "wb"
            if mode == "wb":
                dest.parent.mkdir(parents=True, exist_ok=True)

            with open(dest, mode) as f:
                if resume:
                    f.seek(0)
                    while chunk := f.read(1024 * 1024):
                        md5.update(chunk)
                    f.seek(0, 2)

                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    md5.update(chunk)
                    if total:
                        pct = downloaded / total * 100
                        mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        print(f"\r下载进度: {mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%)", end="", flush=True)

            print(f"\n下载完成: {dest} ({downloaded} bytes)")

            if expected_md5:
                actual_md5 = md5.hexdigest()
                if actual_md5 == expected_md5:
                    print(f"MD5 校验通过: {actual_md5}")
                else:
                    print(f"MD5 校验失败! 期望: {expected_md5}, 实际: {actual_md5}")
                    dest.unlink(missing_ok=True)
                    if attempt < max_retries:
                        print(f"第 {attempt}/{max_retries} 次尝试失败，5秒后重试...")
                        time.sleep(5)
                        continue
                    raise RuntimeError(f"MD5 校验失败: 期望 {expected_md5}, 实际 {actual_md5}")

            return

        except (requests.RequestException, RuntimeError) as exc:
            if attempt < max_retries:
                print(f"\n下载出错 (第 {attempt}/{max_retries} 次): {exc}")
                print("5秒后重试...")
                time.sleep(5)
            else:
                raise


def extract_apk_minimal(apk_path: Path, extract_dir: Path, needed_bundles: set[str] | None = None) -> Path:
    """最小化解压 APK - 只解压铺面解包所需的文件。

    如果传入 needed_bundles，则只解压这些 bundle（增量模式）。
    否则先解压 catalog.json，再根据 catalog 确定需要哪些铺面 bundle。
    """
    apk_dir = extract_dir / "apk"
    if apk_dir.exists():
        shutil.rmtree(apk_dir)
    apk_dir.mkdir(parents=True)

    print(f"正在解压 APK（最小化模式）: {apk_path}")

    # 第一步：解压 catalog.json
    catalog_target = apk_dir / "assets" / "aa" / "catalog.json"
    catalog_target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(apk_path, "r") as apk:
        if "assets/aa/catalog.json" in apk.namelist():
            with apk.open("assets/aa/catalog.json") as src, open(catalog_target, "wb") as dst:
                dst.write(src.read())
            print("已解压 catalog.json")
        else:
            raise RuntimeError("APK 中未找到 assets/aa/catalog.json")

    # 第二步：读取 catalog 确定铺面 bundle
    sys.path.insert(0, str(REPO_ROOT))
    from phigros_unpacker.catalog import load_catalog

    track_entries, _ = load_catalog(apk_dir)

    # 收集所有铺面相关 bundle
    chart_bundles = set()
    for entry in track_entries.values():
        file_name = entry["file_name"]
        if file_name.startswith("Chart_") and file_name.endswith(".json"):
            chart_bundles.add(entry["bundle"])

    # 增量模式：只解压需要的 bundle
    if needed_bundles is not None:
        chart_bundles = chart_bundles & needed_bundles
        print(f"增量模式: 需要解压 {len(chart_bundles)} 个新 bundle")

    print(f"共 {len(chart_bundles)} 个铺面 bundle 待解压")

    # 第三步：只解压铺面 bundle
    android_dir = apk_dir / "assets" / "aa" / "Android"
    android_dir.mkdir(parents=True, exist_ok=True)

    extracted = 0
    with zipfile.ZipFile(apk_path, "r") as apk:
        all_names = apk.namelist()
        for name in all_names:
            if not name.startswith("assets/aa/Android/"):
                continue
            bundle_name = name[len("assets/aa/Android/"):]
            if bundle_name in chart_bundles:
                target = apk_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with apk.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                extracted += 1

    print(f"已解压 {extracted}/{len(chart_bundles)} 个铺面 bundle")
    return apk_dir


def extract_charts_incremental(apk_dir: Path, output_dir: Path, existing_charts: set[str]) -> dict:
    """仅解包新增铺面到输出目录。

    existing_charts: 已存在的铺面集合，格式为 {song_id/Chart_EZ.json, ...}
    """
    sys.path.insert(0, str(REPO_ROOT))
    from phigros_unpacker import list_catalog_resources, extract_catalog_resource

    print("正在扫描铺面资源...")
    catalog_resources = list_catalog_resources(apk_dir)

    # 过滤出新增的铺面资源
    new_chart_resources = []
    for song in catalog_resources:
        for resource in song["resources"]:
            if resource["kind"] == "chart":
                key = f"{song['song_id']}/{resource['file_name']}"
                if key not in existing_charts:
                    new_chart_resources.append({**resource, "song_id": song["song_id"]})

    total_charts = sum(
        len([r for r in song["resources"] if r["kind"] == "chart"])
        for song in catalog_resources
    )

    print(f"铺面总数: {total_charts}, 已有: {len(existing_charts)}, 新增: {len(new_chart_resources)}")

    if not new_chart_resources:
        print("没有新增铺面")
        return {"extracted": 0, "failed": 0, "new_count": 0}

    output_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = 0
    for i, resource in enumerate(new_chart_resources, 1):
        try:
            result = extract_catalog_resource(apk_dir, resource, output_dir)
            success += 1
            print(f"  [{i}/{len(new_chart_resources)}] 新增: {resource['song_id']}/{resource['file_name']}")
        except Exception as exc:
            failed += 1
            print(f"  [{i}/{len(new_chart_resources)}] 失败: {resource['song_id']}/{resource['file_name']} - {exc}")

    print(f"解包完成: 新增 {success}, 失败 {failed}")
    return {"extracted": success, "failed": failed, "new_count": len(new_chart_resources)}


def git_commit_and_push(version_info: dict) -> bool:
    """提交铺面更新到仓库。"""
    print("正在提交更新到仓库...")

    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)

    subprocess.run(["git", "add", "charts/", "version.json"], check=True)

    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if result.returncode == 0:
        print("没有文件变更，跳过提交")
        return False

    version = version_info["version"]
    subprocess.run(
        ["git", "commit", "-m", f"feat: 更新 Phigros 铺面至 v{version}"],
        check=True,
    )
    subprocess.run(["git", "push"], check=True)
    print(f"已提交并推送 Phigros v{version} 铺面更新")
    return True


def main():
    print("=" * 60)
    print("Phigros 铺面自动更新工具")
    print("=" * 60)

    # 1. 获取最新版本
    latest = fetch_latest_info()
    print(f"最新版本: v{latest['version']} (code={latest['versioncode']}, {latest['size_m']})")

    # 2. 比较版本
    local = load_local_version()
    if local and local.get("version") == latest["version"]:
        print(f"本地版本已是最新 (v{local['version']})，无需更新")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("updated=false\n")
        return

    if local:
        print(f"版本变更: v{local['version']} -> v{latest['version']}")
    else:
        print(f"首次运行，将解包 v{latest['version']}")

    # 3. 扫描已有铺面（增量更新关键步骤）
    existing_charts = scan_existing_charts(CHARTS_DIR)
    print(f"已有铺面: {len(existing_charts)} 个")

    # 4. 下载 APK
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    apk_path = WORK_DIR / "phigros.apk"

    download_apk(latest["apkurl"], apk_path, expected_md5=latest.get("md5"))

    # 5. 最小化解压 APK
    apk_dir = extract_apk_minimal(apk_path, WORK_DIR)

    # 6. 增量解包铺面（只添加新增的，不删除已有的）
    result = extract_charts_incremental(apk_dir, CHARTS_DIR, existing_charts)

    # 7. 保存版本信息
    save_local_version(latest)

    # 8. 清理临时文件
    print("正在清理临时文件...")
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)

    # 9. 提交到仓库（仅在 CI 环境中）
    if os.environ.get("GITHUB_ACTIONS"):
        git_commit_and_push(latest)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"updated=true\n")
            f.write(f"version={latest['version']}\n")
            f.write(f"charts_extracted={result['extracted']}\n")

    print(f"\n更新完成! Phigros v{latest['version']} - 新增 {result['extracted']} 个铺面")


if __name__ == "__main__":
    main()
