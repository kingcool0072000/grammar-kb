#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把词光 Word Atlas 的词条写入 grammar.db，并可选拉取插画和录音。

词条进 ``vocab_atlas``（grammar.db 的加表）。不修改 ``vocab_word`` 的级别，
不打开 fce.db，因此不碰背词进度。媒体默认落在
``data/atlas-cache/word-atlas/public/atlas/``（或 ``$GRAMMAR_KB_ATLAS_DIR``），
不提交进 git。``public/sfx`` 的界面音效版权不清楚，这里不拉取。

用法（在仓库根目录）::

    uv run python scripts/import_word_atlas.py
    uv run python scripts/import_word_atlas.py --media
    uv run python scripts/import_word_atlas.py --from /path/to/word-atlas --media

重复执行是安全的：词条按 word 覆盖更新，已存在且未变化的媒体文件会跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAW = "https://raw.githubusercontent.com/designfuchen/word-atlas/main"
JSON_URL = RAW + "/data/word-atlas.json"
INDEX_URL = RAW + "/public/atlas/index.json"
_UA = "hebaxue-word-atlas-import"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as handle:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
            tmp.replace(dest)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            if tmp.exists():
                tmp.unlink()
    raise SystemExit(f"下载失败 {url}：{last_err}")


def _load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _ensure_dataset(args, cache: Path) -> tuple[Path, Path | None]:
    """返回 (word-atlas.json, index.json 或 None)。"""
    if args.json:
        json_path = Path(args.json)
        index_path = Path(args.index) if args.index else None
        if not json_path.is_file():
            raise SystemExit(f"找不到词库文件：{json_path}")
        return json_path, index_path if index_path and index_path.is_file() else None

    if args.from_dir:
        base = Path(args.from_dir)
        json_path = base / "data" / "word-atlas.json"
        index_path = base / "public" / "atlas" / "index.json"
        if not json_path.is_file():
            raise SystemExit(f"--from 目录里没有 data/word-atlas.json：{base}")
        return json_path, index_path if index_path.is_file() else None

    json_path = cache / "word-atlas.json"
    index_path = cache / "index.json"
    print(f"下载词条 {JSON_URL}")
    _download(JSON_URL, json_path)
    print(f"下载索引 {INDEX_URL}")
    _download(INDEX_URL, index_path)
    return json_path, index_path


def _git_sync(repo: Path) -> str:
    """稀疏克隆插画和录音。重复执行会 fetch 后补齐缺失 blob。"""
    from grammar_kb.atlas import SOURCE_BRANCH, SOURCE_REPO

    repo.parent.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        print(f"克隆 {SOURCE_REPO} → {repo}")
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                "--branch", "main", SOURCE_REPO + ".git", str(repo),
            ],
            check=True,
        )
    else:
        print(f"更新 {repo}")
        subprocess.run(
            ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", SOURCE_BRANCH],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-f", "FETCH_HEAD"],
            check=True,
        )
    subprocess.run(
        [
            "git", "-C", str(repo), "sparse-checkout", "set",
            "public/atlas/img", "public/atlas/thumb", "public/atlas/audio",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
    ).strip()
    return revision


def main() -> None:
    from grammar_kb.atlas import (
        SOURCE_REPO,
        cache_repo_dir,
        import_entries,
        media_map_from_index,
        mirror_tree,
        resolve_atlas_dir,
    )
    from grammar_kb.db import GrammarDB
    from grammar_kb.ingest import default_db_path

    parser = argparse.ArgumentParser(description="导入词光 Word Atlas 词条与媒体")
    parser.add_argument("--db", default=None, help="grammar.db 路径（默认 $GRAMMAR_KB_DB 或 data/grammar.db）")
    parser.add_argument("--json", default=None, help="本地 word-atlas.json，跳过下载")
    parser.add_argument("--index", default=None, help="本地 index.json（媒体位标记）")
    parser.add_argument("--from", dest="from_dir", default=None, help="已有的 word-atlas 检出目录")
    parser.add_argument("--media", action="store_true", help="同时拉取插画和 mp3（体积约 700MB）")
    parser.add_argument(
        "--copy", action="store_true",
        help="媒体源目录和服务目录不同时，复制/硬链接过去（设置了 GRAMMAR_KB_ATLAS_DIR 时会自动做）",
    )
    args = parser.parse_args()

    cache = ROOT / "data" / "atlas-cache"
    json_path, index_path = _ensure_dataset(args, cache)
    entries = _load_json(json_path)
    if not isinstance(entries, list):
        raise SystemExit("word-atlas.json 应为数组")
    index = _load_json(index_path) if index_path else None
    flags = media_map_from_index(index)

    db_path = args.db or default_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    db = GrammarDB(db_path)
    try:
        stats = import_entries(
            db.conn, entries, flags, have_index=index is not None,
        )
        db.set_meta("atlas_count", str(stats["upserted"]))
        db.set_meta("atlas_source", SOURCE_REPO)
        db.set_meta("atlas_license", "CC BY 4.0")
        db.set_meta("atlas_attribution", "Word Atlas contributors（词光）")
        if index_path:
            db.set_meta("atlas_index", str(index_path))
    finally:
        db.close()

    print(
        f"词条已写入 {db_path} 的 vocab_atlas："
        f"{stats['upserted']} 条，跳过 {stats['skipped']} 条。"
        "未改 vocab_word，未打开 fce.db。"
    )

    if not args.media:
        print("未拉取媒体。需要插画和录音时再跑一次并加上 --media。")
        return

    if args.from_dir:
        src = Path(args.from_dir) / "public" / "atlas"
        revision = ""
        git_dir = Path(args.from_dir) / ".git"
        if git_dir.exists():
            revision = subprocess.check_output(
                ["git", "-C", args.from_dir, "rev-parse", "HEAD"], text=True,
            ).strip()
    else:
        revision = _git_sync(cache_repo_dir())
        src = cache_repo_dir() / "public" / "atlas"

    if not (src / "img").is_dir() and not (src / "audio").is_dir():
        raise SystemExit(f"媒体目录不存在：{src}")

    dest = resolve_atlas_dir()
    db = GrammarDB(db_path)
    try:
        if revision:
            db.set_meta("atlas_revision", revision)
        db.set_meta("atlas_media_src", str(src))
    finally:
        db.close()

    if src.resolve() == dest.resolve():
        print(f"媒体已在服务目录：{dest}")
        return

    custom = bool(os.environ.get("GRAMMAR_KB_ATLAS_DIR", "").strip())
    if not (args.copy or custom):
        print(
            "媒体在克隆目录，服务进程默认就会读这里，因此没有再复制一份：\n"
            f"  {src}\n"
            "若进程设置了别的 GRAMMAR_KB_ATLAS_DIR，请加上 --copy。"
        )
        return

    print(f"同步媒体 {src} → {dest}")
    mirrored = mirror_tree(src, dest)
    print(
        f"媒体同步完成：硬链接 {mirrored['linked']}，"
        f"复制 {mirrored['copied']}，跳过 {mirrored['skipped']}。"
    )


if __name__ == "__main__":
    main()
