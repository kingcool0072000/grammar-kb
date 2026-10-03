import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 学生管理：增删学生账号 + 给每个学生配置学习总目标。
// 目标结构复用 /plan/goals（词汇/讲次/FCE/截止日），讲次下拉来自备课内容。
export async function mountStudents(el, { bare = false } = {}) {
  el.innerHTML = `
    ${bare ? '' : `<div class="view-head">
      <h1>学生管理</h1>
      <p>增减学生账号，给每个学生配置冲刺总目标。</p>
    </div>`}
    <div id="st-body"><p class="muted">加载中…</p></div>
  `
  const body = el.querySelector('#st-body')
  let users = [], editorData = null

  async function load() {
    try {
      const r = await api.users()
      users = (r.users || []).filter((u) => u.role === 'student')
    } catch (e) {
      body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
      return
    }
    try {
      editorData = await api.planEditorData({ user: users[0]?.user || 'malin' })
    } catch { editorData = { lectures: [], goal_assets: {} } }
    render()
  }

  function goalsText(g) {
    const parts = []
    if (g?.lectures?.length) parts.push(`📚 ${g.lectures.length} 讲`)
    if (g?.vocab_target) parts.push(`🔤 +${g.vocab_target} 词`)
    if (g?.fce_parts?.length) parts.push(`🎧 ${g.fce_parts.length} Part`)
    if (g?.articles?.length) parts.push(`📖 ${g.articles.length} 篇`)
    if (g?.reading?.length) parts.push(`📚 ${g.reading.length} 书`)
    if (g?.vocab_papers?.length) parts.push(`📝 ${g.vocab_papers.length} 卷`)
    if (g?.deadline) parts.push(`⏰ ${g.deadline}`)
    return parts.length ? parts.join(' · ') : '未配置'
  }

  function render() {
    body.innerHTML = `
      <section class="st-add card">
        <h3>➕ 新增学生</h3>
        <div class="st-add-row">
          <label>用户名 <input id="st-new-user" placeholder="字母/数字，2-32 位"/></label>
          <label>密码 <input id="st-new-pass" type="password" placeholder="至少 4 位"/></label>
          <button class="btn-primary" id="st-add-btn">添加</button>
        </div>
      </section>
      <section class="st-list">
        ${users.map((u) => `
          <div class="st-row card" data-user="${escapeHtml(u.user)}">
            <div class="st-info">
              <b>🎒 ${escapeHtml(u.user)}</b>
              <span class="st-goal" data-goal="${escapeHtml(u.user)}">目标加载中…</span>
            </div>
            <div class="st-ops">
              <button class="chip" data-cfg="${escapeHtml(u.user)}">🎯 配置目标</button>
              <button class="chip" data-go-plan="${escapeHtml(u.user)}">📅 去计划表</button>
              <button class="chip st-del" data-del="${escapeHtml(u.user)}">删除</button>
            </div>
          </div>`).join('')}
      </section>`
    bind()
    loadAllGoals()
  }

  async function loadAllGoals() {
    for (const u of users) {
      try {
        const r = await api.planGoals({ user: u.user })
        const el2 = body.querySelector(`[data-goal="${CSS.escape(u.user)}"]`)
        if (el2) el2.textContent = '🎯 ' + goalsText(r.goals)
      } catch { /* 单个失败不影响整体 */ }
    }
  }

  function bind() {
    body.querySelector('#st-add-btn').addEventListener('click', async () => {
      const user = body.querySelector('#st-new-user').value.trim()
      const password = body.querySelector('#st-new-pass').value
      if (!user || !password) return alert('用户名与密码须填完整')
      try {
        await api.userAdd(user, password)
        toast(`🎉 学生 ${user} 已添加`)
        load()
      } catch (e) { alert('添加失败：' + e.message) }
    })
    body.querySelectorAll('[data-del]').forEach((b) =>
      b.addEventListener('click', async () => {
        const name = b.dataset.del
        if (!confirm(`删除学生「${name}」？其登录账号将失效（学习记录保留）。`)) return
        try {
          await api.userDel(name)
          toast(`已删除 ${name}`)
          load()
        } catch (e) { alert(e.message) }
      }))
    body.querySelectorAll('[data-cfg]').forEach((b) =>
      b.addEventListener('click', () => openGoalDialog(b.dataset.cfg)))
    // 计划看板「配置目标」跳转联动：自动打开该生弹窗
    const openMark = sessionStorage.getItem('gkb-open-goal')
    if (openMark && users.some((u) => u.user === openMark)) {
      sessionStorage.removeItem('gkb-open-goal')
      setTimeout(() => openGoalDialog(openMark), 300)
    }
    body.querySelectorAll('[data-go-plan]').forEach((b) =>
      b.addEventListener('click', () => {
        sessionStorage.setItem('gkb-plan-student', b.dataset.goPlan)
        location.hash = '/plan'
      }))
  }

  async function openGoalDialog(user) {
    const ga = editorData?.goal_assets || {}
    const lecOpts = ga.lectures || []
    const vocabLv = ga.vocab_levels || []
    const fceParts = ga.fce_parts || []
    const arts = (ga.articles || []).filter((a) => a.kind === 'derived')
    const books = ga.books || []
    const papers = ga.vocab_papers || []
    // 回填：一个学生同一时段只有一个总目标（plan_goals 一人一行），
    // 打开即带出当前配置——保存=修改覆盖，学习记录不受影响。
    let cur = {}
    try { cur = (await api.planGoals({ user })).goals || {} } catch { cur = {} }
    const hasGoal = Object.keys(cur).length > 0
    const dlg = document.createElement('div')
    dlg.className = 'plan-editor'
    dlg.innerHTML = `
      <div class="plan-editor-mask"></div>
      <div class="plan-editor-body card pe-body st-goal-body">
        <h3>🎯 ${escapeHtml(user)} 的总目标</h3>
        <p class="muted" style="margin:0 0 12px;font-size:12px">${hasGoal
          ? '已带出当前配置——保存即整体替换这一个目标；学习过的内容不受影响。'
          : '该生暂无总目标。所有选项来自备课中心已有资产。'}</p>

        <div class="pe-sec"><h4>📚 哈一课程 + 考卷 <i>选择要完成的讲次（标「卷」= 有考卷）</i><button type="button" class="chip sg-all" data-all="lec">全选</button></h4>
          <div class="pe-lec-grid" id="sg-lec">${lecOpts.map((l) => `
            <button type="button" class="pe-lec" data-sg-lec="${l.number}" title="${l.has_paper ? l.questions + ' 题考卷' : '无考卷'}">
              <b>${l.number}</b>${escapeHtml(l.title)}${l.has_paper ? '<i>卷</i>' : ''}
            </button>`).join('')}</div>
        </div>

        <div class="pe-sec"><h4>🔤 单词级别 + 完成数量<button type="button" class="chip sg-all" data-all="vlv">全选</button></h4>
          <div class="sg-vocab-row">
            ${vocabLv.map((v) => `
              <label class="sg-vocab-lv"><input type="checkbox" data-sg-vlv="${v.level}"/> L${v.level}
                <i>${v.words} 词</i></label>`).join('')}
            <label class="sg-vocab-n">目标新掌握 <input id="sg-vocab-n" type="number" min="0" placeholder="如 500"/> 词</label>
          </div>
        </div>

        <div class="pe-sec"><h4>🎧 FCE 题目（Part 维度）</h4>
          ${[1, 2, 3, 4].map((tid) => {
            const ps = fceParts.filter((x) => x.test_id === tid)
            if (!ps.length) return ''
            return `<div class="pe-fce-group"><b>${ps[0].test_title}
                <button type="button" class="chip sg-all" data-all="fce" data-tid="${tid}">全选</button></b>
              <div class="pe-fce-parts">${ps.map((x) => `
                <button type="button" class="pe-part" data-sg-fce="${escapeHtml(x.label)}">${escapeHtml(x.label.replace(ps[0].test_title + ' · ', ''))}<i>${x.questions}</i></button>`).join('')}</div>
            </div>`
          }).join('') || '<p class="muted">FCE 题库不可用</p>'}
        </div>

        <div class="pe-sec"><h4>📖 精读文章<button type="button" class="chip sg-all" data-all="art">全选</button></h4>
          <div class="pe-fce-parts" style="max-height:120px;overflow-y:auto">${arts.map((a) => `
            <button type="button" class="pe-part" data-sg-art="${a.id}">${escapeHtml(a.title.slice(0, 20))}<i>${a.words}</i></button>`).join('') || '<p class="muted">暂无派生文章</p>'}</div>
        </div>

        <div class="pe-sec"><h4>📚 泛读书-章节目标 <i>按章勾选；点书名展开/收起章节</i><button type="button" class="chip sg-all" data-all="bk">全选书</button></h4>
          ${books.map((b) => `
            <div class="sg-book-row" data-sg-bkrow="${b.id}">
              <label class="sg-book-main"><input type="checkbox" data-sg-bk="${b.id}"/>
                <b>${escapeHtml(b.title.slice(0, 30))}</b>
                <i>${b.valid_chapters || b.chapters} 章 · ${b.goal_words} 词</i>
                <em>已读 ${b.percent}%</em>
              </label>
              <div class="pe-chchips sg-chchips" data-sg-chips="${b.id}">
                ${(b.chapter_opts || []).map((c) =>
                  `<button type="button" class="chip pe-chchip" data-sg-ch="${b.id}:${c.idx}" title="${escapeHtml(c.title)}">第${c.no}章 ${c.words}词</button>`).join('')}
              </div>
            </div>`).join('') || '<p class="muted">书架暂无书目</p>'}
        </div>

        <div class="pe-sec"><h4>📝 单词考试试卷 <i>关联已生成的试卷资产</i><button type="button" class="chip sg-all" data-all="paper">全选</button></h4>
          <div class="pe-fce-parts">${papers.map((x) => `
            <button type="button" class="pe-part" data-sg-paper="${escapeHtml(x.paper_id)}">L${x.level} · ${x.paper_id.slice(-8)}<i>${(x.created_at || '').slice(5, 10)}</i></button>`).join('') || '<p class="muted">尚未生成试卷（备课中心-背单词）</p>'}</div>
        </div>

        <label class="plan-field">冲刺截止日期
          <input id="sg-deadline" type="date" value="${cur.deadline || '2027-01-31'}"/>
        </label>
        <div class="chip-row">
          <button class="btn-primary" id="sg-save">保存</button>
          ${hasGoal ? '<button class="chip st-abandon" id="sg-abandon">放弃目标</button>' : ''}
          <button class="chip" id="sg-cancel">取消</button>
        </div>
      </div>`
    document.body.appendChild(dlg)
    const close = () => dlg.remove()
    dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
    dlg.querySelector('#sg-cancel').addEventListener('click', close)

    const sel = {
      lectures: new Set(), vocab_levels: new Set(), fce_parts: new Set(),
      articles: new Set(), books: new Set(), vocab_papers: new Set(),
      reading_chapters: new Map(), // bookId(str) -> Set(章idx)
    }
    // ---- 回填当前配置（按钮 on / checkbox checked 与 sel 同步）----
    const prefill = () => {
      for (const n of cur.lectures || []) {
        sel.lectures.add(String(n))
        const b = dlg.querySelector(`[data-sg-lec="${CSS.escape(String(n))}"]`)
        if (b) b.classList.add('on')
      }
      for (const lv of cur.vocab_levels || []) {
        sel.vocab_levels.add(Number(lv))
        const c = dlg.querySelector(`[data-sg-vlv="${Number(lv)}"]`)
        if (c) c.checked = true
      }
      for (const label of cur.fce_parts || []) {
        sel.fce_parts.add(label)
        const b = dlg.querySelector(`[data-sg-fce="${CSS.escape(label)}"]`)
        if (b) b.classList.add('on')
      }
      for (const id of cur.articles || []) {
        sel.articles.add(String(id))
        const b = dlg.querySelector(`[data-sg-art="${id}"]`)
        if (b) b.classList.add('on')
      }
      for (const id of cur.reading || []) {
        sel.books.add(String(id))
        const c = dlg.querySelector(`[data-sg-bk="${id}"]`)
        if (c) c.checked = true
      }
      // 章选回填：reading_chapters={bookId:[idx]}；未配置的书勾选时全选
      for (const [bid, idxs] of Object.entries(cur.reading_chapters || {})) {
        const set = new Set(idxs.map(Number))
        sel.reading_chapters.set(String(bid), set)
        for (const i of set) {
          const chip = dlg.querySelector(`[data-sg-ch="${bid}:${i}"]`)
          if (chip) chip.classList.add('primary')
        }
      }
      for (const pid of cur.vocab_papers || []) {
        sel.vocab_papers.add(pid)
        const b = dlg.querySelector(`[data-sg-paper="${CSS.escape(pid)}"]`)
        if (b) b.classList.add('on')
      }
      if (cur.vocab_target) dlg.querySelector('#sg-vocab-n').value = cur.vocab_target
    }
    prefill()
    // attr 用连字符（选择器 [data-xx]），dataset 键自动驼峰
    const bindToggle = (attr, set) => dlg.querySelectorAll(`[data-${attr}]`).forEach((b) =>
      b.addEventListener('click', (e) => {
        e.preventDefault()
        const key = attr.replace(/-(.)/g, (_, c) => c.toUpperCase())
        const v = b.dataset[key]
        if (set.has(v)) { set.delete(v); b.classList.remove('on') }
        else { set.add(v); b.classList.add('on') }
      }))
    bindToggle('sg-lec', sel.lectures)
    bindToggle('sg-fce', sel.fce_parts)
    bindToggle('sg-art', sel.articles)
    bindToggle('sg-paper', sel.vocab_papers)
    // 全选/全不选（再点一次取消全部）
    const selMap = { lec: sel.lectures, vlv: sel.vocab_levels,
                     fce: sel.fce_parts, art: sel.articles,
                     bk: sel.books, paper: sel.vocab_papers }
    const attrMap = { lec: 'sg-lec', fce: 'sg-fce', art: 'sg-art',
                      paper: 'sg-paper' }
    dlg.querySelectorAll('.sg-all').forEach((b) =>
      b.addEventListener('click', (e) => {
        e.stopPropagation()
        const kind = b.dataset.all
        const set = selMap[kind]
        if (kind === 'vlv' || kind === 'bk') {
          // checkbox 型：全选 = 全部勾上；再点 = 全清
          const boxes = b.closest('.pe-sec').querySelectorAll('input[type=checkbox]')
          const allOn = [...boxes].every((x) => x.checked)
          boxes.forEach((x) => {
            const v = kind === 'vlv' ? Number(x.dataset.sgVlv) : x.dataset.sgBk
            x.checked = !allOn
            if (!allOn) set.add(v); else set.delete(v)
          })
        } else {
          const attr = attrMap[kind]
          let btns = [...b.closest(kind === 'fce' ? '.pe-fce-group' : '.pe-sec')
            .querySelectorAll(`[data-${attr}]`)]
          if (kind === 'fce') btns = btns.filter((x) =>
            x.closest('.pe-fce-group').querySelector('.sg-all').dataset.tid === b.dataset.tid)
          const allOn = btns.every((x) => x.classList.contains('on'))
          const key = attr.replace(/-(.)/g, (_, c) => c.toUpperCase())
          btns.forEach((x) => {
            const v = x.dataset[key]
            if (allOn) { set.delete(v); x.classList.remove('on') }
            else { set.add(v); x.classList.add('on') }
          })
        }
      }))
    dlg.querySelectorAll('[data-sg-vlv]').forEach((c) =>
      c.addEventListener('change', () => {
        const v = Number(c.dataset.sgVlv)
        if (c.checked) sel.vocab_levels.add(v); else sel.vocab_levels.delete(v)
      }))
    dlg.querySelectorAll('[data-sg-bk]').forEach((c) =>
      c.addEventListener('change', () => {
        const v = c.dataset.sgBk
        if (c.checked) sel.books.add(v); else sel.books.delete(v)
        // 勾书默认全选该书章；取消书清空该书章选
        const chips = dlg.querySelectorAll(`[data-sg-ch="${v}:"], [data-sg-ch^="${v}:"]`)
        const on = c.checked
        const set = sel.reading_chapters.get(v) || new Set()
        chips.forEach((chip) => {
          const idx = Number(chip.dataset.sgCh.split(':')[1])
          if (on) { set.add(idx); chip.classList.add('primary') }
          else { set.delete(idx); chip.classList.remove('primary') }
        })
        if (set.size) sel.reading_chapters.set(v, set)
        else sel.reading_chapters.delete(v)
      }))
    // 章 chip 点选切换（不影响书勾选态）
    dlg.querySelectorAll('[data-sg-ch]').forEach((chip) =>
      chip.addEventListener('click', () => {
        const [bid, idxS] = chip.dataset.sgCh.split(':')
        const idx = Number(idxS)
        const set = sel.reading_chapters.get(bid) || new Set()
        if (set.has(idx)) { set.delete(idx); chip.classList.remove('primary') }
        else { set.add(idx); chip.classList.add('primary') }
        if (set.size) sel.reading_chapters.set(bid, set)
        else sel.reading_chapters.delete(bid)
      }))

    dlg.querySelector('#sg-save').addEventListener('click', async () => {
      const ng = {}
      if (sel.lectures.size) ng.lectures = [...sel.lectures].map(Number)
      if (sel.vocab_levels.size) ng.vocab_levels = [...sel.vocab_levels].map(Number)
      const vn = Number(dlg.querySelector('#sg-vocab-n').value)
      if (vn > 0) ng.vocab_target = vn
      if (sel.fce_parts.size) ng.fce_parts = [...sel.fce_parts]
      if (sel.articles.size) ng.articles = [...sel.articles].map(Number)
      if (sel.books.size) ng.reading = [...sel.books].map(Number)
      // 章选目标（新口径）：{bookId: [章idx...]}；勾了书但没细选章的书
      // 不写章表（总目标泛读按整书词数计）
      if (sel.reading_chapters.size) {
        ng.reading_chapters = Object.fromEntries(
          [...sel.reading_chapters.entries()].map(([k, s]) => [Number(k), [...s].sort((a, b) => a - b)]))
      }
      if (sel.vocab_papers.size) ng.vocab_papers = [...sel.vocab_papers]
      const dl = dlg.querySelector('#sg-deadline').value
      if (dl) ng.deadline = dl
      try {
        await api.planGoalsPut(user, ng)
        toast(`✅ ${user} 总目标已保存`)
        close()
        loadAllGoals()
      } catch (e) { alert('保存失败：' + e.message) }
    })
    // 放弃目标：清空配置（唯一目标置空），学习记录保留在成绩/录音/背词等活动数据中
    const abBtn = dlg.querySelector('#sg-abandon')
    if (abBtn) abBtn.addEventListener('click', async () => {
      if (!confirm(`放弃「${user}」的当前总目标？\n学习过的内容（成绩/录音/背词/阅读进度）全部保留，只是不再跟踪该目标。`)) return
      try {
        await api.planGoalsPut(user, {})
        toast(`已放弃 ${user} 的总目标（学习记录保留）`)
        close()
        loadAllGoals()
      } catch (e) { alert('操作失败：' + e.message) }
    })
  }

  function toast(msg) {
    const t = document.createElement('div')
    t.className = 'plan-toast'
    t.textContent = msg
    document.body.appendChild(t)
    setTimeout(() => t.remove(), 3200)
  }

  await load()
}
