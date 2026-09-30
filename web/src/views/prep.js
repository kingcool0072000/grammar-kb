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
  let papers = null, bases = null, derived = null, vex = null, lib = null
  try {
    ;[papers, bases, derived, vex, lib] = await Promise.all([
      api.fcePapers(),
      api.readingArticles({ kind: 'base' }),
      api.readingArticles(),
      api.vocabExams().catch(() => null),
      api.libraryBooks().catch(() => null),
    ])
  } catch { /* FCE 数据不可用时仍展示哈1板块 */ }
  const libCount = lib?.books?.length ?? 0

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
          <nav>
            <button class="reading-btn small primary" data-go="courses">📖 语法课</button>
            <button class="reading-btn small" data-go="taxonomy">🌳 知识点体系</button>
            <button class="reading-btn small" data-go="vocab">🔤 词汇表</button>
            <button class="reading-btn small" data-go="exams">📋 作业题库</button>
          </nav>
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
            <button class="prep-card" data-go="vocab">
              <b>词汇表</b><span>释义 · 词形 · 出处</span>
            </button>
            <button class="prep-card" data-go="exams">
              <b>哈一作业</b><span>成绩录入与题库</span>
            </button>
            <button class="prep-card" data-go="vocabExam">
              <b>L${vex?.unlocked_level ?? 0}</b><span>词汇级别考试 · 已解锁到</span>
            </button>
            <button class="prep-card" data-go="students">
              <b>学生管理</b><span>增减学生 · 配置总目标</span>
            </button>
          </div>
          <p class="reading-hint">点开语法课查看整讲内容；知识点体系按「语法大类 → 主题」两级组织，可定位到讲义原文。</p>
        </div>
      </section>

      <!-- ================= FCE 听说读写 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎧 FCE 听说读写</h2>
          <nav>
            <button class="reading-btn small primary" data-go="readingAdmin">🧬 阅读内容管理</button>
            <button class="reading-btn small" data-go="fce">📘 FCE 知识库</button>
            <button class="reading-btn small" data-go="fcePapers">📝 真题库</button>
          </nav>
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
          <nav>
            <button class="reading-btn small primary" data-go="library">📖 书架</button>
            <button class="reading-btn small" data-go="librarySettings">⚙️ 全馆设置</button>
          </nav>
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
}
