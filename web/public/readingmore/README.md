# 阅读更多（readingmore）· 杂志阅读器

线上地址：`https://<服务器>/readingmore/`（挂在和爸学同进程，登录和爸学账号后统计入云）。

## 本目录是什么

阅读器的**全部静态源**（单文件 `index.html` + 本地化 pdf.js `assets/` + ECDICT 分桶词典 `dict/`）。
vite 构建时会把本目录原样拷进 `web/dist/readingmore/`，由 server.py 的根 StaticFiles 直接服务。

**注意**：版权杂志 PDF 不入 git（根 .gitignore 已排除 `*.pdf`），部署时单独放置到
`web/dist/readingmore/`（本地与服务器都是这个位置）。

## 开发流程（在本仓库内改，独立仓 the-week-junior-reader 已于 26-10-05 退役）

1. 直接改 `web/public/readingmore/index.html`（或词典等资源）
2. 本地预览：
   ```bash
   python3 -m http.server 8931 --directory web/public/readingmore
   # 打开 http://127.0.0.1:8931（PDF 放同目录；云端上报在同源 /readingmore 下不可用属正常）
   ```
3. 部署：`tar` 静态目录 → scp 到服务器 `/opt/grammar-kb/web/dist/readingmore/`（静态文件即时生效，无需重启）；改了后端 Python 才需要 `systemctl restart hebaxue`

## 词典重建（换词库/扩词量时）

```bash
# 源库：ECDICT releases 的 ecdict-sqlite-28.zip 解压出 stardict.db
python3 scripts/build-dict.py /path/to/stardict.db web/public/readingmore/dict
# 扩词量：编辑脚本里 FRQ_CAP（默认 60000 → 如 150000）后重跑，再部署 dict/ 目录
```

## 后端与数据

- API：`grammar_kb/readingmore.py`（`POST /readingmore/api/session` 60s 心跳 upsert、`GET /readingmore/api/stats` 按登录用户聚合；学生白名单已加）
- 数据：独立 SQLite `data/readingmore.db`（rm_sessions / rm_lookups），已接入 `hebaxue-backup.sh` 每日 04:30 备份
- 鉴权：静态页免登录；API 走和爸学 Bearer token（前端读 localStorage `gkb-auth-v1`）；未登录时统计只存浏览器本地

## 功能备忘

双页杂志阅读（封面单页）、双击查词（ECDICT 离线 + 变形归原形 + 后缀推断）、查词后自动语音播报、生词本（TSV 导出）、缩放（按钮/触摸板 ctrl+wheel/移动端捏合，大倍率四角可达）、`/#history` 本机明细 + 云端聚合、60s 心跳双写（localStorage + 服务端，deviceId 区分设备）。
