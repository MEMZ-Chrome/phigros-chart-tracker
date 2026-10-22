#!/usr/bin/env python3
"""Phigros 自动解包脚本 - 用于 GitHub Actions CI。

流程：
1. 从 API 获取最新版本信息
2. 与本地记录的版本比较
3. 若有更新：下载 APK → 解压 → 仅解包新增谱面 → 提交到仓库

增量策略：
- 不删除已有谱面，只补充新增的
- 通过比较 charts/ 目录已有的 song_id/Chart_*.json 与 catalog 中的资源列表来确定新增
- 只提取谱面 JSON。曲绘/音频体积过大（全量一次提交会超过 GitHub 2GB 的 push 上限），不进仓库
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

# API 地址
API_URL = "https://api.3839app.com/cdn/android/gameintro-home-1546-id-112696-packag--level-2.htm"

# 客户端 UA
DEFAULT_UA = "Androidkb/1.5.8.007(android;PJX110;16;1080x2256;WiFi)"

# 项目路径
REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "version.json"
CHARTS_DIR = REPO_ROOT / "charts"

# 临时工作目录
WORK_DIR = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "phigros-unpack"


def fetch_latest_info() -> dict:
    """从 API 获取 Phigros 最新版本信息。"""
    print("正在获取 API 最新版本信息...")
    resp = requests.get(API_URL, headers={"User-Agent": DEFAULT_UA}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    downinfo = data["result"]["data"]["downinfo"]
    return {
        "version": downinfo["version"],
        "versioncode": downinfo["versioncode"],
        "apkurl": downinfo["apkurl"],
        "md5": downinfo["md5"],
        "size_byte": int(downinfo["size_byte"]),
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


def _download_progress(downloaded: int, total: int) -> None:
    """打印单行下载进度。"""
    if not total:
        return
    pct = downloaded / total * 100
    mb = downloaded / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    print(f"\r下载进度: {mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%)", end="", flush=True)


def download_apk(
    url: str,
    dest: Path,
    expected_md5: str | None = None,
    expected_size: int | None = None,
    max_retries: int = 10,
    stale_timeout: int = 120,
    max_network_errors: int = 5,
) -> None:
    """下载 APK，支持断点续传和 MD5 校验。

    网络中断、传输超时、长度不符都不会直接失败：已经落盘的数据会保留在 dest，
    下一轮用 Range 请求从断点继续。只有以下情况才放弃：
    - 连续 max_retries 轮没有任何进展
    - MD5 连续 max_retries 次校验失败（说明源文件已损坏或已变更）
    """
    print(f"正在下载 APK: {url}")
    print(f"目标路径: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    def known_total() -> int:
        """优先用 API 给出的文件大小，拿不到时用 Range 探测。"""
        if expected_size:
            return expected_size
        try:
            resp = requests.head(url, headers={"User-Agent": DEFAULT_UA}, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            return int(resp.headers.get("content-length", 0) or 0)
        except requests.RequestException:
            return 0

    total = known_total()
    print(f"文件总大小: {total} bytes")

    stalls = 0
    network_errors = 0
    md5_failures = 0
    # 每轮最多尝试的续传次数 + 没有进度的次数统计
    while stalls < max_retries and network_errors < max_network_errors:
        existing_size = dest.stat().st_size if dest.exists() else 0

        if total:
            if existing_size == total:
                pass  # 已经下载完整，直接进入校验
            elif existing_size > total:
                print(f"本地文件比预期大 ({existing_size} > {total})，删除后重新下载")
                dest.unlink(missing_ok=True)
                existing_size = 0

        if total and existing_size == total:
            print(f"文件已完整下载 ({existing_size} bytes)，开始校验")
        else:
            if total and existing_size > 0:
                print(f"断点续传: 从 {existing_size} bytes 继续 ({existing_size / total * 100:.1f}%)")

            req_headers = {"User-Agent": DEFAULT_UA}
            if existing_size > 0:
                req_headers["Range"] = f"bytes={existing_size}-"

            try:
                with requests.get(
                    url, headers=req_headers, stream=True, timeout=(30, stale_timeout)
                ) as resp:
                    resp.raise_for_status()

                    if resp.status_code == 206:
                        content_range = resp.headers.get("content-range", "")
                        # Content-Range: bytes 0-123/456
                        if "/" in content_range:
                            total = int(content_range.rsplit("/", 1)[1]) or total
                        mode = "ab"
                    else:
                        # 服务端忽略了 Range，只能从头来
                        if existing_size > 0:
                            print("服务端不支持断点续传，重新下载")
                        existing_size = 0
                        length = int(resp.headers.get("content-length", 0) or 0)
                        if length:
                            total = length
                        mode = "wb"

                    written = existing_size
                    with open(dest, mode) as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            f.write(chunk)
                            written += len(chunk)
                            _download_progress(written, total)

                    print()
                    if total and written < total:
                        raise ConnectionError(f"连接提前关闭: 已下载 {written}/{total} bytes")

            except (
                requests.RequestException,
                ConnectionError,
                TimeoutError,
                OSError,
            ) as exc:
                current = dest.stat().st_size if dest.exists() else 0
                if current > existing_size:
                    # 有进展：保留已下载的数据，立刻续传
                    print(f"\n下载中断 (已保留 {current} bytes): {exc}")
                    if total:
                        print(f"已保存 {current / total * 100:.1f}%，继续续传...")
                    network_errors = 0
                    stalls = 0
                    time.sleep(2)
                else:
                    # 没有进展：可能是连接一直连不上，退避重试
                    stalls += 1
                    network_errors += 1
                    wait = min(60, 5 * network_errors)
                    print(f"\n下载出错 (无进度 {stalls}/{max_retries}, {wait}秒后重试): {exc}")
                    time.sleep(wait)
                continue

        # 走到这里说明文件长度已达标，做 MD5 校验
        if expected_md5:
            actual_md5 = compute_md5(dest)
            if actual_md5 == expected_md5:
                print(f"MD5 校验通过: {actual_md5}")
                return
            md5_failures += 1
            print(f"MD5 校验失败 ({md5_failures}/{max_retries})! 期望: {expected_md5}, 实际: {actual_md5}")
            dest.unlink(missing_ok=True)
            if md5_failures >= max_retries:
                raise RuntimeError(f"MD5 校验失败: 期望 {expected_md5}, 实际 {actual_md5}")
            print("删除文件后重新下载...")
            time.sleep(5)
            stalls += 1
            continue

        print(f"\n下载完成: {dest}")
        return

    actual_size = dest.stat().st_size if dest.exists() else 0
    raise RuntimeError(
        f"下载失败: {url}（已下载 {actual_size}/{total or '?'} bytes）"
    )


def compute_md5(path: Path) -> str:
    """分块计算文件 MD5，避免把 2GB+ 的文件整个读进内存。"""
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            md5.update(chunk)
    return md5.hexdigest()


def extract_apk_minimal(apk_path: Path, extract_dir: Path, needed_bundles: set[str] | None = None) -> Path:
    """最小化解压 APK - 只解压谱面所需的 bundle 文件。

    如果传入 needed_bundles，则只解压这些 bundle（增量模式）。
    否则先解压 catalog.json，再根据 catalog 确定需要哪些谱面 bundle。
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

    # 第二步：读取 catalog 确定谱面 bundle
    sys.path.insert(0, str(REPO_ROOT))
    from phigros_unpacker.catalog import load_catalog

    track_entries, _ = load_catalog(apk_dir)

    chart_bundles = set()
    for entry in track_entries.values():
        file_name = entry["file_name"]
        if file_name.startswith("Chart_") and file_name.endswith(".json"):
            chart_bundles.add(entry["bundle"])

    # 增量模式：只解压需要的 bundle
    if needed_bundles is not None:
        chart_bundles = chart_bundles & needed_bundles
        print(f"增量模式: 需要解压 {len(chart_bundles)} 个新 bundle")

    print(f"共 {len(chart_bundles)} 个谱面 bundle 待解压")

    # 第三步：只解压谱面 bundle
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

    print(f"已解压 {extracted}/{len(chart_bundles)} 个谱面 bundle")

    # 一个都没解出来说明 catalog 里的 bundle 名与 APK 内的实际文件名对不上
    # （例如命名规则变化），此时必须显式失败，不能带着 0 个 bundle 继续往下走
    if chart_bundles and extracted == 0:
        raise RuntimeError(
            f"catalog 声明的 {len(chart_bundles)} 个谱面 bundle 在 APK 中一个都没找到，"
            "bundle 命名规则可能已变化，请检查 catalog 解析"
        )

    return apk_dir


