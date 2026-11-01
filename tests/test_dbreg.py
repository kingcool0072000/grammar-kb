"""dbreg（库路径+连接唯一注册处）与 Store 构造建表契约。

背景（Phase 1 数据访问统一，2026-11）：历史上 8 套路径解析口径并存
（grammar.db 兜 cwd、fce.db 兜仓库根、homework_admin 又是第三套），
同一张库可能被解析到不同文件；9 个 Store 的 ``with conn`` 只管事务
不关连接。这组测试锁住统一后的语义：

- 路径：环境变量优先（历史变量名不变）；默认仓库根 data/，与 cwd 无关；
  GRAMMAR_KB_DATA_DIR 只影响 readingmore（历史口径）。
- connect：成功提交、异常回滚、退出必关；readonly 拒写。
- Store 构造即建表（原为首连才建，test_plan 等 fixture 依赖此契约）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grammar_kb import dbreg
from grammar_kb.focus import FocusStore
from grammar_kb.paths import data_path
from grammar_kb.plan import PlanStore
from grammar_kb.reading import ReadingStore
from grammar_kb.recite import ReciteStore
from grammar_kb.topics import TopicStore
from grammar_kb.vocab_exam import VocabExamStore
from grammar_kb.video_study import VideoStore


# ---- 路径解析 ----

def test_env_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAMMAR_KB_DB", str(tmp_path / "g.db"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "f.db"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "e.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "l.db"))
    assert dbreg.grammar_db_path() == str(tmp_path / "g.db")
    assert dbreg.fce_db_path() == str(tmp_path / "f.db")
    assert dbreg.exam_db_path() == str(tmp_path / "e.db")
    assert dbreg.library_db_path() == str(tmp_path / "l.db")


def test_default_is_repo_data_not_cwd(monkeypatch):
    """默认一律仓库根 data/（历史 bug：cwd/data 与仓库根 data 并存漂移）。"""
    for var in ("GRAMMAR_KB_DB", "GRAMMAR_KB_FCE_DB", "GRAMMAR_KB_EXAM_DB",
                "GRAMMAR_KB_LIBRARY_DB"):
        monkeypatch.delenv(var, raising=False)
    assert Path(dbreg.grammar_db_path()) == data_path("grammar.db")
    assert Path(dbreg.fce_db_path()) == data_path("fce.db")
    assert Path(dbreg.exam_db_path()) == data_path("exam.db")
    assert Path(dbreg.library_db_path()) == data_path("library.db")
    assert Path(dbreg.ecdict_db_path()) == data_path("ecdict.db")


def test_data_dir_only_affects_readingmore(monkeypatch, tmp_path):
    """GRAMMAR_KB_DATA_DIR 保持 readingmore 专属语义（不外溢到其他库）。"""
    monkeypatch.setenv("GRAMMAR_KB_DATA_DIR", str(tmp_path))
    for var in ("GRAMMAR_KB_DB", "GRAMMAR_KB_FCE_DB"):
        monkeypatch.delenv(var, raising=False)
    assert dbreg.readingmore_db_path() == str(tmp_path / "readingmore.db")
    assert Path(dbreg.grammar_db_path()) == data_path("grammar.db")
    assert Path(dbreg.fce_db_path()) == data_path("fce.db")


# ---- connect：提交 / 回滚 / 必关 / 只读 ----

def test_connect_commits_and_closes(tmp_path):
    p = tmp_path / "t.db"
    with dbreg.connect(str(p)) as conn:
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (7)")
    # 连接已关（sqlite3 对 closed connection 抛 ProgrammingError）
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    # 已提交：新连接可见
    with sqlite3.connect(p) as c2:
        assert c2.execute("SELECT x FROM t").fetchone()[0] == 7


def test_connect_rolls_back_on_error(tmp_path):
    p = tmp_path / "t.db"
    with sqlite3.connect(p) as c0:
        c0.execute("CREATE TABLE t(x)")
    with pytest.raises(RuntimeError):
        with dbreg.connect(str(p)) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    # 回滚：行未落库；连接也已关闭（后续借 c2 另开验证数据）
    with sqlite3.connect(p) as c2:
        assert c2.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_connect_readonly_rejects_write(tmp_path):
    p = tmp_path / "t.db"
    with sqlite3.connect(p) as c0:
        c0.execute("CREATE TABLE t(x)")
    with pytest.raises(sqlite3.OperationalError):
        with dbreg.connect(str(p), readonly=True) as conn:
            conn.execute("INSERT INTO t VALUES (1)")


# ---- Store 构造即建表（Phase 1 契约：fresh 库不再依赖首连兜底）----

@pytest.mark.parametrize("store_cls,table", [
    (ReciteStore, "recite_sessions"),
    (FocusStore, "focus_sessions"),
    (TopicStore, "topic_progress"),
    (VocabExamStore, "vocab_exams"),
    (ReadingStore, "reading_article"),
    (VideoStore, "video_item"),
    (PlanStore, "plan_weeks"),
])
def test_store_init_creates_schema_on_fresh_db(tmp_path, store_cls, table):
    db = tmp_path / "fce.db"
    store = store_cls(str(db))
    assert Path(store.db_path).exists(), "Store 构造后库文件应存在"
    with sqlite3.connect(store.db_path) as c:
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert table in names, f"构造即应建出 {table}"
