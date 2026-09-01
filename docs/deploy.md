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

## 云服务器部署：数据同步怎么办

> 关键认知：**SQLite 没有"多节点同步"，正确模式是收敛为单一事实源。**

局域网模式是"每台设备各自跑服务 + iCloud 同步库文件"；一旦部署到云，
云上的库与 iCloud 的库会形成**两个事实源**互相分叉。解法不是同步，
而是**迁移后全员只访问云**，本机/iCloud 库退役为备份来源：

```
部署后：云服务器 ══ 唯一的库
        ├─ 教师电脑（只是个浏览器，不再跑本机 server）
        └─ 学生设备（只是个浏览器）
```

**迁移四步**（在现有 Mac 上执行）：

```bash
# 1) 停本机服务，做一致性快照（VACUUM INTO 避免拷到写一半的文件）
kill $(lsof -tiTCP:8000)   # 停本机 server
IC="$HOME/Library/Mobile Documents/com~apple~CloudDocs/grammar-kb"
for db in exam fce; do
  sqlite3 "$IC/$db.db" "VACUUM INTO '/tmp/migrate-$db.db'"
done
cp data/grammar.db /tmp/migrate-grammar.db   # 讲义库只读，直接拷

# 2) 传上云（连同账号与签名密钥，token 继续有效）
scp /tmp/migrate-*.db user@server:/opt/grammar-kb/data/
scp data/users.json data/auth_secret.key user@server:/opt/grammar-kb/data/

# 3) 云上环境变量写死路径（Linux 不命中 iCloud 分支，显式更稳）
#    systemd 里：
Environment=GRAMMAR_KB_DB=/opt/grammar-kb/data/grammar.db
Environment=GRAMMAR_KB_EXAM_DB=/opt/grammar-kb/data/exam.db
Environment=GRAMMAR_KB_FCE_DB=/opt/grammar-kb/data/fce.db

# 4) 全员改用云 URL；教师 Mac 上本机 server 不再启动
```

**云上的备份方向反转**（原 iCloud 同步退役）：

```bash
# 云上 cron：每天快照三个库（保留 30 天）
sqlite3 /opt/grammar-kb/data/exam.db "VACUUM INTO '/backup/exam-$(date +%F).db'"
# 或拉回本地 / 推对象存储（rclone 等）
# 需要时点恢复（误删回滚）可加 litestream —— 注意它是单向异步复制，
# 用于灾备，不是两台服务器之间的双向同步。
```

**为什么不做双向同步**：SQLite 无多主合并能力；两份都在写的库必然分叉。
真要多节点写入需要换 PostgreSQL 等网络数据库——对家庭应用属于过度设计。

**公网安全清单**：HTTPS（Caddy 自动证书，方案 B）· 师生强密码 ·
`GRAMMAR_KB_CORS_ORIGINS` 收紧到你的域名 · 云防火墙只放 80/443 ·
录音入库依赖 ffmpeg（云上 `apt install ffmpeg`）。

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
