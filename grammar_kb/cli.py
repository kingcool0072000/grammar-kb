"""命令行入口。

用法示例：
    grammar-kb ingest /path/讲义                    # 导入整个目录
    grammar-kb ingest /path/讲义/22.动词时态1....pdf # 导入单讲
    grammar-kb lecture 25                            # 输出第25讲 markdown（表格已还原）
    grammar-kb lecture 25 --format html              # 输出第25讲 HTML（表格渲染为 <table>）
    grammar-kb kp 142                                # 输出某知识点 markdown
    grammar-kb search "主将从现"                     # 全文检索知识点
    grammar-kb search "already" --category 时态      # 限定类别检索
    grammar-kb markers --category 时态               # 列出所有时态标志词
    grammar-kb markers --tense 现在完成时            # 某时态的标志词
    grammar-kb relation 主将从现                     # 含某关系的知识点
    grammar-kb stats                                 # 统计
    grammar-kb serve --port 8000                     # 启动 HTTP 查询服务（见 /docs）
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
    if args.format == "html":
        out = q.lecture_html(args.number)
    else:
        out = q.lecture_markdown(args.number)
    if out is None:
        print(f"未找到第 {args.number} 讲。", file=sys.stderr)
        db.close()
        return 1
    _print(out)
    db.close()
    return 0


def cmd_kp(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    if args.format == "html":
        out = q.kp_html(args.id)
    else:
        out = q.kp_markdown(args.id)
    if out is None:
        print(f"未找到知识点 id={args.id}。", file=sys.stderr)
        db.close()
        return 1
    _print(out)
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


def cmd_exam_signal(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    if args.list:
        print("考点信号维度：")
        for s in q.list_exam_signals():
            print(f"  - {s}")
        db.close()
        return 0
    kps = q.kps_by_exam_signal(args.signal)
    if not kps:
        print("（无）")
        db.close()
        return 0
    print(f"考点信号【{args.signal}】关联的知识点（{len(kps)}）：")
    for kp in kps:
        sigs = " ".join(f"#{s}" for s in kp.exam_signals)
        print(f"  [#{kp.id}] 第{kp.lecture_number}讲 · {kp.title}  {sigs}")
    db.close()
    return 0


def cmd_words(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    entries = q.vocabulary(limit=args.limit, min_freq=args.min_freq)
    if not entries:
        print("（单词表为空，请先 ingest）")
        db.close()
        return 0
    for e in entries:
        pos = "/".join(e["pos"]) or "?"
        meanings = "；".join(e["meanings"]) if e["meanings"] else "—"
        forms = " ".join(f"{k}={v}" for k, v in e["forms"].items())
        print(f"- {e['word']} [{pos}] (×{e['freq']})  释义：{meanings}")
        if forms:
            print(f"    变化：{forms}")
    print(f"\n共 {len(entries)} 词。")
    db.close()
    return 0


def cmd_stats(args) -> int:
    db = open_db(args.db)
    q = Query(db)
    s = q.stats()
    ds = s.get("dataset") or {}
    if ds:
        print(
            f"数据集：{ds.get('dataset_version', '?')} · 生成于 {ds.get('generated_at', '?')} · "
            f"{ds.get('lecture_count', '?')}讲/{ds.get('source_pdf_count', '?')}PDF · "
            f"代码 {ds.get('code_version', '?')}"
        )
    print(f"讲次：{s['lectures']}")
    print(f"知识点：{s['knowledge_points']}")
    print(f"标志词：{s['markers']}")
    print("按类别：")
    for cat, n in sorted(s["by_category"].items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}")
    db.close()
    return 0


def cmd_serve(args) -> int:
    try:
        from .server import run_app
    except ImportError as e:
        print(f"启动 HTTP 服务需要 server 依赖：{e}\n请运行 `uv sync --extra server` 后重试。", file=sys.stderr)
        return 1
    print(f"grammar-kb HTTP 服务启动：http://{args.host}:{args.port}/docs")
    run_app(host=args.host, port=args.port, db_path=args.db)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grammar-kb", description="PDF 讲义/教材知识点数据库")
    p.add_argument("--db", default=None, help="SQLite 数据库路径（默认 data/grammar.db 或 $GRAMMAR_KB_DB）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="导入 PDF 文件或目录")
    pi.add_argument("target", help="PDF 文件或目录路径")
    pi.set_defaults(func=cmd_ingest)

    pl = sub.add_parser("lecture", help="输出某讲 markdown/html")
    pl.add_argument("number", type=int)
    pl.add_argument("--format", "-f", choices=["markdown", "html"], default="markdown")
    pl.set_defaults(func=cmd_lecture)

    pk = sub.add_parser("kp", help="输出某知识点 markdown/html")
    pk.add_argument("id", type=int)
    pk.add_argument("--format", "-f", choices=["markdown", "html"], default="markdown")
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

    pes = sub.add_parser("exam-signal", help="考点信号：按考点维度反查知识点")
    pes.add_argument("signal", nargs="?", default="时态", help="如 时态/语态/从句/拼写")
    pes.add_argument("--list", action="store_true", help="列出所有考点信号维度")
    pes.set_defaults(func=cmd_exam_signal)

    pw = sub.add_parser("words", help="基于讲义语料的单词表")
    pw.add_argument("--limit", type=int, default=100)
    pw.add_argument("--min-freq", type=int, default=2)
    pw.set_defaults(func=cmd_words)

    pst = sub.add_parser("stats", help="统计")
    pst.set_defaults(func=cmd_stats)

    pse = sub.add_parser("serve", help="启动 HTTP 查询服务")
    pse.add_argument("--host", default="127.0.0.1")
    pse.add_argument("--port", type=int, default=8000)
    pse.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