def extract_charts_incremental(apk_dir: Path, output_dir: Path, existing_charts: set[str]) -> dict:
    """仅解包新增谱面到输出目录。

    existing_charts: 已存在的谱面集合，格式为 {song_id/Chart_EZ.json, ...}
    """
    sys.path.insert(0, str(REPO_ROOT))
    from phigros_unpacker import list_catalog_resources, extract_catalog_resource

    print("正在扫描谱面资源...")
    catalog_resources = list_catalog_resources(apk_dir)

    # 过滤出新增的谱面资源
    new_chart_resources = []
    for song in catalog_resources:
        for resource in song["resources"]:
            if resource["kind"] != "chart":
                continue
            key = f"{song['song_id']}/{resource['file_name']}"
            if key not in existing_charts:
                new_chart_resources.append({**resource, "song_id": song["song_id"]})

    total_charts = sum(
        len([r for r in song["resources"] if r["kind"] == "chart"])
        for song in catalog_resources
    )

    print(f"谱面总数: {total_charts}, 已有: {len(existing_charts)}, 新增: {len(new_chart_resources)}")

    if not new_chart_resources:
        print("没有新增谱面")
        return {"extracted": 0, "failed": 0, "new_count": 0}

    output_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = 0
    for i, resource in enumerate(new_chart_resources, 1):
        try:
            result = extract_catalog_resource(apk_dir, resource, output_dir)
            success += 1
            print(f"  [{i}/{len(new_chart_resources)}] 谱面: {resource['song_id']}/{resource['file_name']}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"  [{i}/{len(new_chart_resources)}] 失败: {resource['song_id']}/{resource['file_name']} - {exc}", flush=True)

    print(f"解包完成: 成功 {success}, 失败 {failed}")
    return {"extracted": success, "failed": failed, "new_count": len(new_chart_resources)}


def _target_branch() -> str:
    """确定推送目标分支：优先用触发本次运行的分支，否则回退到 main。"""
    return (
        os.environ.get("GITHUB_REF_NAME")
        or os.environ.get("GITHUB_HEAD_REF")
        or "main"
    )


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
    # Actions 里的 checkout 可能处于 detached HEAD，显式指定远端和分支
    subprocess.run(
        ["git", "push", "origin", f"HEAD:{_target_branch()}"],
        check=True,
    )
    print(f"已提交并推送 Phigros v{version} 铺面更新")
    return True


def main():
    print("=" * 60)
    print("Phigros 谱面自动更新工具")
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

    download_apk(
        latest["apkurl"],
        apk_path,
        expected_md5=latest.get("md5"),
        expected_size=latest.get("size_byte"),
    )

    # 5. 最小化解压 APK（只取谱面 bundle）
    apk_dir = extract_apk_minimal(apk_path, WORK_DIR)

    # 6. 增量解包（只添加新增的，不删除已有的）
    result = extract_charts_incremental(apk_dir, CHARTS_DIR, existing_charts)

    # 全部失败时不要把版本标记为已处理：否则下次运行会直接跳过，缺失的谱面永远补不回来
    if result.get("new_count", 0) > 0 and result.get("extracted", 0) == 0:
        raise RuntimeError(
            f"{result['new_count']} 个新增谱面全部提取失败，已保留 "
            f"v{local['version'] if local else '（无）'} 版本号，下次运行会重试"
        )

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
            f.write(f"charts_extracted={result.get('new_count', 0)}\n")

    print(f"\n更新完成! Phigros v{latest['version']}")
    print(f"  本次新增谱面: {result.get('new_count', 0)} (成功 {result.get('extracted', 0)}, 失败 {result.get('failed', 0)})")


if __name__ == "__main__":
    main()
