import { api } from '../api.js'
import { escapeHtml } from '../render.js'
// 专题清单动态加载：TOPICS 是整本静态手册（大内容块），拉进首包会显著
// 增大 index bundle——只在备课中心需要计数，await 动态 import 取。

// 教师版 · 备课中心：教学内容管理按两大板块组织——
//   哈1 语法（初中语法课 / 知识点体系 / 词汇表 / 作业题库）
//   FCE 听说读写（真题库与阅读派生文 / FCE 知识库）
export async function mountPrep(el, ctx) {
  // 备课中心：纯内容管理（学生管理已迁至计划表双子 Tab）
  await mountContent(el)
}

async function mountContent(el) {
  el.innerHTML = '<p class="muted">加载中…</p>'
  const stats = await api.stats().catch(() => null)
  // 已上架专题数（topics-admin/list；失败显示 — 不阻塞备课中心）
  let topicCount = '—'
  try { topicCount = (await api.topicsAdminList()).length } catch { /* 忽略 */ }
  let papers = null, bases = null, derived = null, vex = null, lib = null, vlevels = null, topicsProg = null
  try {
    ;[papers, bases, derived, vex, lib, vlevels, topicsProg] = await Promise.all([
      api.fcePapers().catch(() => null),
      api.readingArticles({ kind: 'base' }).catch(() => null),
      api.readingArticles().catch(() => null),
      api.vocabExams().catch(() => null),
      api.libraryBooks().catch(() => null),
      api.vocabLevels({ maxLevel: 5, countsOnly: true }).catch(() => null),
      api.topicsProgress().catch(() => null),
    ])
  } catch { /* FCE 数据不可用时仍展示哈1板块 */ }
  const libCount = lib?.books?.length ?? 0
  const vocabCounts = vlevels?.counts || {}
  const vocabUnlocked = vex?.unlocked_level ?? vlevels?.unlocked ?? 0

  const lecCount = stats ? stats.lectures : '—'
  const kpCount = stats ? stats.knowledge_points : '—'
  const fceTotal = papers?.reduce((s, t) => s + Object.values(t.papers).flat().reduce((x, p) => x + p.questions, 0), 0) || 0
  const derivedCount = derived?.length || 0
  const baseCount = bases?.length || 0
  // 专题手册数（上方动态 import 已取）+ 已完成进度
  const topicsDone = (topicsProg || []).filter((r) => r.done).length

  el.innerHTML = `
    <div class="gd-board">
      <!-- ================= 哈1 语法 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📚 哈1 语法</h2>
        </header>
        <div class="gd-subgroup">
          <h3>📖 初中语法课</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="courses">
              <b>${lecCount}</b><span>讲课程</span>
            </button>
            <button class="prep-card" data-go="taxonomy">
              <b>${kpCount}</b><span>知识点（体系树）</span>
            </button>
            <button class="prep-card" data-go="paperAdmin">
              <b>作业卷管理</b><span>题目 · 答案</span>
            </button>
          </div>
          <p class="reading-hint">点开语法课查看整讲内容；知识点体系按「语法大类 → 主题」两级组织，可定位到讲义原文。</p>
        </div>
      </section>

      <!-- ================= 背单词（独立板块：分级词库 + 解锁配置） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🔤 背单词</h2>
        </header>
        <div class="gd-subgroup">
          <h3>📚 分级词库 L0-L5</h3>
          <div class="prep-cards">
            ${[0, 1, 2, 3, 4, 5].map((lv) => {
              const n = vocabCounts[String(lv)] ?? 0
              return `<button class="prep-card vocab-lv" data-vlv="${lv}">
                <b>${n}</b><span>L${lv} · ${lv === 0 ? '课本高频词' : '级别词库'}</span>
              </button>`
            }).join('')}
          </div>
          <p class="reading-hint">点开级别管理词库、生成考卷、配置解锁（当前生效解锁到 L${vocabUnlocked}）；学生端 L${vocabUnlocked} 以上仍锁定。</p>
        </div>
      </section>

      <!-- ================= 精读（独立板块：派生阅读文章列表） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📖 精读</h2>
        </header>
        <div class="gd-subgroup">
          <h3>✍️ 派生阅读文章</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="readingList">
              <b>${derivedCount}</b><span>派生精读文章 · 列表与编辑</span>
            </button>
          </div>
          <p class="reading-hint">派生精读文章列表（新增/编辑/删除）；原文段落管理在 FCE 板块；学生的录音提交与评分在批改中心。</p>
        </div>
      </section>

      <!-- ================= FCE 听说读写 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎧 FCE 听说读写</h2>
        </header>
        <div class="gd-subgroup">
          <h3>🧬 真题与知识</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="fcePapers">
              <b>${fceTotal}</b><span>FCE 真题（4 Test）</span>
            </button>
            <button class="prep-card" data-go="fce">
              <b>FCE 知识库</b><span>19 天语法专题</span>
            </button>
          </div>
          <p class="reading-hint">真题库查看四套试卷与练习明细；FCE 知识库为语法专题手册。</p>
        </div>
      </section>

      <!-- ================= 专题学习（错题歼灭手册等自学专题） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🧩 专题学习</h2>
        </header>
        <div class="gd-subgroup">
          <h3>🎯 自学专题手册</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="topicsAdmin">
              <b>制作 / 管理</b><span>JSON 导入 · 上架 · 开放学生</span>
            </button>
            <button class="prep-card" data-go="topics">
              <b>${topicCount}</b><span>已上架专题 · 预览与学生进度</span>
            </button>
          </div>
          <p class="reading-hint">专题是结构化内容：子知识点{讲解+错题回顾+泛化题}，题目引用题库或自含；制作后上架、配置给特定学生；学生在学生版「专题学习」自学并标记进度。</p>
        </div>
      </section>

      <!-- ================= 泛读馆（整体收进备课中心） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📚 泛读馆</h2>
        </header>
        <div class="gd-subgroup">
          <h3>📖 书架与管理</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="library">
              <b>${libCount}</b><span>书架 · 上传与管理</span>
            </button>
            <button class="prep-card" data-go="librarySettings">
              <b>全馆设置</b><span>AI 预习 · 字典 · 学生主题</span>
            </button>
          </div>
          <p class="reading-hint">书架里上传 epub、点开书籍做章节预习与必读章配置；学生侧的「泛读馆」Tab 不变。</p>
        </div>
      </section>
    </div>
  `

  el.querySelectorAll('[data-go]').forEach((b) => {
    b.addEventListener('click', () => {
      location.hash = `#/${b.dataset.go}`
    })
  })
  el.querySelectorAll('[data-vlv]').forEach((b) =>
    b.addEventListener('click', () => { location.hash = `/vocabLevel/${b.dataset.vlv}` }))
}
