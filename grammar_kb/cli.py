"""命令行入口。

用法示例：
    grammar-kb ingest /path/讲义                    # 导入整个目录
    grammar-kb ingest /path/讲义/22.动词时态1....pdf # 导入单讲
    grammar-kb lecture 25                            # 输出第25讲 markdown（表格已还原）
    grammar-kb kp 142                                # 输出某知识点 markdown
    grammar-kb search "主将从现"                     # 全文检索知识点
    grammar-kb search "already" --category 时态      # 限定类别检索
    grammar-kb markers --category 时态               # 列出所有时态标志词
    grammar-kb markers --tense 现在完成时            # 某时态的标志词
    grammar-kb relation 主将从现                     # 含某关系的知识点
    grammar-kb stats                                 # 统计
"""
from __future__ import annotations

import argparse
import sys

from .ingest import ingest_dir, ingest_pdf, open_db
from .query import Query


def _print(s: str) -> None:
    sys.stdout.write(s)
    if not s.endswith("\n"):
        sys.stdout.write("\n")


def cmd_ingest(args) -> int:
    import os

    db = open_db(args.db)
    if os.path.isdir(args.target):
        results = ingest_dir(db, args.target)
    else:
        results = [ingest_pdf(db, args.target)]
    ok = fail = 0
    for r in results:
        flag = "✓" if r.ok else "✗"
        line = f"{flag} 第{r.lecture_number:>2}讲 {r.title}  kp={r.knowledge_points} markers={r.markers}"
        if not r.ok:
            line += f"  ERR: {r.error}"
            fail += 1
        else:
            ok += 1
        print(line)
    print(f"\n导入完成：成功 {ok}，失败 {fail}。")
    db.close()
    return 0 if fail == 0 else 1


def cmd_lecture(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    md = q.lecture_markdown(args.number)
    if md is None:
        print(f"未找到第 {args.number} 讲。", file=sys.stderr)
        db.close()
        return 1
    _print(md)
    db.close()
    return 0


def cmd_kp(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    md = q.kp_markdown(args.id)
    if md is None:
        print(f"未找到知识点 id={args.id}。", file=sys.stderr)
        db.close()
        return 1
    _print(md)
    db.close()
    return 0


def cmd_search(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    kps = q.search_kps(args.query, category=args.category, limit=args.limit)
    if not kps:
        print("（无命中）")
        db.close()
        return 0
    for kp in kps:
        tags = " ".join(f"#{t}" for t in kp.tags)
        print(f"[#{kp.id}] 第{kp.lecture_number}讲 · {kp.category} · {kp.title}  {tags}")
    print(f"\n共 {len(kps)} 条。")
    db.close()
    return 0


def cmd_markers(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    if args.tense:
        rows = q.markers_by_tense(args.tense)
    else:
        rows = q.markers_by_category(args.category)
    if not rows:
        print("（无标志词）")
        db.close()
        return 0
    cur_tense = None
    for r in rows:
        t = r.get("tense") or "—"
        if t != cur_tense:
            print(f"\n## {t}")
            cur_tense = t
        lec = r.get("lecture_number")
        lec_s = f"（第{lec}讲）" if lec else ""
        print(f"  - {r['marker']} {lec_s}")
    print(f"\n共 {len(rows)} 个标志词。")
    db.close()
    return 0


def cmd_relation(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    kps = q.kps_by_relation(args.type)
    if not kps:
        print("（无）")
        db.close()
        return 0
    for kp in kps:
        print(f"[#{kp.id}] 第{kp.lecture_number}讲 · {kp.title}")
    db.close()
    return 0


def cmd_stats(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    s = q.stats()
    print(f"讲次：{s['lectures']}")
    print(f"知识点：{s['knowledge_points']}")
    print(f"标志词：{s['markers']}")
    print("按类别：")
    for cat, n in sorted(s["by_category"].items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}")
    db.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grammar-kb", description="哈一语法讲义知识点库")
    p.add_argument("--db", default=None, help="SQLite 数据库路径（默认 data/grammar.db 或 $GRAMMAR_KB_DB）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="导入 PDF 文件或目录")
    pi.add_argument("target", help="PDF 文件或目录路径")
    pi.set_defaults(func=cmd_ingest)

    pl = sub.add_parser("lecture", help="输出某讲 markdown")
    pl.add_argument("number", type=int)
    pl.set_defaults(func=cmd_lecture)

    pk = sub.add_parser("kp", help="输出某知识点 markdown")
    pk.add_argument("id", type=int)
    pk.set_defaults(func=cmd_kp)

    ps = sub.add_parser("search", help="全文检索知识点")
    ps.add_argument("query")
    ps.add_argument("--category", default=None)
    ps.add_argument("--limit", type=int, default=50)
    ps.set_defaults(func=cmd_search)

    pm = sub.add_parser("markers", help="列出标志词")
    pm.add_argument("--category", default="时态")
    pm.add_argument("--tense", default=None)
    pm.set_defaults(func=cmd_markers)

    pr = sub.add_parser("relation", help="按关系类型查知识点")
    pr.add_argument("type", help="如 主将从现 / 时态呼应")
    pr.set_defaults(func=cmd_relation)

    pst = sub.add_parser("stats", help="统计")
    pst.set_defaults(func=cmd_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
