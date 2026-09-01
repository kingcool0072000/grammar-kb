# 部署方案

单进程 Python 应用（FastAPI + SQLite）+ 纯静态前端（Vite 构建产物），
**不需要 Docker**。按场景选：

| 场景 | 方案 | 进程数 | 外部依赖 |
|---|---|---|---|
| 本机/局域网家庭使用 | A. 单进程一体 | 1 | 无 |
| 外网访问（HTTPS） | B. Caddy 反代 | 2 | Caddy |
| 必须容器时 | C. Podman | 1 | Podman |

## 方案 A（推荐）：单进程一体部署

前端 `web/dist` 由 FastAPI 直接服务（`GRAMMAR_KB_STATIC=1` 默认开启），
`/api/*` 前缀由内置中间件剥离——**一个进程、一个端口、零外部依赖**。

```bash
# 1) 构建
uv sync --extra server
cd web && npm install && npm run build && cd ..

# 2) 启动（唯一命令）
uv run grammar-kb-server --host 0.0.0.0 --port 8000

# 3) 访问
# http://<ip>:8000          → 完整前端
# http://<ip>:8000/docs     → API 文档
# GRAMMAR_KB_STATIC=0 …     → 纯 API 模式（不服务前端）
```

守护（macOS launchd / Linux systemd 任选）：

```ini
# /etc/systemd/system/hebaxue.service（Linux）
[Unit]
Description=Hebaxue grammar-kb
After=network.target

[Service]
WorkingDirectory=/opt/grammar-kb
ExecStart=/opt/grammar-kb/.venv/bin/uvicorn grammar_kb.server:app --host 0.0.0.0 --port 8000
Restart=on-failure
# 数据库路径：默认解析 环境变量 → iCloud → data/
# Environment=GRAMMAR_KB_DB=/data/grammar.db
# Environment=GRAMMAR_KB_EXAM_DB=/data/exam.db
# Environment=GRAMMAR_KB_FCE_DB=/data/fce.db

[Install]
WantedBy=multi-user.target
```

## 方案 B：外网访问（Caddy 反代 + 自动 HTTPS）

Caddy 是单二进制（~40MB），比 nginx 轻，自动签发证书。

```
# Caddyfile
learn.example.com {
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * /opt/grammar-kb/web/dist
        try_files {path} /index.html
        file_server
    }
}
```

> ⚠️ 家庭场景建议只在局域网用方案 A；上公网务必：强密码 + HTTPS（Caddy）
> + `GRAMMAR_KB_CORS_ORIGINS` 收紧到你的域名。

## 方案 C：坚持容器 → 用 Podman（无守护进程、rootless）

```bash
podman build -t hebaxue .
podman run -d --name hebaxue -p 8000:8000 \
  -v hebaxue-data:/app/data \
  hebaxue
```

`Containerfile`（multi-stage，最终镜像 ~200MB）：

```dockerfile
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install fastapi "uvicorn[standard]"
COPY grammar_kb/ grammar_kb/
COPY --from=web /web/dist/ web/dist/
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "grammar_kb.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

> 若部署机不需要 OCR/PDF 解析，可去掉 PyMuPDF/pdfplumber 依赖再瘦 ~60MB。

## 数据说明

- 三个 SQLite 库的路径解析顺序均为：环境变量 → iCloud（macOS）→ `data/`
  - `GRAMMAR_KB_DB`（grammar.db 讲义库，只读）
  - `GRAMMAR_KB_EXAM_DB`（exam.db 哈一成绩）
  - `GRAMMAR_KB_FCE_DB`（fce.db FCE/阅读/背单词/录音）
- 库为单文件 journal 模式，可直接整文件备份/搬迁（rsync/scp 即可）
- 录音经 ffmpeg 转 m4a 入库，部署机需装 ffmpeg（未装则原样存 webm，教师端 Chrome 可播）

## 健康检查

```bash
curl -fs http://127.0.0.1:8000/api/api-info   # 200 + JSON
```
