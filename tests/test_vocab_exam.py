"""词汇级别考试测试：解锁规则 / 成绩登记 / 考卷生成 / 权限。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.clock import today_cn  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402
from grammar_kb.vocab_exam import MAX_UNLOCK_LEVEL, PASS_SCORE  # noqa: E402

# 旧版卷的 structure 回填逻辑在一次性脚本里（vocab_paper 的现场还原
# fallback 已删）。这里直接测脚本里的同一份实现。
_SPEC = importlib.util.spec_from_file_location(
    "oneoff_legacy_backfill_vex",
    Path(__file__).resolve().parent.parent / "scripts"
    / "oneoff_2026-11_legacy_backfill.py")
_oneoff = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_oneoff)


@pytest.fixture()
def vex_env(tmp_path, monkeypatch):
    """隔离用户/成绩库；grammar.db 用仓库真实词表（考卷生成需 50 词）。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    from pathlib import Path
    # vocab_exam.load_level_words 走 ingest 默认路径（仓库 data/grammar.db）
    real_grammar = Path(__file__).resolve().parent.parent / "data" / "grammar.db"
    return TestClient(create_app(str(real_grammar), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _archive_paper(env, paper_id, level=0, user=""):
    """往 fce.db 放一份已存档卷，供登记成绩引用。"""
    con = sqlite3.connect(env / "fce.db")
    con.execute(
        "CREATE TABLE IF NOT EXISTS vocab_paper_history ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, level INTEGER NOT NULL,"
        " paper_id TEXT NOT NULL UNIQUE, words TEXT NOT NULL, created_at TEXT)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(vocab_paper_history)")}
    if "user" not in cols:
        con.execute("ALTER TABLE vocab_paper_history ADD COLUMN user TEXT DEFAULT ''")
    if "scope" not in cols:
        con.execute("ALTER TABLE vocab_paper_history ADD COLUMN scope TEXT")
    con.execute(
        "INSERT OR REPLACE INTO vocab_paper_history"
        " (level, paper_id, words, created_at, user, scope)"
        " VALUES (?,?,?,?,?,?)",
        (int(level), paper_id, '["w"]', "2026-10-01T00:00:00+00:00", user,
         "learned" if int(level) < 0 else "level"))
    con.commit()
    con.close()


def _require_level_words(monkeypatch):
    """考卷/词级统计依赖仓库 data/grammar.db 的 vocab_word。

    该库是本地 ingest 产物，不入库。CI 把 GRAMMAR_KB_DB 指到空的 tmp
    库（防止裸 create_app 在 data/ 下造空库），load_level_words 会跟
    环境变量走——有真库时改指过去，没有则跳过，与 taxonomy 用例同口径。
    """
    db = Path(__file__).resolve().parent.parent / "data" / "grammar.db"
    if not db.is_file():
        pytest.skip("data/grammar.db 不存在（本地构建产物，未入库），跳过依赖词表的用例")
    try:
        with sqlite3.connect(str(db)) as con:
            has = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vocab_word'"
            ).fetchone()
            n = (con.execute("SELECT COUNT(*) FROM vocab_word WHERE level = 0").fetchone()[0]
                 if has else 0)
    except sqlite3.Error:
        pytest.skip("data/grammar.db 无法读取 vocab_word，跳过依赖词表的用例")
    if n < 50:
        pytest.skip(f"data/grammar.db L0 词不足 50（现有 {n}），跳过依赖词表的用例")
    monkeypatch.setenv("GRAMMAR_KB_DB", str(db))


def test_exam_unlock_flow(vex_env, monkeypatch):
    _require_level_words(monkeypatch)
    """L0 恒开 → L0 考 82 分解锁 L1 → 查询/词汇级数联动。"""
    c = _client(vex_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 初始：学生解锁 L0
    d = c.get("/vocab-exams", headers=hs).json()["data"]
    assert d["unlocked_level"] == 0 and d["passed_levels"] == []
    lv = c.get("/vocab-levels?max_level=5", headers=hs).json()["data"]
    assert lv["unlocked"] == 0
    # 登记 82 分（≥80 通过）。必须指向已存档试卷。
    _archive_paper(vex_env, "L0-unlock", 0)
    r = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 82, "exam_date": "2026-10-11",
        "paper_id": "L0-unlock"})
    assert r.status_code == 200 and r.json()["data"]["passed"] is True
    assert r.json()["data"]["unlocked_level"] == 1
    lv = c.get("/vocab-levels?max_level=5", headers=hs).json()["data"]
    assert lv["unlocked"] == 1 and "1" in lv["counts"]
    # 不及格不解锁
    _archive_paper(vex_env, "L1-unlock", 1)
    c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 1, "score": 79, "exam_date": "2026-10-12",
        "paper_id": "L1-unlock"})
    d = c.get("/vocab-exams", headers=hs).json()["data"]
    assert d["unlocked_level"] == 1 and 1 not in d["passed_levels"]
    # 教师不受限
    lv = c.get("/vocab-levels?max_level=5", headers=ht).json()["data"]
    assert lv["unlocked"] == 5


