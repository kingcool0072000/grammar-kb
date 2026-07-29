# grammar-kb

把 **PDF 教材 / 讲义 / 手册** 清洗并结构化为**可检索、可溯源的知识点数据库**：
自动去水印、还原表格、切分知识点、抽取关键词与关系，存入本地 SQLite（FTS5 全文检索），
提供 CLI 与 MCP 服务。

适用于任何"版式相对统一、带页眉页脚水印、含表格"的教学/技术 PDF。

## 特性

- 🧹 **去水印**：按字体 + 文字方向剔除页眉/页脚/斜排背景水印（含 PDF 子集字体前缀）
- 📊 **表格还原**：自动检测有框线表格并还原为 GFM Markdown 表格
- 🧩 **知识点切分**：按标题层级（章/节/小节/子项/例句/练习）拆成可独立检索单元
- 🏷️ **分类与关系**：按主题分类，抽取关键词/标志词与知识点间关系（如"主将从现""时态呼应"）
- 🔍 **可溯源**：每个知识点带 `讲次 · 节路径 · 页码`，可定位回原文
- 🗄️ **不截断**：正文存 SQLite `TEXT`（无长度上限），FTS 仅用于命中
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
uv run grammar-kb lecture 25                   # 输出某讲的完整 Markdown（表格已还原）
uv run grammar-kb lecture 25 --format html     # 输出某讲的 HTML（表格渲染为 <table>）
uv run grammar-kb kp 173                       # 输出某知识点的完整 Markdown
uv run grammar-kb search "关键词"              # 全文检索知识点
uv run grammar-kb search "since" --category 时态
uv run grammar-kb markers --category 时态      # 列出某类下所有关键词/标志词
uv run grammar-kb markers --tense 现在完成时   # 列出某时态的标志词
uv run grammar-kb relation 主将从现            # 按关系类型查知识点
uv run grammar-kb stats                        # 统计
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
| `classify.py`   | 分类规则、关键词词典、关系检测（**纯函数**） |
| `markdown.py`   | 表格 → GFM、知识点与整讲渲染 |
| `db.py`         | schema + CRUD + FTS5(trigram, external-content)，无截断 |
| `query.py`      | 面向调用的查询 API |
| `ingest.py`     | PDF → 落库（目录导入 = 全量重建，id 可复现） |
| `cli.py`        | 命令行 |
| `mcp_server.py` | MCP 服务（可选 extra） |

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

## 测试

```bash
uv run pytest                           # 全部（含真实 PDF 集成）
uv run pytest -m "not integration"      # 仅纯单测（无需 PDF，秒级）
```

覆盖：水印过滤 / 行重排 / 表格还原 / 知识点切分 / 分类 / 关键词抽取 / DB 不截断往返 /
FTS 中英文检索 / 级联清理 / id 重建可复现 / 查询 / 端到端集成。

集成测试需要一个 PDF 目录，用环境变量 `GRAMMAR_TEST_PDF_DIR` 指定；未指定或不存在则自动跳过。

## 设计取舍与已知边界

- **无框线表格**：仅还原 pdfplumber 能靠框线检测到的 ruled table；少量无边框多栏对照以正文段落保留（信息不丢）。后续可加"按列空白对齐"的兜底检测。
- **知识点切分**：基于统一版式的启发式；特殊排版可能合并/拆分略有出入，可用 `search` + `kp` 复核。
- **目录导入即重建**：`ingest <目录>` 会清空并重建库（id 从 1 开始、可复现）；导入单个 PDF 只更新该讲。
