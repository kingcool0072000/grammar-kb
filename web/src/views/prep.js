import { api } from '../api.js'
import { escapeHtml } from '../render.js'
// 专题数从 topics-admin/list 接口取（v2 结构化专题；v1 静态手册已退役）。

// 教师版 · 备课中心：按学科闭环重组——
//   每个学科分区 = 内容入口卡 + 待批队列（内嵌批改）+ 成绩入口。
//   批改中心不再单独占顶栏 Tab；待批队列懒挂载自 grading/queues.js 与
//   grading/vocabGrading.js（车道A实现），学情分析/学习日志仍跳独立批改页。
export async function mountPrep(el, ctx) {
  await mountContent(el)
}

async function mountContent(el) {
  el.innerHTML = '<p class="muted">加载中…</p>'
  const stats = await api.stats().catch(() => null)
  // 已上架专题数（topics-admin/list；失败显示 — 不阻塞备课中心）
  let topicCount = '—'
  try { topicCount = (await api.topicsAdminList()).length } catch { /* 忽略 */ }
  let papers = null, bases = null, derived = null, lib = null, vlevels = null, topicsProg = null
  try {
    ;[papers, bases, derived, lib, vlevels, topicsProg] = await Promise.all([
      api.fcePapers().catch(() => null),
      api.readingArticles({ kind: 'base' }).catch(() => null),
      api.readingArticles().catch(() => null),
      api.libraryBooks().catch(() => null),
      api.vocabLevels({ maxLevel: 7, countsOnly: true }).catch(() => null),
      api.topicsProgress().catch(() => null),
    ])
  } catch { /* FCE 数据不可用时仍展示哈1板块 */ }
  const libCount = lib?.books?.length ?? 0
  const vocabCounts = vlevels?.counts || {}

  const lecCount = stats ? stats.lectures : '—'
  const kpCount = stats ? stats.knowledge_points : '—'
  const fceTotal = papers?.reduce((s, t) => s + Object.values(t.papers).flat().reduce((x, p) => x + p.questions, 0), 0) || 0
  const derivedCount = derived?.length || 0
  const baseCount = bases?.length || 0
  // 专题手册数（上方动态 import 已取）+ 已完成进度
  const topicsDone = (topicsProg || []).filter((r) => r.done).length

  // 共享「学生」筛选（模式照抄批改中心 #gd-user）：单学生 / 接口失败时整条隐藏。
  // 队列始终按 qstate.student 过滤（与批改看板一致，默认第一个学生）。
  let students = []
  try {
    const r = await api.users()
    students = (r.users || []).filter((u) => u.role === 'student').map((u) => u.user)
  } catch { /* 单学生/接口不可用时筛选条隐藏，队列展示全部 */ }
  // 默认空串 = 聚合全部学生待批（教师打开即见所有孩子的队列）；选具体学生才过滤
  const qstate = { student: '' }
  const snapshots = new Map() // 队列数据快照按学生分键，切回学生时免重拉
  const unmounts = []
  let mountSeq = 0

  el.innerHTML = `
    <div class="view-head">
      <h1>备课中心</h1>
    </div>
    ${students.length > 1 ? `
    <div class="pw-stubar">
      <label class="pw-student">学生
        <select id="pp-user">
          <option value="" ${!qstate.student ? 'selected' : ''}>全部学生</option>
          ${students.map((x) => `<option value="${escapeHtml(x)}" ${qstate.student === x ? 'selected' : ''}>${escapeHtml(x)}</option>`).join('')}
        </select>
      </label>
      <span class="muted" style="font-size:12.5px">筛选各学科分区的待批队列与成绩</span>
    </div>` : ''}

    <div class="gd-board">
      <!-- ================= 1 · 哈1 语法 ================= -->
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
            <button class="prep-card" data-go="exams">
              <b>📝 成绩登记</b><span>哈一考试成绩 · 录入与修改</span>
            </button>
          </div>
          <p class="reading-hint">点开语法课查看整讲内容；知识点体系按「语法大类 → 主题」两级组织，可定位到讲义原文；哈一考试成绩在本区成绩卡登记。</p>
        </div>
      </section>

      <!-- ================= 2 · 背单词（分级词库 + 考试打分队列） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🔤 背单词 <span class="prep-qbadge" data-qbadge="vocab" hidden></span></h2>
        </header>
        <div class="gd-subgroup">
          <h3>📚 分级词库 L0-L7</h3>
          <div class="prep-cards">
            ${[0, 1, 2, 3, 4, 5, 6, 7].map((lv) => {
              const n = vocabCounts[String(lv)] ?? 0
              return `<button class="prep-card vocab-lv" data-vlv="${lv}">
                <b>${n}</b><span>L${lv} · ${lv === 0 ? '课本高频词' : '级别词库'}</span>
              </button>`
            }).join('')}
            <button class="prep-card" data-go="vocabExam">
              <b>📈 考试成绩记录</b><span>单词考试 · 成绩与趋势</span>
            </button>
          </div>
          <p class="reading-hint">点开级别可管理词库、生成考卷，并为每个学生设置单词级别（直接指定，不要求考试 80 分）。考试打分与成绩登记在本区下方队列。</p>
        </div>
        <div class="prep-queue" data-q="vocabGrading"><p class="muted">加载中…</p></div>
      </section>

      <!-- ================= 3 · 精读（派生文章 + 待批录音队列） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📖 精读 <span class="prep-qbadge" data-qbadge="reading" hidden></span></h2>
        </header>
        <div class="gd-subgroup">
          <h3>✍️ 派生阅读文章</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="readingList">
              <b>${derivedCount}</b><span>派生精读文章 · 列表与编辑</span>
            </button>
          </div>
          <p class="reading-hint">派生精读文章列表（新增/编辑/删除）；原文段落管理在 FCE 板块；学生的录音提交与评分在本区下方队列。</p>
        </div>
        <div class="prep-queue" data-q="recording"><p class="muted">加载中…</p></div>
        <div class="prep-queue" data-q="recentGraded"><p class="muted">加载中…</p></div>
      </section>

      <!-- ================= 4 · 视频学习（清单 + 待批复述队列） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎬 视频学习 <span class="prep-qbadge" data-qbadge="video" hidden></span></h2>
        </header>
        <div class="gd-subgroup">
          <h3>🎞 看视频 + 复述</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="videoAdmin">
              <b>视频管理</b><span>清单 · 生僻词 · 提示 · 上下架</span>
            </button>
          </div>
          <p class="reading-hint">维护学生「视频学习」Tab 的视频清单（新增/编辑/上下架）；学生看完视频提交复述录音，复述批改在本区下方队列。</p>
        </div>
        <div class="prep-queue" data-q="retell"><p class="muted">加载中…</p></div>
      </section>

      <!-- ================= 5 · FCE 听说读写（真题听力 + 待批作文队列） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎧 FCE 听说读写 <span class="prep-qbadge" data-qbadge="fce" hidden></span></h2>
        </header>
        <div class="gd-subgroup">
          <h3>🧬 真题与知识</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="fcePapers">
              <b>${fceTotal}</b><span>FCE 真题（4 Test）</span>
            </button>
            <button class="prep-card" data-go="fceListen">
              <b>🎧 16 段</b><span>FCE 听力播放台</span>
            </button>
          </div>
          <p class="reading-hint">真题库查看四套试卷与练习明细；听力播放台带频谱进度条（平坦段=空白时间）；学生作文提交的批改在本区下方队列。</p>
        </div>
        <div class="prep-queue" data-q="essay"><p class="muted">加载中…</p></div>
      </section>

      <!-- ================= 6 · 微练习（每日3题 + 提升档素材 + 完成度） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>✏️ 微练习</h2>
        </header>
        <div class="gd-subgroup">
          <h3>📝 题库 · 句型 · 完成度</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="microAdmin">
              <b>题库管理</b><span>审核 · 正确率 · 手动加题</span>
            </button>
            <button class="prep-card" data-go="microProgress">
              <b>学生完成度</b><span>按学生汇总 · 逐天明细</span>
            </button>
            <button class="prep-card" data-go="microPatterns">
              <b>句型素材</b><span>复述原句 · 编辑 · 删除</span>
            </button>
          </div>
          <p class="reading-hint">学生每天自动抽 3 题（错题重练/变形/时态等）；句型素材是提升档「句型复述」的原句库；学生端「微练习」Tab 里可回看做题记录与提升回顾。</p>
        </div>
      </section>

      <!-- ================= 7 · 专题学习（错题歼灭手册等自学专题） ================= -->
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

      <!-- ================= 8 · 泛读馆（整体收进备课中心） ================= -->
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
              <b>全馆设置</b><span>学生主题 · 书籍解锁</span>
            </button>
          </div>
          <p class="reading-hint">书架里上传 epub、点开书籍进入阅读器；学生侧的「泛读馆」Tab 不变。</p>
        </div>
      </section>

      <!-- ================= 9 · 学情与日志（独立批改页两个子 Tab） ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📊 学情与日志</h2>
        </header>
        <div class="gd-subgroup">
          <h3>📈 全量数据（独立页）</h3>
          <div class="prep-cards">
            <button class="prep-card" data-go="grading?tab=analytics">
              <b>📊 学情分析</b><span>趋势 · 专注 · AI 周报</span>
            </button>
            <button class="prep-card" data-go="grading?tab=log">
              <b>📚 学习日志</b><span>打卡记录 · 学习明细</span>
            </button>
          </div>
          <p class="reading-hint">跨学科的长期趋势与全量日志在独立页查看，这里只放入口；日常待批在上方各学科分区完成。</p>
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

  const sel = el.querySelector('#pp-user')
  if (sel) {
    sel.addEventListener('change', (e) => {
      qstate.student = e.target.value
      remountQueues()
    })
  }

  remountQueues()

  /** 懒挂载/重挂各学科待批队列。学生切换时整体重挂；动态 import 失败给出可见错误。 */
  function remountQueues() {
    for (const fn of unmounts.splice(0)) { try { fn() } catch { /* 忽略旧队列清理失败 */ } }
    const hosts = [...el.querySelectorAll('.prep-queue[data-q]')]
    hosts.forEach((host) => { host.innerHTML = '<p class="muted">加载中…</p>' })
    const seq = ++mountSeq
    const hostOf = (q) => el.querySelector(`.prep-queue[data-q="${q}"]`)
    // 分区头待批角标：队列 onCount(n) 驱动，n=0 隐藏
    const setBadge = (key, n) => {
      const b = el.querySelector(`.prep-qbadge[data-qbadge="${key}"]`)
      if (!b) return
      b.textContent = `待批 ${n}`
      b.hidden = !n
    }
    // 与批改中心 go() 同映射：队列内「去批改」跳独立批改页（sessionStorage 联动自动展开对应视图）
    const go = (target) => {
      if (target === 'recording') {
        sessionStorage.setItem('gkb-open-review', '1')
        location.hash = '#/readingAdmin'
      } else if (target === 'essay') {
        sessionStorage.setItem('gkb-open-essay', '1')
        location.hash = '#/fcePapers'
      } else if (target === 'fce-subs') {
        location.hash = '#/fcePapers'
      } else if (target === 'haya-exam') {
        location.hash = '#/exams'
      } else if (target === 'recite') {
        location.hash = '#/grading?tab=analytics'
      }
    }
    Promise.all([
      import('./grading/queues.js'),
      import('./grading/vocabGrading.js'),
    ]).then(([queues, vocabGrading]) => {
      if (seq !== mountSeq) return // 学生已再次切换，本轮作废
      const base = { state: qstate, snapshots, go }
      // 单个队列挂载失败只标记自己的宿主，不连坐其它队列；
      // 异步挂载（返回 Promise）reject 时同样落到宿主上。
      const mountOne = (q, fn) => {
        const host = hostOf(q)
        if (!host) return
        const fail = (e) => { host.innerHTML = `<p class="muted">队列加载失败：${escapeHtml(e.message)}</p>` }
        try {
          const r = fn(host)
          if (typeof r === 'function') unmounts.push(r)
          else if (r && typeof r.catch === 'function') r.catch(fail)
        } catch (e) { fail(e) }
      }
      mountOne('recording', (h) =>
        queues.mountRecordingQueue(h, { ...base, onCount: (n) => setBadge('reading', n) }))
      mountOne('retell', (h) =>
        queues.mountRetellQueue(h, { ...base, onCount: (n) => setBadge('video', n) }))
      mountOne('essay', (h) =>
        queues.mountEssayQueue(h, { ...base, onCount: (n) => setBadge('fce', n) }))
      // 最近已批（录音/复述/作文 前3条）：非待办，不驱动待批角标
      mountOne('recentGraded', (h) =>
        queues.mountRecentGraded(h, { ...base, onCount: () => {} }))
      mountOne('vocabGrading', (h) =>
        vocabGrading.mountExamGrading(h, { state: qstate, students }))
    }).catch((e) => {
      if (seq !== mountSeq) return
      hosts.forEach((host) => {
        host.innerHTML = `<p class="muted">队列加载失败：${escapeHtml(e.message)}</p>`
      })
    })
  }
}