def test_exam_validation_and_perm(vex_env):
    c = _client(vex_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 学生不能登记、不能看按题错词明细（教师专属端点）
    assert c.post("/vocab-exams", headers=hs, json={
        "user": "malin", "level": 0, "score": 99,
        "exam_date": "2026-10-11"}).status_code == 403
    assert c.get("/vocab-exams/1", headers=hs).status_code == 403
    # 参数校验
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 9, "score": 80,
        "exam_date": "2026-10-11"}).status_code == 422
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 120,
        "exam_date": "2026-10-11"}).status_code == 422
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 80,
        "exam_date": "2026/10/11"}).status_code == 422
    # 删除不存在 404
    assert c.delete("/vocab-exams/999", headers=ht).status_code == 404


def test_level_query_cannot_bypass_unlock(vex_env):
    """?level= 不能越过学生解锁上限，也不能把 unlocked 改写成所请求的级别。"""
    db = vex_env / "grammar.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE vocab_word (word TEXT, level INTEGER, pos TEXT, gloss TEXT,"
        " meanings TEXT, example TEXT, extra TEXT)"
    )
    con.executemany(
        "INSERT INTO vocab_word VALUES (?,?,?,?,?,?,?)",
        [
            ("apple", 0, "[]", "苹果", "[]", "{}", "{}"),
            ("orbit", 3, "[]", "轨道", "[]", "{}", "{}"),
        ],
    )
    con.commit()
    con.close()
    c = TestClient(create_app(str(db), str(vex_env / "exam.db"),
                              fce_db_path=str(vex_env / "fce.db")))
    hs = _login(c, "malin", "123456")
    locked = c.get("/vocab-levels?max_level=7&level=3", headers=hs)
    assert locked.status_code == 200, locked.text
    body = locked.json()["data"]
    assert body["unlocked"] == 0
    assert body["items"] == []
    assert body["counts"].get("3") == 1 or body["counts"].get(3) == 1
    own = c.get("/vocab-levels?max_level=7", headers=hs).json()["data"]
    assert [w["word"] for w in own["items"]] == ["apple"]
    ht = _login(c, "teacher", "123456")
    teacher = c.get("/vocab-levels?max_level=7&level=3", headers=ht).json()["data"]
    assert [w["word"] for w in teacher["items"]] == ["orbit"]
    assert teacher["unlocked"] == 7


def _rich_rows(names, level=0):
    """带变形与例句的富词条（不读 grammar.db）：form/ctx 两段都可出题。"""
    return [{
        "word": w, "level": level, "pos": '["v","n"]', "pos_list": ["v", "n"],
        "pos_cn": "v. 动词 / n. 名词",
        "gloss": f"{w}的释义", "gloss_clean": f"{w}的释义",
        "forms": {"past": f"{w}ed", "plural": f"{w}s"},
        "example": {"en": f"We {w} it together.", "zh": f"{w}的例句"},
    } for w in names]


def test_paper_structure_roundtrip(tmp_path, monkeypatch):
    """存什么渲染什么：generate(save=True) 落库的 sections 与 get_paper
    取回的逐项一致（题型/顺序/题面/答案）。

    真 Bug 回归：旧版落库已 shuffle 的词序、取回按位置还原分段，导致
    分段错乱；新版 structure 列存整卷，取回即所见。
    """
    import grammar_kb.vocab_paper as vp
    names = [f"rw{i:03d}" for i in range(60)]
    rich = _rich_rows(names)
    monkeypatch.setattr(vp, "_load_words_rich", lambda level: rich)
    db = str(tmp_path / "fce.db")
    paper = vp.generate_paper(0, save=True, seed=11, fce_db_path=db)
    got = vp.get_paper(paper["paper_id"], fce_db_path=db)
    assert got is not None
    assert got["sections"] == paper["sections"]
    assert got["words"] == paper["words"]
    assert got["config"] == paper["config"]
    assert got["level"] == 0 and got["scope"] == "level"
    # 落库行本身就是整卷：structure 列含 sections，words 列是词序
    with sqlite3.connect(db) as con:
        structure, words_raw = con.execute(
            "SELECT structure, words FROM vocab_paper_history"
            " WHERE paper_id = ?", (paper["paper_id"],)).fetchone()
    assert json.loads(structure)["sections"] == paper["sections"]
    assert json.loads(words_raw) == paper["words"]


