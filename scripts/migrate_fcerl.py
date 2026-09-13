#!/usr/bin/env python3
"""一次性迁移：FCEReadingLib（Node）→ grammar-kb 泛读馆（Python）。

读 FCEReadingLib 的 data/app.db（只读），把 malin 与 teacher 两个账号的
books / chapters / reading_progress / chapter_preps 迁到 grammar-kb 的
library.db，global_settings 的 dict_json + student_json 并入 library_settings
（ai_json 不迁，API Key 让用户重填）；uploads/books、uploads/covers 文件
复制到 data/library/ 并改写路径字段。按 title+file_size 幂等（已存在跳过）。

用法：
    python scripts/migrate_fcerl.py                     # 默认路径
    python scripts/migrate_fcerl.py --src ... --dst ...  # 自定义路径
不带 --commit 时为演练（dry-run），只打印将要执行的动作。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

# 只迁这两个账号的数据（users.id → username）
USER_MAP = {1: "teacher", 2: "malin"}

DEFAULT_SRC_DB = "/Users/maxiangyu/Code/FCEReadingLib/server/data/app.db"
DEFAULT_SRC_UPLOADS = "/Users/maxiangyu/Code/FCEReadingLib/server/uploads"
DEFAULT_DST_DB = str(Path(__file__).resolve().parent.parent / "data" / "library.db")
DEFAULT_DST_DIR = str(Path(__file__).resolve().parent.parent / "data" / "library")


def migrate(src_db: str, src_uploads: str, dst_db: str, dst_dir: str,
            commit: bool = False) -> int:
    src_db, src_uploads = Path(src_db), Path(src_uploads)
    dst_db, dst_dir = Path(dst_db), Path(dst_dir)
    if not src_db.is_file():
        print(f"[错误] 源库不存在: {src_db}")
        return 1
    if not dst_db.is_file():
        print(f"[错误] 目标库不存在: {dst_db}（先启动一次 grammar-kb-server 让其建库）")
        return 1

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(dst_db)
    dst.row_factory = sqlite3.Row
    dst.execute("PRAGMA foreign_keys = OFF")

    books_dir, covers_dir = dst_dir / "books", dst_dir / "covers"
    if commit:
        books_dir.mkdir(parents=True, exist_ok=True)
        covers_dir.mkdir(parents=True, exist_ok=True)

    copied, skipped, migrated_preps, migrated_progress = 0, 0, 0, 0
    try:
        # 幂等基准：目标库已有的 (title, file_size)
        existing = {
            (r["title"], r["file_size"])
            for r in dst.execute("SELECT title, file_size FROM books")
        }

        src_books = src.execute(
            f"SELECT * FROM books WHERE user_id IN ({','.join('?' * len(USER_MAP))})",
            tuple(USER_MAP),
        ).fetchall()

        for b in src_books:
            key = (b["title"], b["file_size"])
            if key in existing:
                print(f"  [跳过] 已存在: {b['title']}")
                skipped += 1
                continue

            # 复制 epub / 封面（保持原 16 位 hex 文件名，路径字段同步改写）
            src_epub = src_uploads / b["file_path"]
            dst_epub_rel = b["file_path"]  # 形如 books/xxxx.epub，目标结构一致
            dst_cover_rel = b["cover_path"]
            if not src_epub.is_file():
                print(f"  [警告] epub 缺失仍迁记录: {src_epub}")
            elif commit:
                shutil.copy2(src_epub, dst_dir / dst_epub_rel)
            if dst_cover_rel:
                src_cover = src_uploads / dst_cover_rel
                if src_cover.is_file() and commit:
                    shutil.copy2(src_cover, dst_dir / dst_cover_rel)

            owner = USER_MAP.get(b["user_id"], "teacher")
            cur = dst.execute(
                "INSERT INTO books (user, title, author, cover_path, file_path, file_size,"
                " chapter_count, added_at, last_opened_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (owner, b["title"], b["author"], dst_cover_rel, dst_epub_rel,
                 b["file_size"], b["chapter_count"], b["added_at"], b["last_opened_at"]),
            )
            new_book_id = cur.lastrowid

            rows = src.execute(
                "SELECT * FROM chapters WHERE book_id = ?", (b["id"],)
            ).fetchall()
            dst.executemany(
                "INSERT INTO chapters (book_id, idx, title, text_content, word_count,"
                " spine_index, skip_prep) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(new_book_id, r["idx"], r["title"], r["text_content"], r["word_count"],
                  r["spine_index"], r["skip_prep"]) for r in rows],
            )
            preps = src.execute(
                "SELECT * FROM chapter_preps WHERE book_id = ?", (b["id"],)
            ).fetchall()
            dst.executemany(
                "INSERT INTO chapter_preps (book_id, chapter_index, payload, model,"
                " difficulty, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(new_book_id, r["chapter_index"], r["payload"], r["model"],
                  r["difficulty"], r["created_at"]) for r in preps],
            )
            migrated_preps += len(preps)

            progs = src.execute(
                f"SELECT * FROM reading_progress WHERE book_id = ?"
                f" AND user_id IN ({','.join('?' * len(USER_MAP))})",
                (b["id"], *USER_MAP),
            ).fetchall()
            dst.executemany(
                "INSERT OR REPLACE INTO reading_progress (book_id, user, cfi, chapter_index,"
                " percent, reading_seconds, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(new_book_id, USER_MAP[r["user_id"]], r["cfi"], r["chapter_index"],
                  r["percent"], r["reading_seconds"], r["updated_at"]) for r in progs],
            )
            migrated_progress += len(progs)
            copied += 1
            print(f"  [{'迁移' if commit else '演练'}] {b['title']} → book_id={new_book_id}"
                  f"（章节 {len(rows)}，预习 {len(preps)}，进度 {len(progs)}）")

        # global_settings → library_settings：只并 dict_json + student_json（ai_json 不迁，key 重填）
        gs = src.execute("SELECT dict_json, student_json FROM global_settings WHERE id = 1").fetchone()
        if gs is not None:
            row = dst.execute(
                "SELECT ai_json, dict_json, student_json FROM library_settings WHERE id = 1"
            ).fetchone()
            ai = json.loads(row["ai_json"] or "{}") if row else {}
            dct = json.loads(row["dict_json"] or "{}") if row else {}
            student = json.loads(row["student_json"] or "{}") if row else {}
            dct.update({k: v for k, v in json.loads(gs["dict_json"] or "{}").items()})
            student.update({k: v for k, v in json.loads(gs["student_json"] or "{}").items()})
            if commit:
                if row is None:
                    dst.execute(
                        "INSERT INTO library_settings (id, ai_json, dict_json, student_json)"
                        " VALUES (1, ?, ?, ?)",
                        (json.dumps(ai), json.dumps(dct), json.dumps(student)),
                    )
                else:
                    dst.execute(
                        "UPDATE library_settings SET dict_json = ?, student_json = ? WHERE id = 1",
                        (json.dumps(dct), json.dumps(student)),
                    )
            print(f"  [{'迁移' if commit else '演练'}] 设置：dict={dct} student={student}（apiKey 不迁，需重填）")

        if commit:
            dst.commit()
            print(f"\n完成：迁书 {copied} 本，跳过 {skipped} 本；预习 {migrated_preps} 条，"
                  f"进度 {migrated_progress} 条。")
        else:
            print(f"\n演练完成（未写入）：将迁书 {copied} 本，跳过 {skipped} 本；"
                  f"预习 {migrated_preps} 条，进度 {migrated_progress} 条。加 --commit 执行。")
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FCEReadingLib → grammar-kb 泛读馆迁移")
    ap.add_argument("--src", default=DEFAULT_SRC_DB, help=f"源 app.db（默认 {DEFAULT_SRC_DB}）")
    ap.add_argument("--src-uploads", default=DEFAULT_SRC_UPLOADS,
                    help=f"源 uploads 目录（默认 {DEFAULT_SRC_UPLOADS}）")
    ap.add_argument("--dst", default=DEFAULT_DST_DB, help=f"目标 library.db（默认 {DEFAULT_DST_DB}）")
    ap.add_argument("--dst-dir", default=DEFAULT_DST_DIR,
                    help=f"目标 data/library 目录（默认 {DEFAULT_DST_DIR}）")
    ap.add_argument("--commit", action="store_true", help="实际写入（默认演练 dry-run）")
    args = ap.parse_args()
    return migrate(args.src, args.src_uploads, args.dst, args.dst_dir, commit=args.commit)


if __name__ == "__main__":
    sys.exit(main())
