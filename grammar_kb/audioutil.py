"""录音音频工具：base64 体积上限 + Safari 兼容转码（webm/opus → m4a/aac）。

精读朗读（reading）与视频复述（video_study）同一套约束；此前 video
跨模块 import reading 的私有符号，收拢为独立小模块。
"""
from __future__ import annotations

import base64
from pathlib import Path

MAX_AUDIO_B64 = 12 * 1024 * 1024  # 12MB base64（约 9MB 音频，5 分钟 webm 足够）


def transcode_for_safari(audio_b64: str, mime: str) -> tuple[str, str]:
    """webm/opus → m4a(aac)。

    学生端多为 Chrome（MediaRecorder 产 webm/opus），教师端若用 Safari/
    WebKit 打开，<audio> 不支持 webm/opus——表现为「听不见」。系统有
    ffmpeg 时统一转成 m4a（Safari/Chrome 双端原生可播）；无 ffmpeg 或
    转码失败则原样存（Chrome 教师端仍可播）。
    """
    if "webm" not in (mime or "").lower():
        return audio_b64, mime
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("ffmpeg"):
        return audio_b64, mime
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.webm"
            dst = Path(td) / "out.m4a"
            src.write_bytes(base64.b64decode(audio_b64))
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(src),
                 "-c:a", "aac", "-b:a", "64k", str(dst)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                return base64.b64encode(dst.read_bytes()).decode(), "audio/mp4"
    except Exception:  # noqa: BLE001
        pass
    return audio_b64, mime