def test_paper_legacy_structure_backfill(tmp_path, monkeypatch):
    """旧版卷（只存 50 词词序、structure 空）由一次性脚本回填 structure：
    按 20 中译英 / 20 英译中 / 末段拼写位置还原，写回 structure/config 列；
    get_paper 取回即所见；脚本重跑 0 行（幂等）。"""
    import grammar_kb.vocab_paper as vp
    names = [f"old{i:02d}" for i in range(50)]
    monkeypatch.setattr(vp, "_load_words_rich", lambda level: _rich_rows(names))
    db = str(tmp_path / "fce.db")
    vp.VocabPaperStore(db)  # 建表 + 幂等补列（模拟生产库迁移后的形态）
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO vocab_paper_history (level, paper_id, words, created_at)"
            " VALUES (?,?,?,?)",
            (0, "L0-20251101010101-0001", json.dumps(names),
             "2025-11-01T00:00:00"))
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    assert _oneoff.backfill_paper_structure(con) == (1, 0)
    con.commit()
    con.close()
    paper = vp.get_paper("L0-20251101010101-0001", fce_db_path=db)
    assert paper["words"] == names
    assert [s["type"] for s in paper["sections"]] == ["zh2en", "en2zh", "spell"]
    assert [it["word"] for it in paper["sections"][0]["items"]] == names[:20]
    assert [it["word"] for it in paper["sections"][1]["items"]] == names[20:40]
    spell = paper["sections"][2]["items"]
    assert [it["word"] for it in spell] == names[40:]
    for it, w in zip(spell, names[40:]):
        assert it["blanked"].startswith(w[0]) and "_" in it["blanked"]
        assert it["answer"] and it["answer"] not in it["blanked"]
    # 挖空可复现：同一卷再取回，位置一致
    again = vp.get_paper("L0-20251101010101-0001", fce_db_path=db)
    assert [it["blanked"] for it in again["sections"][2]["items"]] == \
        [it["blanked"] for it in spell]
    # 旧卷配置按位置口径回填
    assert paper["config"]["total"] == 50
    assert paper["config"]["ratios"] == {"form": 0, "spell": 10, "ctx": 0,
                                         "zh2en": 20, "en2zh": 20}
    # 幂等：structure 已非空，重跑不重复处理
    con = sqlite3.connect(db)
    assert _oneoff.backfill_paper_structure(con) == (0, 0)
    con.close()


def test_paper_seed_reproducible(tmp_path, monkeypatch):
    """同 seed 两次 generate：词序与 sections（含挖空位置、变形键）一致。"""
    import grammar_kb.vocab_paper as vp
    names = [f"sd{i:03d}" for i in range(60)]
    rich = _rich_rows(names)
    monkeypatch.setattr(vp, "_load_words_rich", lambda level: rich)
    db = str(tmp_path / "fce.db")
    p1 = vp.generate_paper(0, save=False, seed=42, fce_db_path=db)
    p2 = vp.generate_paper(0, save=False, seed=42, fce_db_path=db)
    assert p1["seed"] == 42
    assert p1["words"] == p2["words"]
    assert p1["sections"] == p2["sections"]


