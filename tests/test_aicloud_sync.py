"""爱问云同步：登录口令的 MD5 不进 curl 参数。"""
import hashlib
import subprocess

from grammar_kb.aicloud_sync import AiCloudClient


def test_password_md5_goes_on_stdin_not_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["input"] = kwargs.get("input")

        class Proc:
            returncode = 0
            stdout = b'{"code":0,"obj":{"access_token":"tok"}}'

        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    AiCloudClient("13800000000", "secret-pass", 1).login()

    digest = hashlib.md5(b"secret-pass").hexdigest()
    argv = " ".join(captured["cmd"])
    assert "secret-pass" not in argv
    assert digest not in argv
    assert captured["cmd"][captured["cmd"].index("--data-binary") + 1] == "@-"
    assert digest.encode() in captured["input"]
