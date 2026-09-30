import { api } from '../api.js'
import { mountStudents } from './students.js'
import { escapeHtml } from '../render.js'

// 教师版 · 备课中心：教学内容管理按两大板块组织——
//   哈1 语法（初中语法课 / 知识点体系 / 词汇表 / 作业题库）
//   FCE 听说读写（真题库与阅读派生文 / FCE 知识库）
export async function mountPrep(el, ctx) {
  // 备课中心双子标签：内容管理（教学内容与题库）/ 学生管理（账号+总目标）
  let sub = 'content'
  el.innerHTML = `
    <div class="view-head">
      <h1>备课中心</h1>
      <p>教学内容与题库管理：哈1 语法 + FCE 听说读写 + 泛读馆。</p>
    </div>
    <div class="ana-tabs prep-subtabs">
      <button class="reading-btn primary" data-sub="content">📚 内容管理</button>
      <button class="reading-btn" data-sub="students">🎒 学生管理</button>
    </div>
    <div id="prep-body"></div>
  `
  const bodyEl = el.querySelector('#prep-body')
  const render = () => {
    el.querySelectorAll('[data-sub]').forEach((b) =>
      b.classList.toggle('primary', b.dataset.sub === sub))
    if (sub === 'students') {
      const host = document.createElement('div')
      bodyEl.replaceChildren(host)
      mountStudents(host, { bare: true })
    } else {
      mountContent(bodyEl)
    }
  }
  el.querySelectorAll('[data-sub]').forEach((b) =>
    b.addEventListener('click', () => { sub = b.dataset.sub; render() }))
  render()
}

async function mountContent(el) {
  el.innerHTML = '<p class="muted">加载中…</p>'
  const stats = await api.stats().catch(() => null)
  let papers = null, bases = null, derived = null, vex = null, lib = null, vlevels = null
  try {
    ;[papers, bases, derived, vex, lib, vlevels] = await Promise.all([
      api.fcePapers(),
      api.readingArticles({ kind: 'base' }),
      api.readingArticles(),
      api.vocabExams().catch(() => null),
      api.libraryBooks().catch(() => null),
      api.vocabLevels({ maxLevel: 5 }).catch(() => null),
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

  el.innerHTML = `
    <div class="view-head">
      <h1>备课中心</h1>
      <p>教学内容与题库管理：哈1 语法 + FCE 听说读写。</p>
    </div>

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
              const open = lv <= vocabUnlocked
              return `<button class="prep-card vocab-lv ${open ? '' : 'locked'}" data-vlv="${lv}" ${open ? '' : 'disabled'}>
                <b>${open ? `${n}` : '🔒'}</b><span>${open ? `L${lv} · ${lv === 0 ? '课本高频词' : '级别词库'}${lv === vocabUnlocked && lv < 5 ? ' · 当前解锁到' : ''}` : `L${lv} · 考试解锁`}</span>
              </button>`
            }).join('')}
            <button class="prep-card" data-go="vocabExam">
              <b>L${vocabUnlocked}</b><span>词汇级别考试 · 解锁配置</span>
            </button>
            <button class="prep-card" data-go="vocab">
              <b>词汇表</b><span>释义 · 词形 · 出处</span>
            </button>
          </div>
          <p class="reading-hint">L0 恒开；L${vocabUnlocked} → L${vocabUnlocked + 1} 需在词汇级别考试中 ≥80 分自动解锁（登记成绩即生效），点击「词汇级别考试」配置。</p>
        </div>
      </section>

      <!-- ================= FCE 听说读写 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎧 FCE 听说读写</h2>
        </header>
        <div class="gd-subgroup">
          <h3>🧬 阅读内容</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="readingAdmin">
              <b>${baseCount}</b><span>FCE 原文段落</span>
            </button>
            <button class="prep-card" data-go="readingAdmin">
              <b>${derivedCount}</b><span>派生阅读文章</span>
            </button>
            <button class="prep-card" data-go="fcePapers">
              <b>${fceTotal}</b><span>FCE 真题（4 Test）</span>
            </button>
            <button class="prep-card" data-go="fce">
              <b>FCE 知识库</b><span>19 天语法专题</span>
            </button>
          </div>
          <p class="reading-hint">阅读内容：按 Test/Part 管理原文段落与派生文章（新增/编辑/删除）；真题库查看四套试卷与练习明细。</p>
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
    b.addEventListener('click', () => { location.hash = '/vocabExam' }))
}