def test_paper_sections_complete_and_total_scale(tmp_path, monkeypatch):
    """造词库数据出卷：五段齐全且顺序固定；total=40 按最大余数法缩放
    各段配比（6/8/12/12/12 → 5/6/10/9/10）。"""
    import grammar_kb.vocab_paper as vp
    db = str(tmp_path / "grammar.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE vocab_word (word TEXT, level INTEGER, pos TEXT,"
        " gloss TEXT, extra TEXT, example TEXT)")
    rows = []
    for i in range(60):
        w = f"sc{i:03d}"
        rows.append((
            w, 3, '["v","n"]', f"{w}的释义",
            json.dumps({"forms": {"past": f"{w}ed", "plural": f"{w}s"}}),
            json.dumps({"en": f"We {w} it together.", "zh": "例句"},
                       ensure_ascii=False),
        ))
    con.executemany("INSERT INTO vocab_word VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    monkeypatch.setattr(vp, "_grammar_db_path", lambda: db)

    paper = vp.generate_paper(3, save=False, fce_db_path=db)
    assert [s["type"] for s in paper["sections"]] == \
        ["form", "spell", "ctx", "zh2en", "en2zh"]
    assert sum(len(s["items"]) for s in paper["sections"]) == 50
    assert paper["config"]["total"] == 50
    counts50 = {s["type"]: len(s["items"]) for s in paper["sections"]}
    assert counts50 == {"form": 6, "spell": 8, "ctx": 12, "zh2en": 12, "en2zh": 12}

    scaled = vp.generate_paper(3, save=False, total=40, fce_db_path=db)
    assert scaled["config"]["total"] == 40
    counts = {s["type"]: len(s["items"]) for s in scaled["sections"]}
    assert counts == {"form": 5, "spell": 6, "ctx": 10, "zh2en": 9, "en2zh": 10}


def test_paper_simple_words_layer_into_form(tmp_path, monkeypatch):
    """排序优先分层：简单词（level≤1）排前出词形变化题，复杂词排前出
    例句填空题（learned 混合词池跨级别）。"""
    import grammar_kb.vocab_paper as vp
    simple = _rich_rows([f"easy{i:03d}" for i in range(15)], level=0)
    hard = _rich_rows([f"hard{i:03d}" for i in range(45)], level=5)
    monkeypatch.setattr(vp, "_load_learned_words",
                        lambda user, fce_db_path=None: simple + hard)
    db = str(tmp_path / "fce.db")
    paper = vp.generate_paper(0, save=False, scope="learned", user="malin",
                              fce_db_path=db)
    assert paper["level"] == -1 and paper["paper_id"].startswith("MIX-")
    by_type = {s["type"]: s["items"] for s in paper["sections"]}
    form_words = [it["word"] for it in by_type["form"]]
    assert len(form_words) == 6
    assert set(form_words) <= {w["word"] for w in simple}
    ctx_words = [it["word"] for it in by_type["ctx"]]
    assert len(ctx_words) == 12
    assert set(ctx_words) <= {w["word"] for w in hard}


def _review_row(con, user, word):
    return con.execute(
        "SELECT stage, next_review, conquered_at, wrong_count"
        " FROM recite_review WHERE user = ? AND word = ?",
        (user, word)).fetchone()


def _progress_row(con, user, word):
    return con.execute(
        "SELECT wrong, mastered_at FROM recite_word_progress"
        " WHERE user = ? AND word = ?",
        (user, word)).fetchone()


def test_wrong_words_flow_into_review_queue(vex_env):
    """POST 错词进复习队列一次。PUT 重写明细，但只把相对旧明细新增的词回流：
    重叠词不再 wrong_count+1，也不重置 stage / 攻克 / 掌握。
    不传 wrong_words 不动明细和队列；移出名单的词留在队列里。"""
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    tomorrow = (today_cn() + timedelta(days=1)).isoformat()
    _archive_paper(vex_env, "L0-flow", 0)
    r = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 66, "exam_date": "2026-10-11",
        "paper_id": "L0-flow",
        "wrong_words": ["W1", "w2 ", "w1"],
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["wrong_count"] == 2
    eid = data["exam"]["id"]
    con = sqlite3.connect(vex_env / "fce.db")
    rows = con.execute(
        "SELECT word FROM vocab_exam_answers WHERE exam_id = ? ORDER BY id",
        (eid,)).fetchall()
    assert [x[0] for x in rows] == ["w1", "w2"]
    for w in ("w1", "w2"):
        stage, nxt, conquered, wrong_n = _review_row(con, "malin", w)
        assert (stage, nxt, conquered, wrong_n) == (0, tomorrow, None, 1)
    # 把 w2 推进到已攻克，证明随后的重叠 PUT 不会把它打回 stage 0
    con.execute(
        "UPDATE recite_review SET stage = 3, conquered_at = '2026-09-01'"
        " WHERE user = 'malin' AND word = 'w2'")
    con.execute(
        "UPDATE recite_word_progress SET mastered_at = '2026-09-01'"
        " WHERE user = 'malin' AND word = 'w2'")
    con.commit()
    con.close()
    # 单份成绩明细：answers 是行，exam.wrong_words 是从行投影的词表
    detail = c.get(f"/vocab-exams/{eid}", headers=ht)
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert [a["word"] for a in body["answers"]] == ["w1", "w2"]
    assert body["exam"]["wrong_words"] == ["w1", "w2"]
    # PUT 改成绩并传新错词：明细重写（w1 出局、w3 进来）。
    # w2 仍在名单里，wrong_count / stage / 攻克保持；只有 w3 +1。
    r2 = c.put(f"/vocab-exams/{eid}", headers=ht, json={
        "score": 77, "wrong_words": ["w2", "w3"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["wrong_count"] == 2
    con = sqlite3.connect(vex_env / "fce.db")
    rows = con.execute(
        "SELECT word FROM vocab_exam_answers WHERE exam_id = ? ORDER BY id",
        (eid,)).fetchall()
    assert [x[0] for x in rows] == ["w2", "w3"]
    assert _review_row(con, "malin", "w1")[3] == 1  # 移出名单，队列不抽走
    assert _review_row(con, "malin", "w2") == (3, tomorrow, "2026-09-01", 1)
    assert _progress_row(con, "malin", "w2") == (1, "2026-09-01")
    w3_stage, w3_next, w3_conquered, w3_n = _review_row(con, "malin", "w3")
    assert (w3_stage, w3_next, w3_conquered, w3_n) == (0, tomorrow, None, 1)
    assert _progress_row(con, "malin", "w3")[0] == 1
    con.close()
    again = c.get(f"/vocab-exams/{eid}", headers=ht).json()["data"]
    assert again["exam"]["wrong_words"] == ["w2", "w3"]
    # 只改分数、不传 wrong_words：明细和队列都不动
    r3 = c.put(f"/vocab-exams/{eid}", headers=ht, json={"score": 70})
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["exam"]["score"] == 70
    assert r3.json()["data"]["wrong_count"] == 2
    con = sqlite3.connect(vex_env / "fce.db")
    rows = con.execute(
        "SELECT word FROM vocab_exam_answers WHERE exam_id = ? ORDER BY id",
        (eid,)).fetchall()
    assert [x[0] for x in rows] == ["w2", "w3"]
    assert _review_row(con, "malin", "w2") == (3, tomorrow, "2026-09-01", 1)
    assert _review_row(con, "malin", "w3")[3] == 1
    con.close()
    # 再存一次同一批错词（大小写/空白不同）：仍然不加
    r4 = c.put(f"/vocab-exams/{eid}", headers=ht, json={
        "wrong_words": ["W2", " w3"]})
    assert r4.status_code == 200, r4.text
    con = sqlite3.connect(vex_env / "fce.db")
    assert _review_row(con, "malin", "w2") == (3, tomorrow, "2026-09-01", 1)
    assert _review_row(con, "malin", "w3")[3] == 1
    assert _progress_row(con, "malin", "w2") == (1, "2026-09-01")
    con.close()
    # 换学生：当前错词对该生是新登记，整表回流一次；原学生队列不动
    r5 = c.put(f"/vocab-exams/{eid}", headers=ht, json={
        "user": "mxy", "wrong_words": ["w2", "w3"]})
    assert r5.status_code == 200, r5.text
    assert r5.json()["data"]["exam"]["user"] == "mxy"
    con = sqlite3.connect(vex_env / "fce.db")
    assert _review_row(con, "malin", "w2") == (3, tomorrow, "2026-09-01", 1)
    assert _review_row(con, "mxy", "w2")[3] == 1
    assert _review_row(con, "mxy", "w3")[3] == 1
    # [] 清空明细，不从队列抽走
    con.close()
    r6 = c.put(f"/vocab-exams/{eid}", headers=ht, json={"wrong_words": []})
    assert r6.status_code == 200, r6.text
    assert r6.json()["data"]["wrong_count"] == 0
    con = sqlite3.connect(vex_env / "fce.db")
    n_ans = con.execute(
        "SELECT COUNT(*) FROM vocab_exam_answers WHERE exam_id = ?",
        (eid,)).fetchone()[0]
    assert n_ans == 0
    assert _review_row(con, "mxy", "w2")[3] == 1
    assert _review_row(con, "malin", "w2")[3] == 1
    con.close()
    cleared = c.get(f"/vocab-exams/{eid}", headers=ht).json()["data"]
    assert cleared["exam"]["wrong_words"] == []
    assert cleared["answers"] == []
    # wrong_words 非 list → 422
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 66,
        "exam_date": "2026-10-12", "wrong_words": "w1"}).status_code == 422


def test_unlock_manual_set_open_ends(vex_env):
    """解锁上限放开到 MAX_UNLOCK_LEVEL=7：可设 7，越界 422。"""
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    r = c.put("/vocab-exams/unlock", headers=ht,
              json={"user": "malin", "level": MAX_UNLOCK_LEVEL})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["level"] == MAX_UNLOCK_LEVEL == 7
    assert r.json()["data"]["unlocked"] == 7
    hs = _login(c, "malin", "123456")
    assert c.get("/vocab-exams", headers=hs).json()["data"]["unlocked_level"] == 7
    assert c.put("/vocab-exams/unlock", headers=ht,
                 json={"user": "malin", "level": 8}).status_code == 422


def _fake_rich(names):
    """测试用富词条：不读 grammar.db。"""
    return {
        w: {"word": w, "pos": '["n"]', "pos_list": ["n"],
            "pos_cn": "n. 名词", "gloss": "义", "gloss_clean": "义"}
        for w in names
    }


def _insert_hist(db, level, paper_id, words):
    from grammar_kb.vocab_exam import VocabExamStore
    from grammar_kb.vocab_paper import _HISTORY_TABLE_SQL
    store = VocabExamStore(db)
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO vocab_paper_history"
            " (level, paper_id, words, created_at) VALUES (?,?,?,?)",
            (level, paper_id, json.dumps(words), "2026-01-01T00:00:00"),
        )


def test_mix_history_buckets_and_save(tmp_path):
    """本级 + 混合卷分桶取回；大量其它级别不能把本级或 MIX 挤出窗口。"""
    from grammar_kb.vocab_paper import list_paper_history, save_paper
    db = str(tmp_path / "fce.db")
    words = [f"w{i:02d}" for i in range(50)]
    save_paper({"paper_id": "L0-20260101010101-1000", "words": words,
                "created_at": "2026-01-01T00:00:00"}, fce_db_path=db)
    save_paper({"paper_id": "MIX-20260101010101-1001", "words": words,
                "created_at": "2026-01-01T00:00:01"}, fce_db_path=db)
    for i in range(30):
        save_paper({
            "paper_id": f"L2-20260101010101-{2000 + i}",
            "words": words, "created_at": "2026-01-02T00:00:00",
        }, fce_db_path=db)
    only = list_paper_history(0, limit=5, fce_db_path=db)
    assert [p["paper_id"] for p in only] == ["L0-20260101010101-1000"]
    both = list_paper_history(0, limit=5, fce_db_path=db, include_mix=True)
    ids = {p["paper_id"] for p in both}
    assert "L0-20260101010101-1000" in ids
    assert "MIX-20260101010101-1001" in ids
    assert all(p["level"] in (0, -1) for p in both)
    with pytest.raises(ValueError):
        save_paper({"paper_id": "MIX-short", "words": ["a"]}, fce_db_path=db)
    with pytest.raises(ValueError):
        save_paper({"paper_id": "NOPE-1", "words": words}, fce_db_path=db)
    # 新口径：词数须等于 config.total（缺 config 按 50）
    saved = save_paper({"paper_id": "L0-20260101010101-1002",
                        "words": ["a", "b"], "config": {"total": 2},
                        "created_at": "2026-01-03T00:00:00"}, fce_db_path=db)
    assert saved == {"paper_id": "L0-20260101010101-1002", "level": 0,
                     "count": 2}
    with pytest.raises(ValueError, match="50 词"):
        save_paper({"paper_id": "L0-20260101010101-1003",
                    "words": ["a", "b", "c"], "config": {"total": 50}},
                   fce_db_path=db)


def test_learned_dedup_ignores_level_papers(tmp_path, monkeypatch):
    """learned 只跟先前 MIX（level<0）去重；本级卷用过的词不抽干混合卷。

    反过来，level 范围仍只跟本级历史去重，不看 MIX。
    """
    import grammar_kb.vocab_paper as vp
    words = [f"w{i:03d}" for i in range(80)]
    rich = list(_fake_rich(words).values())
    monkeypatch.setattr(vp, "_load_learned_words", lambda user, fce_db_path=None: rich)
    monkeypatch.setattr(vp, "_load_words_rich", lambda level: rich)
    db = str(tmp_path / "fce.db")
    _insert_hist(db, 0, "L0-20260101010101-3000", words)
    paper = vp.generate_paper(1, save=False, scope="learned", user="malin",
                              fce_db_path=db)
    assert paper["paper_id"].startswith("MIX-")
    assert paper["level"] == -1 and paper["scope"] == "learned"
    assert paper["user"] == "malin"
    assert paper["overlap_with_history"] == 0
    assert len(paper["words"]) == 50
    assert set(paper["words"]) <= set(words)
    # 先前 MIX 已覆盖全部词池 → 再出一卷应因重复超限失败
    _insert_hist(db, -1, "MIX-20260101010101-3001", words)
    with pytest.raises(ValueError, match="重复"):
        vp.generate_paper(1, save=False, scope="learned", user="malin",
                          fce_db_path=db)
    # level 范围不看 MIX：清掉本级历史后，只剩 MIX 覆盖也不挡本级
    from grammar_kb.vocab_exam import VocabExamStore
    with VocabExamStore(db)._connect() as conn:
        conn.execute("DELETE FROM vocab_paper_history WHERE level = 0")
    level_paper = vp.generate_paper(0, save=False, fce_db_path=db)
    assert level_paper["paper_id"].startswith("L0-")
    assert level_paper["overlap_with_history"] == 0
    # 本级历史仍按原口径去重
    _insert_hist(db, 0, "L0-20260101010101-3002", words)
    with pytest.raises(ValueError, match="重复"):
        vp.generate_paper(0, save=False, fce_db_path=db)


def test_paper_generate_learned_scope(vex_env, monkeypatch):
    """learned：进度里出现过的词（含未掌握）跨级出卷；登记须选 0-7 级。"""
    import grammar_kb.vocab_paper as vp
    monkeypatch.setattr(
        vp, "_load_words_by_names",
        lambda names: _fake_rich([n for n in names if n]))
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    db = str(vex_env / "fce.db")
    learned = [f"got{i:03d}" for i in range(80)]
    other = [f"other{i:03d}" for i in range(10)]
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS recite_word_progress ("
            " user TEXT NOT NULL, word TEXT NOT NULL, level INTEGER,"
            " right INTEGER NOT NULL DEFAULT 0, wrong INTEGER NOT NULL DEFAULT 0,"
            " mastered_at TEXT, last_seen TEXT, PRIMARY KEY (user, word))")
        # 已学 = 进度里出现过即可，这里全部未掌握（mastered_at 空）
        con.executemany(
            "INSERT OR REPLACE INTO recite_word_progress"
            " (user, word, level, right, wrong, mastered_at, last_seen)"
            " VALUES (?,?,?,?,?,?,datetime('now'))",
            [("malin", w, 0, 0, 1, None) for w in learned]
            + [("other", w, 1, 3, 0, "2026-01-01") for w in other])
    # 本级卷用过全部已学词，也不应挡住 MIX
    _insert_hist(db, 0, "L0-20260101010101-4000", learned)
    r = c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "save": False, "scope": "learned", "user": "malin"})
    assert r.status_code == 200, r.text
    p = r.json()["data"]["paper"]
    assert p["paper_id"].startswith("MIX-") and p["level"] == -1
    assert p["scope"] == "learned" and p["user"] == "malin"
    assert len(p["words"]) == 50 and p["overlap_with_history"] == 0
    assert set(p["words"]) <= set(learned)
    assert set(p["words"]).isdisjoint(other)
    assert "已学全部混合卷" in r.json()["data"]["html"]
    r2 = c.post("/vocab-papers/save", headers=ht, json=p)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["level"] == -1
    r3 = c.get(f"/vocab-papers/{p['paper_id']}/preview", headers=ht)
    assert r3.status_code == 200
    assert "已学全部混合卷" in r3.json()["data"]["html"]
    # 指定级别时默认不含 MIX；include_mix 把混合卷带上
    plain = c.get("/vocab-papers/history", headers=ht,
                  params={"level": 1, "limit": 20})
    assert plain.status_code == 200
    assert all(x["paper_id"] != p["paper_id"]
               for x in plain.json()["data"]["papers"])
    mixed = c.get("/vocab-papers/history", headers=ht,
                  params={"level": 0, "include_mix": True, "limit": 5})
    assert mixed.status_code == 200
    got = {x["paper_id"]: x["level"] for x in mixed.json()["data"]["papers"]}
    assert got.get(p["paper_id"]) == -1
    assert got.get("L0-20260101010101-4000") == 0
    # 混合卷不能按 level=-1 登记；选一个 0-7 级可以，并出现在批改列表
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": -1, "score": 88,
        "exam_date": "2026-10-11", "paper_id": p["paper_id"],
    }).status_code == 422
    ok = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 2, "score": 88,
        "exam_date": "2026-10-11", "paper_id": p["paper_id"],
    })
    assert ok.status_code == 200, ok.text
    grading_body = c.get("/vocab-papers/grading", headers=ht).json()["data"]
    assert grading_body["pass_score"] == PASS_SCORE
    grading = grading_body["papers"]
    row = next(x for x in grading if x["paper_id"] == p["paper_id"])
    assert row["level"] == -1
    assert any(e["user"] == "malin" and e["level"] == 2 and e["score"] == 88
               for e in row["exams"])
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "learned"}).status_code == 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "mixed"}).status_code == 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "learned", "user": "ghost"}).status_code == 422
    hs = _login(c, "malin", "123456")
    assert c.post("/vocab-papers/generate", headers=hs, json={
        "level": 1, "scope": "learned", "user": "malin"}).status_code == 403


