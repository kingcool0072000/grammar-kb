# 哈一初中语法讲义知识点库（grammar-kb）

把 `/Users/maxiangyu/Desktop/哈1语法课/讲义` 下的 PDF 讲义拆解为**结构化、可检索、可溯源**的知识点，
存入本地 SQLite 数据库，并保留**表格还原**能力。后续封装为 MCP 服务供 Claude 调用。

- **知识点维度**：词法 / 句法 / 时态 / 语态 / 非谓语 / 综合复习
- **可溯源**：每个知识点都带 `第N讲 · 节路径 · 页码`
- **表格还原**：原 PDF 里是表格的，渲染回 GFM markdown 表格
- **标志词/关系**：抽取时态标志词（since/already/now…）与关系（主将从现、时态呼应…）
- **不截断**：SQLite `TEXT` 无长度上限；FTS5 仅用于命中，正文从主表完整读取

---

## 快速开始

```bash
# 1. 安装依赖（pdfplumber / PyMuPDF 已在系统中可用）
pip install -r requirements.txt
pip install pytest            # 跑测试用

# 2. 导入全部讲义（约 43s，48 讲）
python -m grammar_kb.cli ingest "/Users/maxiangyu/Desktop/哈1语法课/讲义"

# 3. 用起来
python -m grammar_kb.cli stats                       # 统计
python -m grammar_kb.cli markers --category 时态      # 所有时态关键词（按时态分组、溯源到讲次）
python -m grammar_kb.cli lecture 25                   # 第25讲讲义 md（表格已还原）
python -m grammar_kb.cli kp 487                       # 某知识点的完整 md
python -m grammar_kb.cli search "主将从现"            # 全文检索知识点
python -m grammar_kb.cli search "since" --category 时态
python -m grammar_kb.cli relation 主将从现            # 按关系类型查
```

默认数据库路径：`data/grammar.db`（可用 `--db` 或环境变量 `GRAMMAR_KB_DB` 覆盖）。

---

## 用户示例查询如何对应

| 需求 | 命令 / API |
|---|---|
| 找到所有时态关键词 | `cli markers --category 时态` / `Query.markers_by_category("时态")` |
| 给我第 25 课讲义（md，含表格） | `cli lecture 25` / `Query.lecture_markdown(25)` |
| 查某知识点并溯源到讲次 | `cli kp <id>` 或 `cli search <词>`（结果带 `第N讲`） |
| 表格还原 | 讲义内的 ruled table 自动转 GFM 表格（见 `markdown.table_to_markdown`） |
| 不截断存储 | 全文为 `TEXT`；`test_no_truncation_long_body` 验证 200KB 往返 |

---

## 架构

```
PDF ──► pdf_parser   去水印（字体+方向过滤）+ 重排行 + pdfplumber 还原表格
      └─► structure    清洗文本 → 大纲树 → 知识点切分（含标志词/关系抽取）
                      └─► db          SQLite（lecture / knowledge_point / marker / relation / block + FTS5）
                                      └─► query  查询 API（CLI 与 MCP 共用）
```

### 关键模块

| 模块 | 职责 |
|---|---|
| `pdf_parser.py` | fitz 抽 span（字体/位置/方向）→ 过滤水印 → 重排行；pdfplumber 在过滤后字符上还原表格 |
| `structure.py`  | 行分类（节/小节/子项/例句/练习）→ 知识点切分 + 分类 + 标志词 + 关系 |
| `classify.py`   | 讲次分类、时态标志词词典、关系检测（**纯函数**） |
| `markdown.py`   | 表格→GFM、知识点渲染、整讲还原 |
| `db.py`         | schema + CRUD + FTS5(trigram, external-content)，无截断 |
| `query.py`      | 面向调用的查询 API |
| `ingest.py`     | PDF → 落库（幂等：先清后写） |
| `cli.py`        | 命令行 |
| `mcp_server.py` | MCP 服务（可选依赖 `mcp`） |

---

## 数据库 Schema（摘要）

```sql
lecture(number UNIQUE, title, full_title, category, subcategory, source_file, page_count)
knowledge_point(lecture_id, lecture_number, title, category, section_path,
                body_md, examples_md, table_md, is_table, source_page, source_bbox, tags_json, ord)
marker(kp_id, lecture_number, marker, marker_type, tense, note)        -- 标志词/关键词
relation(kp_id, type, to_kp_id, note)                                  -- 主将从现/时态呼应…
lecture_block(lecture_id, page, seq, kind, text_md)                    -- 整讲还原用

-- 全文检索（external-content + trigram，中文子串命中）
CREATE VIRTUAL TABLE kp_fts USING fts5(title, body_md, examples_md, table_md,
    content='knowledge_point', content_rowid='id', tokenize='trigram');
```

---

## 作为 MCP 服务

```bash
pip install mcp
python -m grammar_kb.mcp_server          # 或 grammar-kb-mcp
```

暴露的 tools：`search_knowledge_points`、`get_knowledge_point`、`get_lecture_markdown`、
`list_lectures`、`list_markers`、`find_by_relation`、`stats`。每个 tool 都是对 `Query` 的一行
封装，增删能力与查询层解耦。

Claude Desktop 配置示例（`claude_desktop_config.json`）：
```json
{
  "mcpServers": {
    "grammar-kb": {
      "command": "python",
      "args": ["-m", "grammar_kb.mcp_server"],
      "env": { "GRAMMAR_KB_DB": "/Users/maxiangyu/Code/grammar-kb/data/grammar.db" }
    }
  }
}
```

---

## 测试

```bash
python -m pytest                              # 全部（含真实 PDF 集成）
python -m pytest -m "not integration"         # 仅纯单测（无需 PDF，秒级）
```

覆盖：
- `test_classify.py` 讲次分类 / 文件名解析 / 标志词抽取（词边界）/ 关系检测
- `test_pdf_parser.py` 水印过滤 / 行重排 / 表格还原（纯函数 + 真实 PDF）
- `test_markdown.py` GFM 表格 / 转义 / 知识点与整讲渲染
- `test_structure.py` 行分类 / 知识点切分 / 标志词与关系落点
- `test_db.py` CRUD / **200KB 不截断往返** / FTS 中英文检索 / 级联清理
- `test_query.py` 检索 / 标志词 / 整讲还原
- `test_integration.py` 端到端：导入第 22/25 讲 → 表格还原 / 时态关键词 / 溯源

集成测试默认读取 `/Users/maxiangyu/Desktop/哈1语法课/讲义`，可用 `GRAMMAR_TEST_PDF_DIR` 覆盖；目录不存在则自动跳过。

---

## 设计取舍与已知边界

- **无框线表格**：仅还原 pdfplumber 能靠框线检测到的 ruled table；少量无边框的双栏对照
  会以正文段落形式保留（信息不丢，但不成 GFM 表）。后续可加"按列空白对齐"的兜底检测。
- **知识点切分**：基于讲义统一版式（罗马节/数字小节/子项/课堂练习）的启发式；个别特殊
  排版可能合并或拆分略有出入，可通过 `cli search` + `cli kp` 复核。
- **标志词词典**：内建初中语法 8 时态的时间状语/标志词；可按需在 `classify.TENSE_MARKERS` 增补。
