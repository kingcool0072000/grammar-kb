# 词光 Word Atlas

和爸学里已经在学的单词，如果词光也有，就显示插画，并播放英音 / 美音 /
例句录音。没有文件时仍用浏览器语音。

词条写在 `grammar.db` 的 `vocab_atlas` 表，不改 `vocab_word` 的级别，
也不打开 `fce.db`（背词进度在那里）。媒体不进 git。

署名：Word Atlas contributors（词光），CC BY 4.0。详见仓库根目录 `NOTICE`。

## 为什么不并进「雅思」词表

现有级别是「第一次出现归到哪一层」：初中 → 高中 → 四级 → 六级 → 考研 →
雅思 → 专四，层与层不重复。学生背单词界面只开放 L0–L5，雅思是 L6。

词光 7298 条里，大约 6000 条已经出现在这些词表中（初中词表大约九成对得上）。
只在词光里、现有词表没有的大约 1200 条，以 C1/C2 为主。把它们塞进雅思会
加大 L6 的词数和以后的考卷，而孩子现在的背词队列根本看不到 L6。

所以词光是叠加层：对得上的词在背单词、词表、查词里出插画和录音；
对不上的词仍可用 `/atlas/{word}` 查到，但不进入背词队列。

## 服务器（导入，不要在 Mac 上把媒体 rsync 上去）

在 `/opt/grammar-kb`，代码已经切到包含本改动的版本并装好依赖之后：

```bash
cd /opt/grammar-kb

# 1. 词条写入正在使用的 grammar.db。可重复执行。不碰 fce.db。
GRAMMAR_KB_DB=/opt/grammar-kb/data/grammar.db \
  .venv/bin/python scripts/import_word_atlas.py

# 2. 插画 + 缩略图 + 约 2.3 万条 mp3，大约 700MB。
#    默认落在 data/atlas-cache/word-atlas/public/atlas/
#    需要 git。不会拉取 public/sfx。
GRAMMAR_KB_DB=/opt/grammar-kb/data/grammar.db \
  .venv/bin/python scripts/import_word_atlas.py --media

sudo systemctl restart hebaxue
```

进程读 `GRAMMAR_KB_ATLAS_DIR`。不设置时，克隆目录里一旦出现 `img/`，
就会从那里发文件，所以媒体到位后不必为了新文件再重启一次。
上面的 restart 是为了让新代码先生效；词条导入可以在重启之后再跑，
服务开着时导入也行（只是往 `vocab_atlas` 里 upsert）。

自定义目录时：

```bash
# systemd: Environment=GRAMMAR_KB_ATLAS_DIR=/var/lib/hebaxue/atlas
GRAMMAR_KB_ATLAS_DIR=/var/lib/hebaxue/atlas \
  .venv/bin/python scripts/import_word_atlas.py --media --copy
```

磁盘要能放下大约 1GB（克隆本身约 700MB；`--copy` 到另一块磁盘会再占一份）。

已经有一份检出时可以不重新克隆：

```bash
.venv/bin/python scripts/import_word_atlas.py \
  --from /path/to/word-atlas --media --copy
```

媒体由 `/atlas-media/...` 直接返回。`<img>` 和 `<audio>` 带不上登录头，
所以这个路径不校验 token。文件是 CC BY 的公开素材，不是学习进度。
缺文件返回 404，页面去掉插画，发音退回浏览器 TTS。

若前面还有只把 `/api` 反代进 Python、其余交给 `web/dist` 的 Caddy，
要让 `/api/atlas-media` 继续进 Python（前端请求的就是这条路径）。
现在的 systemd 单进程部署不用改 Caddy：FastAPI 会自己剥掉 `/api`。

## Mac（只构建前端）

媒体不要打进 `web/dist`，也不要 rsync 到服务器。

```bash
cd web
npm ci
npm run build
rsync -a --delete dist/ user@server:/opt/grammar-kb/web/dist/
```

服务器上没有 npm。Python 依赖仍按原来的方式：`pip install -e .` 或项目现用的
可编辑安装。本改动没有新的 Python 包。