def test_exam_record_crud(vex_env):
    """教师可登记、修改、删除考试记录；学生只能看自己的，不能改。"""
    c = _client(vex_env)
    hs = _login(c, "malin", "123456")
    hm = _login(c, "mxy", "mxy123")
    ht = _login(c, "teacher", "123456")
    _archive_paper(vex_env, "L0-paper", 0)
    created = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 90, "exam_date": "2026-10-11",
        "note": "第一次", "paper_id": "L0-paper",
    })
    assert created.status_code == 200, created.text
    exam = created.json()["data"]["exam"]
    eid = exam["id"]
    assert exam["note"] == "第一次" and exam["paper_id"] == "L0-paper"
    # 学生不能改、不能删
    assert c.put(f"/vocab-exams/{eid}", headers=hs, json={
        "score": 10}).status_code == 403
    assert c.delete(f"/vocab-exams/{eid}", headers=hs).status_code == 403
    # 只改分数：其余字段保留；未单独设定级别时，不及格会把推算级别降回去
    edited = c.put(f"/vocab-exams/{eid}", headers=ht, json={"score": 40})
    assert edited.status_code == 200, edited.text
    body = edited.json()["data"]
    assert body["exam"]["score"] == 40
    assert body["exam"]["note"] == "第一次"
    assert body["exam"]["paper_id"] == "L0-paper"
    assert body["exam"]["level"] == 0
    assert body["passed"] is False
    assert body["unlocked_level"] == 0
    # 改级别、日期、备注、归属学生
    moved = c.put(f"/vocab-exams/{eid}", headers=ht, json={
        "user": "mxy", "level": 2, "score": 88, "exam_date": "2026-10-20",
        "note": "改记到 mxy",
    })
    assert moved.status_code == 200, moved.text
    rec = moved.json()["data"]["exam"]
    assert rec["user"] == "mxy" and rec["level"] == 2 and rec["score"] == 88
    assert rec["exam_date"] == "2026-10-20" and rec["note"] == "改记到 mxy"
    assert rec["paper_id"] == "L0-paper"
    assert moved.json()["data"]["previous_user"] == "malin"
    # 教师看全部；学生即使带 include_all 也只能看自己的
    all_rows = c.get("/vocab-exams", headers=ht, params={"include_all": 1}).json()["data"]["exams"]
    assert any(r["id"] == eid and r["user"] == "mxy" for r in all_rows)
    malin_rows = c.get("/vocab-exams", headers=hs, params={"include_all": 1}).json()["data"]["exams"]
    assert malin_rows == []
    mxy_rows = c.get("/vocab-exams", headers=hm).json()["data"]["exams"]
    assert [r["id"] for r in mxy_rows] == [eid]
    # 校验与缺记录
    assert c.put(f"/vocab-exams/{eid}", headers=ht, json={"score": 120}).status_code == 422
    assert c.put(f"/vocab-exams/{eid}", headers=ht, json={"exam_date": "2026/10/20"}).status_code == 422
    assert c.put(f"/vocab-exams/{eid}", headers=ht, json={}).status_code == 422
    assert c.put("/vocab-exams/999", headers=ht, json={"score": 10}).status_code == 404
    assert c.delete(f"/vocab-exams/{eid}", headers=ht).status_code == 200
    assert c.delete(f"/vocab-exams/{eid}", headers=ht).status_code == 404
    assert c.get("/vocab-exams", headers=hm).json()["data"]["exams"] == []


