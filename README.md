# grammar-kb

英语语法教学讲义的**知识库 + 学习前端**单仓项目，分两层：

- **`grammar_kb/`（Python 后端）**：把 PDF 教材/讲义清洗并结构化为**可检索、可溯源的知识点数据库**——
  自动去水印、还原表格、切分知识点、抽取关键词与关系，存入本地 SQLite（FTS5 全文检索），
  提供 CLI、HTTP API 与 MCP 服务。
- **`web/`（Vite 前端）**：面向学生的学习界面——初中语法课 / 词汇表 / 背单词 / 初中英语知识体系 / FCE 真题练习 / 阅读训练，
  外加作业成绩记录（增删改查，持久化于 iCloud Drive，跨设备同步）。
  学生版与教师版按账号区分功能与权限。

适用于任何"版式相对统一、带页眉页脚水印、含表格"的教学/技术 PDF。

## 特性

- 🧹 **去水印**：按字体 + 文字方向剔除页眉/页脚/斜排背景水印（含 PDF 子集字体前缀）
- 📊 **表格还原**：自动检测有框线表格并还原为 GFM Markdown 表格
- 🧩 **知识点切分**：按标题层级（章/节/小节/子项/例句/练习）拆成可独立检索单元
- 🏷️ **分类与关系**：按主题分类，抽取关键词/标志词与知识点间关系（如"主将从现""时态呼应"）
- 🎯 **考点信号**：每个知识点标注考点维度（时态/语态/拼写/从句…），支持"按考点反查知识点"
- 📖 **单词表**：基于讲义语料生成单词表（释义/词性/词形变化/来源溯源）
- 🔍 **可溯源**：每个知识点带 `讲次 · 节路径 · 页码`，可定位回原文
- 🗄️ **不截断**：正文存 SQLite `TEXT`（无长度上限），FTS 仅用于命中
- 🌐 **HTTP API**：内置 REST 服务（FastAPI，自带 `/docs` 交互文档）
- 🔌 **MCP 就绪**：内置 MCP 服务，Claude 等客户端可直接查询

## 快速开始

```bash
uv sync                          # 安装依赖（含开发依赖）
uv run grammar-kb ingest ./pdfs  # 导入一个 PDF 目录（全量重建，id 可复现）
uv run grammar-kb stats          # 查看统计
```

