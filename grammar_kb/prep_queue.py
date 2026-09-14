"""批量预习任务队列：进程内线程池（并发 2，防限流）+ prep_jobs 行驱动。

直译自 FCEReadingLib server/src/jobs/prepQueue.ts。
- ``PrepQueue.create_job()``：建 job 行（status=queued）并投递后台线程
- ``generate_prep_for_chapter()``：单章流程（缓存命中直接跳过 → 取设置 →
  粗筛候选 → 无候选也写空 payload → generate_prep → INSERT OR REPLACE），
  供批量队列与 /library/prep/single 同步生成复用
FastAPI 是 async，这里用 ThreadPoolExecutor 后台跑（不进 asyncio），
端点立刻返回 jobId；每章线程内同步写库（LibraryStore 每调用独立连接，线程安全）。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from . import glm
from .wordfreq import extract_hard_words

if TYPE_CHECKING:  # 仅类型标注用，避免运行时循环依赖
    from .library import LibraryStore

CONCURRENCY = 2


def _num(v, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n


def generate_prep_for_chapter(store: "LibraryStore", book_id: int, chapter_index: int) -> None:
    """单章生成（也供单章重试/手动生成复用）。失败抛 RuntimeError（端点转 502）。"""
    existing = store.get_prep(book_id, chapter_index)
    if existing is not None:
        return  # 缓存命中，不重复请求

    settings = store.get_settings()
    api_key = settings["ai"].get("apiKey")
    if not api_key:
        raise RuntimeError("尚未配置智谱 API Key")
    model = settings["ai"].get("model") or "glm-4-flash"
    difficulty = settings["ai"].get("difficulty") or "L2"
    max_words = min(25, max(3, _num(settings["ai"].get("maxWords"), 12)))

    chapter = store.get_chapter(book_id, chapter_index)
    if not chapter:
        raise RuntimeError(f"章节不存在: {chapter_index}")

    candidates = extract_hard_words(chapter["text_content"], difficulty, 25)
    if not candidates:
        # 没有候选词也生成空 payload（可能是纯封面/目录页），避免反复重试
        store.put_prep(book_id, chapter_index, {"words": [], "idioms": []}, model, difficulty)
        return

    payload = glm.generate_prep(
        api_key,
        model,
        chapter_title=chapter["title"],
        text=chapter["text_content"],
        candidates=[c["word"] for c in candidates],
        max_words=max_words,
        system_prompt=settings["ai"].get("prompt") or None,
    )
    store.put_prep(book_id, chapter_index, payload, model, difficulty)


class PrepQueue:
    """prep_jobs 行驱动的后台生成器（并发 2）。"""

    def __init__(self, store: "LibraryStore"):
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=CONCURRENCY,
                                            thread_name_prefix="prep-job")

    def create_job(self, book_id: int, user: str, chapter_indexes: list[int]) -> int:
        job_id = self.store.insert_job(book_id, user, chapter_indexes)
        self._executor.submit(self._run_job, job_id)
        return job_id

    def _run_job(self, job_id: int) -> None:
        """逐章生成：查缓存 → 粗筛 → GLM → 落库；失败章记入 failed_indexes。"""
        marked_running = False
        while True:
            job = self.store.get_job_row(job_id)
            if not job or job["status"] not in ("queued", "running"):
                return
            try:
                pending = json.loads(job["pending"])
                failed = json.loads(job["failed_indexes"])
            except ValueError:
                return
            if not pending:
                self.store.finish_job(job_id, failed)
                return
            if not marked_running:
                self.store.set_job_running(job_id)
                marked_running = True

            chapter_idx = pending[0]
            rest = pending[1:]
            try:
                generate_prep_for_chapter(self.store, job["book_id"], chapter_idx)
                self.store.advance_job(job_id, rest)
            except Exception:  # noqa: BLE001 - 单章失败不拖垮整个任务
                failed.append(chapter_idx)
                self.store.advance_job(job_id, rest, failed)
            if not rest:
                self.store.finish_job(job_id, failed)
                return