def test_word_level_config_independent_of_score(vex_env):
    """单词级别是教师设定，不要求、也不被 80 分改写；没设定过才按考试推算。"""
    db = vex_env / "grammar.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE vocab_word (word TEXT, level INTEGER, pos TEXT, gloss TEXT,"
        " meanings TEXT, example TEXT, extra TEXT)"
    )
    con.executemany(
        "INSERT INTO vocab_word VALUES (?,?,?,?,?,?,?)",
        [
            ("apple", 0, "[]", "苹果", "[]", "{}", "{}"),
            ("orbit", 3, "[]", "轨道", "[]", "{}", "{}"),
        ],
    )
    con.commit()
    con.close()
    c = TestClient(create_app(str(db), str(vex_env / "exam.db"),
                              fce_db_path=str(vex_env / "fce.db")))
    hs = _login(c, "malin", "123456")
    hm = _login(c, "mxy", "mxy123")
    ht = _login(c, "teacher", "123456")
    # 没有任何考试，教师直接设到 L3
    r = c.put("/vocab-exams/unlock", headers=ht, json={"user": "malin", "level": 3})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["unlocked"] == 3
    assert r.json()["data"]["configured"] is True
    own = c.get("/vocab-exams", headers=hs).json()["data"]
    assert own["unlocked_level"] == 3 and own["level_configured"] is True
    assert own["passed_levels"] == []
    lv = c.get("/vocab-levels?max_level=5", headers=hs).json()["data"]
    assert lv["unlocked"] == 3
    assert {w["word"] for w in lv["items"]} == {"apple", "orbit"}
    # 学生不能改自己的级别
    assert c.put("/vocab-exams/unlock", headers=hs, json={
        "user": "malin", "level": 5}).status_code == 403
    assert c.put("/vocab-exams/unlock", headers=ht, json={
        "user": "malin", "level": True}).status_code == 422
    # 不及格、以及之后的高分，都不改写已经设定的级别
    _archive_paper(vex_env, "L0-lock", 0)
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 50,
        "exam_date": "2026-10-11", "paper_id": "L0-lock"}).status_code == 200
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 96,
        "exam_date": "2026-10-12", "paper_id": "L0-lock"}).json()["data"]["unlocked_level"] == 3
    # 教师可以把级别改到比考试推算更低（96 分本来会解锁 L1）
    low = c.put("/vocab-exams/unlock", headers=ht, json={"user": "malin", "level": 0})
    assert low.json()["data"]["unlocked"] == 0
    after = c.get("/vocab-levels?max_level=5", headers=hs).json()["data"]
    assert after["unlocked"] == 0
    assert [w["word"] for w in after["items"]] == ["apple"]
    passed = c.get("/vocab-exams", headers=hs).json()["data"]
    assert passed["unlocked_level"] == 0 and 0 in passed["passed_levels"]
    # 设定时间留在 vocab_unlock.updated_at（已有审计列，不另开历史表）
    with sqlite3.connect(vex_env / "fce.db") as stamp:
        row = stamp.execute(
            "SELECT level, updated_at FROM vocab_unlock WHERE user = ?",
            ("malin",),
        ).fetchone()
    assert row[0] == 0 and row[1]
    # 从未设定过的学生：考试 ≥80 仍解锁下一级
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "mxy", "level": 0, "score": 82,
        "exam_date": "2026-10-13", "paper_id": "L0-lock"}).json()["data"]["unlocked_level"] == 1
    mxy = c.get("/vocab-exams", headers=hm).json()["data"]
    assert mxy["unlocked_level"] == 1 and mxy["level_configured"] is False
    # 单词级别设定是教师专属：学生 PUT 直接 403
    assert c.put("/vocab-exams/unlock", headers=hs,
                 json={"user": "malin", "level": 1}).status_code == 403