> 未安装 [uv](https://docs.astral.sh/uv/)？`curl -LsSf https://astral.sh/uv/install.sh | sh`

## 常用命令

```bash
uv run grammar-kb ingest ./pdfs               # 导入目录（或单个 PDF 文件）
uv run grammar-kb ingest-homework ./作业卷目录 # 导入哈一作业卷 PDF（题干入 homework_question 表）
uv run grammar-kb lecture 25                   # 输出某讲的完整 Markdown（表格已还原）
uv run grammar-kb lecture 25 --format html     # 输出某讲的 HTML（表格渲染为 <table>）
uv run grammar-kb kp 173                       # 输出某知识点的完整 Markdown
uv run grammar-kb search "关键词"              # 全文检索知识点
uv run grammar-kb search "since" --category 时态
uv run grammar-kb markers --category 时态      # 列出某类下所有关键词/标志词
uv run grammar-kb markers --tense 现在完成时   # 列出某时态的标志词
uv run grammar-kb relation 主将从现            # 按关系类型查知识点
uv run grammar-kb exam-signal 从句             # 按考点信号反查知识点（反之亦然）
uv run grammar-kb exam-signal --list           # 列出所有考点信号维度
uv run grammar-kb words --limit 100            # 单词表（释义/词性/词形变化/来源）
uv run grammar-kb stats                        # 统计
uv run grammar-kb serve --port 8000            # 启动 HTTP 查询服务（见 http://127.0.0.1:8000/docs）
```

默认数据库为运行目录下的 `data/grammar.db`，可用 `--db` 或环境变量 `GRAMMAR_KB_DB` 覆盖。

### 直接用预构建数据集（可选）

不想自己 ingest，可从 [GitHub Releases](https://github.com/kingcool0072000/grammar-kb/releases) 下载对应版本的 `grammar.db`，放到 `data/grammar.db`（或用 `GRAMMAR_KB_DB` 指定路径）即可直接查询。数据集版本号见 release tag（如 `data-v1`），库内 `meta` 表也记录了版本与生成时间。

## 架构

```
PDF ──► pdf_parser   去水印（字体+方向过滤）+ 重排行 + 还原表格
      └─► structure    文本 → 大纲树 → 知识点切分（分类 + 关键词 + 关系）
                      └─► db          SQLite（lecture / knowledge_point / marker / relation / block + FTS5）
                                      └─► query  查询 API（CLI 与 MCP 共用）
```

| 模块 | 职责 |
|---|---|
| `pdf_parser.py` | fitz 抽 span（字体/位置/方向）→ 过滤水印 → 重排行；pdfplumber 在过滤后字符上还原表格 |
| `structure.py`  | 行分类（节/小节/子项/例句/练习）→ 知识点切分 |
| `classify.py`   | 分类规则、关键词词典、关系检测、**考点信号**（**纯函数**） |
| `vocabulary.py` | 基于语料的单词表（释义/词性/词形变化） |
| `markdown.py`   | 表格 → GFM、知识点与整讲渲染 |
| `db.py`         | schema + CRUD + FTS5(trigram, external-content)，无截断 |
| `query.py`      | 面向调用的查询 API |
| `ingest.py`     | PDF → 落库（目录导入 = 全量重建，id 可复现） |
| `exam_store.py` | 作业成绩独立 SQLite 库（增删改查；默认放 iCloud Drive） |
| `auth.py`       | 学生/教师双角色认证（HMAC token，30 天有效） |
| `fce_paper.py`  | FCE 青少版模拟卷 OCR（macOS Vision）→ 解析 → 入库 `data/fce.db`（4 Test × 87 题） |
| `fce_query.py`  | FCE 真题只读查询 + 练习提交/自动批改（fce_submission 表） |
| `reading_build.py` | FCE 阅读原文重建：OCR 坐标拆栏（双栏/四人网格）→ 填入正确答案 → `reading_article`（kind=base） |
| `reading.py`    | 阅读训练读写层：派生文 CRUD + 学生录音提交 + 教师 10 分制批改（reading_recordings 表） |
| `dict_db.py`    | ECDICT 全量词典导入与查询（36.5 万词条，`data/ecdict.db`） |
| `cli.py`        | 命令行 |
| `server.py`     | HTTP 服务（可选 extra） |
| `mcp_server.py` | MCP 服务（可选 extra） |
| `web/`          | 学习前端（Vite，详见下文「Web 学习前端」与 `web/README.md`） |

---

## 数据库 Schema（摘要）

```sql
lecture(number UNIQUE, title, full_title, category, subcategory, source_file, page_count)
knowledge_point(lecture_id, lecture_number, title, category, section_path,
                body_md, examples_md, table_md, is_table, source_page, source_bbox, tags_json, ord)
marker(kp_id, lecture_number, marker, marker_type, tense, note)        -- 关键词/标志词
relation(kp_id, type, to_kp_id, note)                                  -- 关系：主将从现/时态呼应…
lecture_block(lecture_id, page, seq, kind, text_md)                    -- 整讲还原用

-- 全文检索（external-content + trigram，中文子串命中）
CREATE VIRTUAL TABLE kp_fts USING fts5(title, body_md, examples_md, table_md,
    content='knowledge_point', content_rowid='id', tokenize='trigram');
```

## 定制你的数据集

工具默认针对"统一版式的教学讲义"调参，换数据集时通常只需改三处（都在 `grammar_kb/`）：

- **水印字体** —— `pdf_parser.py` 的 `WATERMARK_FONTS`：新增你的页眉/水印字体名。
  诊断新 PDF 字体的快捷脚本：
  ```bash
  uv run python -c "import fitz; d=fitz.open('某.pdf'); \
  import collections; c=collections.Counter(s['font'] for b in d[0].get_text('dict')['blocks'] if b.get('type',0)==0 for l in b['lines'] for s in l['spans'] if s['text'].strip()); print(c)"
  ```
- **分类规则** —— `classify.py` 的 `_TITLE_RULES`：按标题关键字映射主题分类。
- **关键词词典** —— `classify.py` 的 `TENSE_MARKERS`（或自定义同类词典）。
- **版式正则** —— `structure.py`：若你的标题层级用不同记号（如 `一、` / `（一）`），调整对应正则即可。

## 作为 HTTP 服务

```bash
uv sync --extra server                       # 安装 server 依赖（fastapi + uvicorn）
uv run grammar-kb serve --port 8000          # 经由 CLI
# 或独立入口：
uv run grammar-kb-server --host 0.0.0.0 --port 8000
```

启动后访问 `http://127.0.0.1:8000/docs` 查看交互式 API 文档。端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/stats` | 统计与数据集元信息 |
| GET | `/lectures` | 讲次列表 |
| GET | `/lectures/{number}?format=markdown\|html` | 某讲内容（表格还原） |
| GET | `/kp/{id}?format=markdown\|html` | 某知识点 |
| GET | `/search?q=...&category=...&limit=...` | 全文检索 |
| GET | `/markers?category=时态&tense=...` | 标志词 |
| GET | `/relation?type=主将从现` | 按关系查 |
| GET | `/exam-signals` | 所有考点信号维度 |
| GET | `/exam-signal?signal=时态` | 按考点反查知识点 |
| GET | `/vocabulary?limit=300&min_freq=2` | 单词表（释义/词性/词形变化） |
| GET | `/taxonomy` | 知识点主题体系树（大类→主题） |
| GET | `/dict/{word}` | 查任意单词（ECDICT 全量词典） |
| GET | `/homework` | 已导入作业卷的讲次列表；`?lectures=2,4` 批量取多讲题目 |
| GET | `/homework/{lecture}` | 某讲作业卷全部题目（题干+选项，题号与测验平台一致） |
| GET/POST | `/exams` | 作业成绩：列表 / 新增 |
| PUT/DELETE | `/exams/{id}` | 作业成绩：修改 / 删除 |
| POST | `/auth/login` | 登录（学生/教师角色，返回 HMAC token） |
| GET | `/fce-papers` | FCE 真题概览（4 套 Test 各 paper 题数） |
| GET | `/fce-papers/{test_id}` | 单套 FCE Test 全文（学生视角自动剥离答案） |
| POST | `/fce-submissions` | 提交一次 FCE 大题练习（客观题自动批改；作文转待批改；附用时） |
| GET | `/fce-submissions` | 练习历史（学生只看自己；教师可按 status 拉待批改作文） |
| PUT | `/fce-submissions/{id}` | 教师批改作文（分数 + 评语） |
| GET | `/reading/articles` | 阅读文章列表（默认只回派生文；教师 `?kind=base` 看原文段、`?kind=all` 看全部） |
| GET | `/reading/articles/{id}` | 文章正文（学生读 base 原文返回 403） |
| POST/PUT/DELETE | `/reading/articles[/{id}]` | 教师新增 / 编辑 / 删除派生文 |
| POST | `/reading/recordings` | 学生提交朗读录音（base64 webm ≤9MB/5 分钟，附选中的朗读文本） |
| GET | `/reading/recordings` | 录音列表（学生只看自己；教师可按 status=pending 拉待批改） |
| GET | `/reading/recordings/{id}` | 录音详情（含 base64 音频；学生只能听自己的） |
| PUT | `/reading/recordings/{id}` | 教师批改录音（10 分制 + 评语） |

### 作业成绩数据存在哪

成绩存在**独立**的 SQLite 库（与讲义库 `data/grammar.db` 分开），路径按顺序解析：

1. 环境变量 `GRAMMAR_KB_EXAM_DB`
2. **iCloud Drive**：`~/Library/Mobile Documents/com~apple~CloudDocs/grammar-kb/exam.db`（macOS 且 iCloud 可用时）——数据量小，放云端由 iCloud 在多台设备间同步
3. 兜底 `data/exam.db`

库刻意不用 WAL 模式（单文件自包含，iCloud 整文件同步更可靠）；其他设备装好本仓库、登录同一 iCloud 账号后启动服务，读到的就是同一份成绩。

### FCE 真题数据（`data/fce.db`）

FCE 青少版（For Schools）模拟卷，源 PDF 为纯扫描图，经 macOS Vision OCR + 结构化解析入库：

- **内容**：4 套 Test × 87 题（读写 52 + 写作 5 + 听力 30），含阅读原文、选项、关键词与全部答案
- **入库/重跑**：`uv run python -m grammar_kb.fce_paper --pdf 青少版1.PDF --db data/fce.db`
  （已 OCR 的页文本可缓存复用：`--ocr-dir <目录>`，格式为 `pNNN.txt` 三列坐标行）
- **练习记录**：`fce_submission` 表存每次大题提交（作答明细、自动批改结果、用时、作文批改）
- **OCR 依赖**：macOS 系统自带 Vision 框架（无需安装 tesseract），脚本内嵌于 `fce_paper.py`

### 阅读训练数据（同 `data/fce.db`）

- **原文（base）**：`reading_article(kind=base)` 50 段——RUE P1-P7 全部阅读文按 OCR 坐标拆栏重建
  （P5/P6 双栏、P7 四人网格），空格填入正确答案（加粗标记），`base_key` 形如 `T1P5` / `T1P7-A`。
  重建：`uv run python -m grammar_kb.reading_build`（OCR 缓存固化在 `data/ocr_cache/`，可复现）
- **派生文（derived）**：B2 难度补充阅读（每段 120-300 词、青少年主题），教师经 UI 或
  `scripts/ingest_derived.py data/derived_articles.json` 入库，按主题挂到对应原文段
- **录音**：`reading_recordings` 表存学生朗读提交（base64 webm + 选中段落文本），教师 10 分制批改
- **词典**：`/dict/{word}` 走 ECDICT 全量词典——首次使用需导入：
  `uv run python -c "from grammar_kb.dict_db import import_ecdict; import_ecdict(<ecdict.csv 路径>)"`
  （csv 来自 [ECDICT](https://github.com/skywind3000/ECDICT)，约 36.5 万词条；所有格自动归一：`children's` → `children`）

示例：
```bash
curl "http://127.0.0.1:8000/search?q=现在完成时&limit=3"
curl "http://127.0.0.1:8000/lectures/25?format=html"
```

**统一响应格式**：所有端点返回 `{code, message, data}`。
```jsonc
// 成功（HTTP 200）
{ "code": 0, "message": "ok", "data": { "knowledge_points": 359, ... } }
// 错误（HTTP 与 code 一致）
{ "code": 404, "message": "第 99 讲不存在", "data": null }
```

**CORS**：默认允许所有来源（`Access-Control-Allow-Origin: *`），前端可直接跨域调用。
收紧白名单：`GRAMMAR_KB_CORS_ORIGINS=https://a.com,https://b.com grammar-kb-server`。

## 作为 MCP 服务

```bash
uv sync --extra mcp
uv run grammar-kb-mcp
```

暴露的 tools：`search_knowledge_points`、`get_knowledge_point`、`get_lecture_markdown`、
`list_lectures`、`list_markers`、`find_by_relation`、`stats`。每个 tool 都是对 `Query` 的薄封装。

Claude Desktop 配置示例：
```json
{
  "mcpServers": {
    "grammar-kb": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/grammar-kb", "grammar-kb-mcp"],
      "env": { "GRAMMAR_KB_DB": "/path/to/grammar-kb/data/grammar.db" }
    }
  }
}
```

---

## Web 学习前端（`web/`）

面向学生的学习界面，依赖本地运行的后端服务（默认 `http://127.0.0.1:8000``，开发期由 Vite 把 `/api/*` 代理过去）。

```bash
# 终端 1：先起后端
uv sync --extra server && uv run grammar-kb-server

# 终端 2：再起前端
cd web && npm install && npm run dev     # http://localhost:5180
```

功能：

- **登录与角色**：学生版（背单词 + FCE 真题练习）/ 教师版（全部功能）；HMAC token 30 天有效
- **初中语法课**：48 讲按语法体系（词法/时态/语态/非谓语/句法/综合复习）分组，点开看整讲内容
- **词汇表**：600+ 高频词（释义/词性/词形变化/讲义出处），按词性筛选、搜索、排序
- **初中英语**：359 个零散知识点聚合为「语法大类 → 主题」两级树，含固定搭配速查表
- **背单词**：三题型（拼写/变形/认词）间隔练习，特殊拼写点后端判定，进度本地保存
- **FCE**：《FCE 冲刺宝典》静态知识库（19 天语法专题 + 直击考点练习）
- **FCE真题**：FCE 青少版模拟卷（4 Test × 87 题）分大题练习——阅读原文电子书排版（护眼底色、
  serif 阅读字体、字号可调）、做题计时、提交自动批改（错题显示正确答案）、作文提交教师批改；
  防翻译插件、学生练习页禁右键/选择
- **阅读内容（教师）**：FCE 阅读原文 50 段（RUE P1-P7 全部阅读文，OCR 坐标拆栏重建、
  正确答案回填加粗）；每段挂派生阅读（B2 难度、青少年主题，可持续新增/编辑/删除）；
  接收学生朗读录音、在线试听、10 分制打分 + 评语
- **阅读练习（学生）**：派生文章列表（显示字数与来源）→ 详情页阅读——点击段落选中录音范围
  （≤300 词）、浏览器麦克风录音（≤5 分钟）提交教师端；点击任意单词查 ECDICT 全量词典
  （36.5 万词条：音标/释义/词形变化），一次一词
- **🎯 考点信号（双向）**：知识点 ↔ 标志词/时态 双向跳转——「看到这个词，就是在考哪些知识点」
- **📝 作业成绩**：每讲一份作业卷（35 题，满分 100）。点题号记对错、分数自动算；
  多次作答全部保留、可修改可删除；错题本按「讲次+题号」汇总错误次数；
  数据经后端 `/exams` 存 iCloud（见上文），跨浏览器/设备不丢，旧 localStorage 记录首次打开自动迁移

技术栈：Vite + 原生 ES Modules · marked（Markdown 渲染），无框架依赖。更多细节见 [`web/README.md`](web/README.md)。

## 测试

```bash
uv run pytest                           # 全部（含真实 PDF 集成）
uv run pytest -m "not integration"      # 仅纯单测（无需 PDF，秒级）
```

覆盖：水印过滤 / 行重排 / 表格还原 / 知识点切分 / 分类 / 关键词抽取 / DB 不截断往返 /
FTS 中英文检索 / 级联清理 / id 重建可复现 / 查询 / 端到端集成 / 认证与角色权限 /
FCE OCR 解析 / FCE 提交批改 / 阅读文章权限与录音提交批改。

集成测试需要一个 PDF 目录，用环境变量 `GRAMMAR_TEST_PDF_DIR` 指定；未指定或不存在则自动跳过。

## 设计取舍与已知边界

- **无框线表格**：仅还原 pdfplumber 能靠框线检测到的 ruled table；少量无边框多栏对照以正文段落保留（信息不丢）。后续可加"按列空白对齐"的兜底检测。
- **知识点切分**：基于统一版式的启发式；特殊排版可能合并/拆分略有出入，可用 `search` + `kp` 复核。
- **目录导入即重建**：`ingest <目录>` 会清空并重建库（id 从 1 开始、可复现）；导入单个 PDF 只更新该讲。
