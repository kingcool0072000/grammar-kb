"""从爱问云（AiCloud / 伯索 Plaso OEM）拉取班级测验成绩，同步进 exam_records。

爱问云 PC 客户端是纯 Electron 壳，成绩数据走 HTTP API（本模块的接口与参数
均逆向自线上 SPA 并实测验证）：

- 登录 ``POST custom/usr/doLogin``（form 编码，``passwd`` = md5 小写 hex），
  返回 ``access_token``；后续请求带 ``access-token`` 请求头。
- 测验列表 ``POST question/exam/yxt/list``（JSON body）——注意三个坑：
  ``groupId`` 必须传**数组**、``pageStart`` 从 1 起、``pageSize`` 超过 20
  服务端直接返回空。
- 单场报告 ``POST question/exam/nc/examInfo {examResultId}``（JSON body），
  ``userAnswers[].result == 10`` 表示该题答对，其余为错题。

本机若走 fake-ip 代理，直连域名会被掐断 TLS，因此统一用 ``curl --resolve``
固定走阿里云 GA 入口 IP（两个 IP 交替重试）。

CLI::

    uv run grammar-kb sync-aicloud --phone 18610087156
    # 密码默认读环境变量 AICLOUD_PASSWORD
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
from typing import Any, Optional

from .exam_store import ExamStore

BASE_HOST = "www.aiwenyun.cn"
GA_IPS = ["47.239.15.32", "8.210.12.114"]  # 阿里云全球加速入口（fake-ip 环境直连用）
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 测验名 → 讲次，如 "哈一 26.动词时态4" → 26
_LECTURE_RE = re.compile(r"(\d+)\s*\.")


class AiCloudClient:
    """爱问云 API 客户端（curl 传输，自带双 IP 重试）。"""

    def __init__(self, phone: str, password: str, group_id: int):
        self.phone = phone
        self.password = password
        self.group_id = group_id
        self.token: Optional[str] = None

    # ---------- 传输 ----------

    def _curl(self, path: str, body: bytes, content_type: str, tries: int = 4) -> dict[str, Any]:
        last_err = "no attempt"
        for i in range(tries):
            ip = GA_IPS[i % len(GA_IPS)]
            headers = {
                "User-Agent": UA,
                "Content-Type": content_type,
                "Origin": f"https://{BASE_HOST}",
                "Referer": f"https://{BASE_HOST}/",
                "platform": "ai",
                "device": "pc",
                "version": "2.0.3",
            }
            if self.token:
                headers["access-token"] = self.token
            cmd = [
                "curl", "-sS", "--max-time", "40",
                "--resolve", f"{BASE_HOST}:443:{ip}",
                "-X", "POST", f"https://{BASE_HOST}/{path}",
                "--data-binary", body.decode(),
            ]
            for k, v in headers.items():
                cmd += ["-H", f"{k}: {v}"]
            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode == 0 and proc.stdout.strip().startswith(b"{"):
                return json.loads(proc.stdout)
            last_err = f"rc={proc.returncode}"
            time.sleep(1 + i)
        raise RuntimeError(f"请求 {path} 失败: {last_err}")

    def _post_form(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._curl(path, urllib.parse.urlencode(params).encode(),
                          "application/x-www-form-urlencoded")

    def _post_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._curl(path, json.dumps(params).encode(), "application/json")

    # ---------- 业务 ----------

    def login(self) -> None:
        """登录换 token。失败抛 RuntimeError（信封 code!=0）。"""
        r = self._post_form("custom/usr/doLogin", {
            "name": self.phone,
            "passwd": hashlib.md5(self.password.encode()).hexdigest(),
            "osInfo": "MacOS",
            "version": "5.70.131_2.0.3",
        })
        if r.get("code") != 0:
            raise RuntimeError(f"登录失败: code={r.get('code')} {r.get('message') or r.get('msg')}")
        obj = r.get("obj") or {}
        self.token = obj.get("access_token") or obj.get("token")
        if not self.token:
            raise RuntimeError("登录成功但未返回 access_token")

    def quiz_list(self) -> list[dict[str, Any]]:
        """班级测验列表（自动翻页）。每条含 score/status/examInfo[0].name。"""
        items: list[dict[str]] = []
        page = 1
        while True:
            r = self._post_json("question/exam/yxt/list", {
                "groupId": [self.group_id],
                "pageSize": 20,   # >20 服务端返回空，勿改
                "pageStart": page,
                "xx": "",
            })
            if r.get("code") != 0:
                raise RuntimeError(f"获取测验列表失败: code={r.get('code')}")
            obj = r.get("obj") or {}
            batch = obj.get("res") or []
            items.extend(batch)
            if not batch or len(items) >= obj.get("total", 0) or page >= 5:
                return items
            page += 1

    def quiz_wrong_numbers(self, exam_result_id: str) -> tuple[list[int], Optional[str]]:
        """单场报告 → (错题号列表 1..N, 提交日期 YYYY-MM-DD)。"""
        r = self._post_json("question/exam/nc/examInfo",
                            {"examResultId": exam_result_id})
        obj = r.get("obj") or {}
        questions = obj.get("questions") or []
        answers = {a["id"]: a for a in obj.get("userAnswers") or []}
        wrong = [idx for idx, q in enumerate(questions, start=1)
                 if (answers.get(q.get("id")) or {}).get("result") != 10]
        end_ms = ((obj.get("examResultInfo") or {}).get("endTime")) or 0
        date = (datetime.datetime.fromtimestamp(end_ms / 1000).strftime("%Y-%m-%d")
                if end_ms else None)
        return wrong, date


def _lecture_of(name: str) -> Optional[int]:
    m = _LECTURE_RE.search(name or "")
    return int(m.group(1)) if m else None


def sync(phone: str, password: str, group_id: int = 3350581,
         store: Optional[ExamStore] = None,
         dry_run: bool = False) -> list[dict[str, Any]]:
    """拉取全部已出分测验并 upsert 进成绩库。

    匹配规则：同 (lecture, date) 的记录更新得分与错题，否则新增。
    未出分（status != 60）的测验跳过。返回本次同步明细。
    """
    client = AiCloudClient(phone, password, group_id)
    client.login()
    quizzes = client.quiz_list()

    store = store or ExamStore()
    existing = {(r["lecture"], r["date"]): r for r in store.list()}

    report: list[dict[str, Any]] = []
    for it in sorted(quizzes, key=lambda x: x.get("createTime") or 0):
        info = (it.get("examInfo") or [{}])[0]
        name = info.get("name") or "?"
        if it.get("status") != 60:  # 60=已出分
            report.append({"quiz": name, "action": "skip", "reason": "未出分"})
            continue
        lecture = _lecture_of(name)
        if lecture is None:
            report.append({"quiz": name, "action": "skip", "reason": "讲次解析失败"})
            continue
        wrong, date = client.quiz_wrong_numbers(it["_id"])
        if not date:
            report.append({"quiz": name, "action": "skip", "reason": "无提交时间"})
            continue
        score = int(it.get("score") or 0)
        hit = existing.get((lecture, date))
        if hit and (hit["score"] != score or hit["wrong"] != wrong):
            if not dry_run:
                store.update(hit["id"], lecture, date, score, wrong)
            report.append({"quiz": name, "action": "update",
                           "lecture": lecture, "date": date,
                           "old": (hit["score"], hit["wrong"]), "new": (score, wrong)})
        elif hit:
            report.append({"quiz": name, "action": "same", "lecture": lecture})
        else:
            if not dry_run:
                store.add(lecture, date, score, wrong)
            report.append({"quiz": name, "action": "add",
                           "lecture": lecture, "date": date,
                           "score": score, "wrong": wrong})
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="grammar-kb sync-aicloud",
        description="从爱问云拉取测验成绩同步进成绩库")
    p.add_argument("--phone", default=os.environ.get("AICLOUD_PHONE", "18610087156"))
    p.add_argument("--password", default=os.environ.get("AICLOUD_PASSWORD"),
                   help="默认读环境变量 AICLOUD_PASSWORD")
    p.add_argument("--group", type=int,
                   default=int(os.environ.get("AICLOUD_GROUP", "3350581")),
                   help="爱问云班级 ID（默认 25暑哈一）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划不写库")
    args = p.parse_args(argv)

    if not args.password:
        p.error("需要 --password 或环境变量 AICLOUD_PASSWORD")

    for row in sync(args.phone, args.password, args.group, dry_run=args.dry_run):
        if row["action"] == "add":
            print(f"+ 讲{row['lecture']:>2} {row['date']} {row['score']}分 "
                  f"错{len(row['wrong'])}题  ({row['quiz']})")
        elif row["action"] == "update":
            old_s, old_w = row["old"]
            print(f"~ 讲{row['lecture']:>2} {row['date']} {old_s}→{row['new'][0]}分 "
                  f"错{len(old_w)}→{len(row['new'][1])}题  ({row['quiz']})")
        elif row["action"] == "same":
            print(f"= 讲{row['lecture']:>2} 无变化  ({row['quiz']})")
        else:
            print(f"- 跳过 {row['quiz']}（{row['reason']}）")
    return 0