def test_score_requires_archived_paper(vex_env):
    """不绑卷、或编号不在存档里，都不能登记。"""
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    missing = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 80, "exam_date": "2026-10-11"})
    assert missing.status_code == 422
    assert "存档" in missing.json()["message"]
    unknown = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 80, "exam_date": "2026-10-11",
        "paper_id": "L0-not-saved"})
    assert unknown.status_code == 422
    _archive_paper(vex_env, "L0-saved", 0)
    ok = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 80, "exam_date": "2026-10-11",
        "paper_id": "L0-saved"})
    assert ok.status_code == 200, ok.text


def test_grading_keeps_old_ungraded_and_mix_user(vex_env):
    """待打分不因最近 50 份漏掉早的未打分卷；混合卷带出绑定的学生。"""
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    con = sqlite3.connect(vex_env / "fce.db")
    for i in range(55):
        con.execute(
            "INSERT INTO vocab_paper_history"
            " (level, paper_id, words, created_at, user, scope)"
            " VALUES (0, ?, '[\"a\"]', '2026-01-01', '', 'level')",
            (f"L0-old-{i:03d}",))
    con.execute(
        "INSERT INTO vocab_paper_history"
        " (level, paper_id, words, created_at, user, scope)"
        " VALUES (-1, 'MIX-bound', '[\"b\"]', '2026-01-02', 'mxy', 'learned')")
    for i in range(5, 55):
        con.execute(
            "INSERT INTO vocab_exams (user, level, score, exam_date, paper_id)"
            " VALUES ('malin', 0, 90, '2026-10-01', ?)",
            (f"L0-old-{i:03d}",))
    con.commit()
    con.close()
    papers = c.get("/vocab-papers/grading", headers=ht).json()["data"]["papers"]
    ids = [p["paper_id"] for p in papers]
    assert "L0-old-000" in ids
    mix = next(p for p in papers if p["paper_id"] == "MIX-bound")
    assert mix["user"] == "mxy"
    malin = c.get("/vocab-papers/grading", headers=ht,
                  params={"user": "malin"}).json()["data"]["papers"]
    malin_ids = [p["paper_id"] for p in malin]
    assert "L0-old-000" in malin_ids
    assert "MIX-bound" not in malin_ids
    mxy = c.get("/vocab-papers/grading", headers=ht,
                params={"user": "mxy"}).json()["data"]["papers"]
    assert any(p["paper_id"] == "MIX-bound" and p["user"] == "mxy" for p in mxy)
