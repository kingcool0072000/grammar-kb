"""智谱 GLM 调用（OpenAI 兼容接口，httpx 同步客户端）。

直译自 FCEReadingLib server/src/services/glm.ts（原生 fetch）。
- ``generate_prep``：章节预习材料生成（JSON 输出，失败自动重试 1 次）
- ``explain_word``：AI 词典兜底
- ``list_models`` / ``test_api_key``：模型列表与 Key 连通性
- ``extract_json``：容错解析 <think> 块 / ```json 围栏 / 杂文前缀
异常统一为 :class:`GlmError`（RuntimeError 子类），由端点转 502。
"""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx

GLM_BASE = "https://open.bigmodel.cn/api/paas/v4"
TIMEOUT = 120.0

# 接口异常时的内置候选模型（settings /models 回退用）
FALLBACK_MODELS = [
    "glm-4-flash",
    "glm-4-air",
    "glm-4-airx",
    "glm-4-plus",
    "glm-4-long",
    "glm-4-flashx",
]

_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(timeout=TIMEOUT)
    return _client


class GlmError(RuntimeError):
    """GLM 调用失败。status 供上层区分 401（不重试）与一般 502。"""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _fetch(pathname: str, api_key: str, body: Optional[dict] = None, method: str = "POST") -> httpx.Response:
    try:
        return _get_client().request(
            method,
            GLM_BASE + pathname,
            json=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
    except Exception as e:  # httpx.TransportError / TimeoutException 等
        raise GlmError(f"连接智谱服务失败: {e}") from e


def list_models(api_key: str) -> list[str]:
    res = _fetch("/models", api_key, None, "GET")
    if res.status_code != 200:
        raise GlmError(
            f"获取模型列表失败 (HTTP {res.status_code})",
            401 if res.status_code == 401 else 502,
        )
    try:
        data = res.json()
    except ValueError as e:
        raise GlmError("获取模型列表失败（响应不是 JSON）") from e
    return [str(m.get("id") or "") for m in (data.get("data") or []) if m.get("id")]


def test_api_key(api_key: str) -> dict:
    """连通性测试：{ok, message}（不抛异常）。"""
    try:
        models = list_models(api_key)
        return {"ok": True, "message": f"连接成功，可用模型 {len(models)} 个"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def extract_json(text: str) -> dict:
    """从模型输出中容错提取第一个完整 JSON 对象。

    先剥 <think>…</think> 与 ```json 围栏，再从每个 ``{`` 起做括号配对扫描
    （处理字符串内的花括号与转义），取第一个可解析对象。
    """
    s = (text or "").strip()
    s = _THINK_RE.sub("", s)
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()

    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(s)):
            c = s[i]
            if escaped:
                escaped = False
                continue
            if c == "\\":
                if in_str:
                    escaped = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                        break  # 这个起点不是对象，换下一个 '{'
                    except ValueError:
                        break  # 这个起点不完整，换下一个 '{'
        start = s.find("{", start + 1)
    raise GlmError("AI 返回内容无法解析为 JSON，请重试或更换模型")


# 思考型模型（glm-4.5+/glm-5+）：降为低强度思考，避免长思考占用输出 token 导致 JSON 截断
_THINKING_MODEL_RE = re.compile(r"glm-(4\.[5-9]|4x|5)", re.I)


def _thinking_adjusted(model: str) -> dict:
    if _THINKING_MODEL_RE.search(model):
        return {"thinking": {"type": "enabled", "level": "low"}}
    return {}


DEFAULT_PREP_PROMPT = """你是一位面向中国中小学生的英语阅读辅导老师。你的任务是为学生的章节阅读准备课前预习材料。
你会收到：章节标题、从原文粗筛出的候选困难单词列表、生词数上限 maxWords、章节原文（可能被截断）。
请输出严格 JSON（不要任何多余文字）：
{
  "words": [
    { "word": "原形", "phonetic": "英式音标", "pos": "词性(缩写,如 n./v./adj.)", "meaning": "简明中文释义(结合本文语境)", "example": "出自原文的例句(截取一句,含该词或其变形)", "exampleZh": "例句中文翻译" }
  ],
  "idioms": [
    { "phrase": "俚语或习语短语", "literal": "字面意思", "actual": "实际含义", "usage": "一句话使用场景" }
  ]
}
要求：
1. words 只从候选列表中挑选真正值得学的词；剔除人名、地名、品牌等专有名词，剔除过于生僻不值得学的词；按重要性排序，最多 maxWords 个（maxWords 在用户消息中给出）。
2. idioms 从原文中识别真正的俚语、习语、固定搭配（如 give up、cool as a cucumber），数量 0-8 个，没有就给空数组。不要把普通短语算作习语。
3. 音标使用英式 IPA。
4. 所有中文解释要适合中小学生理解。"""


def generate_prep(
    api_key: str,
    model: str,
    chapter_title: str,
    text: str,
    candidates: list[str],
    max_words: int,
    system_prompt: Optional[str] = None,
) -> dict:
    """生成章节预习材料：``{words: [...], idioms: [...]}``。

    解析失败自动重试一次（思考型模型偶发围栏/杂文输出）；401 不重试。
    """
    truncated = (text or "")[:12000]
    user_prompt = (
        f"章节标题：{chapter_title}\n\n"
        f"候选困难单词：{', '.join(candidates)}\n"
        f"maxWords = {max_words}\n\n"
        f"章节原文：\n{truncated}"
    )

    last_error: Optional[Exception] = None
    for _attempt in range(2):
        try:
            body: dict = {
                "model": model,
                "messages": [
                    {"role": "system", "content": (system_prompt or "").strip() or DEFAULT_PREP_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                # 思考型模型的 max_tokens 是"思考+正文"总预算，需留足思考空间
                "max_tokens": 12288 if max_words >= 15 else 8192,
                "response_format": {"type": "json_object"},
            }
            body.update(_thinking_adjusted(model))
            res = _fetch("/chat/completions", api_key, body)

            if res.status_code != 200:
                detail = res.text or ""
                if res.status_code == 401:
                    raise GlmError("API Key 无效或未授权", 401)
                raise GlmError(f"智谱 API 调用失败 (HTTP {res.status_code}): {detail[:300]}")

            try:
                data = res.json()
            except ValueError as e:
                raise GlmError("智谱 API 返回内容不是 JSON") from e
            choices = data.get("choices") or []
            first = choices[0] if choices else {}
            if first.get("finish_reason") == "length":
                raise GlmError("AI 输出被截断，请调低每章生词上限或更换模型")
            content = ((first.get("message") or {}).get("content")) or ""
            return validate_prep(extract_json(content), max_words)
        except GlmError as e:
            last_error = e
            # 401/配额类错误不重试
            if e.status == 401:
                raise
        except Exception as e:  # noqa: BLE001
            last_error = GlmError(str(e) or "AI 生成失败")
    raise last_error or GlmError("AI 生成失败")


def validate_prep(parsed: dict, max_words: int) -> dict:
    """校验并规范 AI 返回的预习 payload；words 与 idioms 全空抛错。"""
    words_raw = parsed.get("words") if isinstance(parsed.get("words"), list) else []
    words = [
        {
            "word": str(w["word"]),
            "phonetic": str(w.get("phonetic") or ""),
            "pos": str(w.get("pos") or ""),
            "meaning": str(w.get("meaning") or ""),
            "example": str(w.get("example") or ""),
            "exampleZh": str(w.get("exampleZh") or ""),
        }
        for w in words_raw
        if isinstance(w, dict) and isinstance(w.get("word"), str)
    ][:max_words]
    idioms_raw = parsed.get("idioms") if isinstance(parsed.get("idioms"), list) else []
    idioms = [
        {
            "phrase": str(p["phrase"]),
            "literal": str(p.get("literal") or ""),
            "actual": str(p.get("actual") or ""),
            "usage": str(p.get("usage") or ""),
        }
        for p in idioms_raw
        if isinstance(p, dict) and isinstance(p.get("phrase"), str)
    ]

    if not words and not idioms:
        raise GlmError("AI 未返回有效内容，请重试")
    return {"words": words, "idioms": idioms}


def explain_word(api_key: str, model: str, word: str, context: Optional[str] = None) -> dict:
    """AI 查词：``{word, phonetic, pos, meaning, example}``（严格 JSON）。"""
    body: dict = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    '你是英语词典，面向中国中小学生。用户给一个英文单词，输出严格 JSON：'
                    '{"word":"","phonetic":"英式音标","pos":"词性","meaning":"简明中文释义",'
                    '"example":"一个简短例句"}。不要任何多余文字。'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"单词：{word}\n原句语境：{context}" if context else f"单词：{word}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    body.update(_thinking_adjusted(model))
    res = _fetch("/chat/completions", api_key, body)
    if res.status_code != 200:
        raise GlmError(f"AI 查词失败 (HTTP {res.status_code})")
    try:
        data = res.json()
    except ValueError as e:
        raise GlmError("AI 查词返回内容不是 JSON") from e
    choices = data.get("choices") or []
    first = choices[0] if choices else {}
    content = ((first.get("message") or {}).get("content")) or ""
    parsed = extract_json(content)
    return {
        "word": str(parsed.get("word") or word),
        "phonetic": str(parsed.get("phonetic") or ""),
        "pos": str(parsed.get("pos") or ""),
        "meaning": str(parsed.get("meaning") or ""),
        "example": str(parsed.get("example") or ""),
    }
