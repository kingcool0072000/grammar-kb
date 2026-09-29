import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 学生管理：增删学生账号 + 给每个学生配置学习总目标。
// 目标结构复用 /plan/goals（词汇/讲次/FCE/截止日），讲次下拉来自备课内容。
export async function mountStudents(el) {
  el.innerHTML = `
    <div class="view-head">
      <h1>学生管理</h1>
      <p>增减学生账号，给每个学生配置冲刺总目标。</p>
    </div>
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
    } catch { editorData = { lectures: [] } }
    render()
  }

  function goalsText(g) {
    const parts = []
    if (g?.deadline) parts.push(`⏰ ${g.deadline}`)
    if (g?.vocab_target) parts.push(`🔤 ${g.vocab_target} 词`)
    if (g?.lecture_target) parts.push(`📚 到第 ${g.lecture_target} 讲`)
    if (g?.fce_target) parts.push(`🎧 FCE ${g.fce_target} Test`)
    if (g?.reading?.length) parts.push(`📖 ${g.reading.length} 本`)
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
    body.querySelectorAll('[data-go-plan]').forEach((b) =>
      b.addEventListener('click', () => {
        sessionStorage.setItem('gkb-plan-student', b.dataset.goPlan)
        location.hash = '/plan'
      }))
  }

  function openGoalDialog(user) {
    const dlg = document.createElement('div')
    dlg.className = 'plan-editor'
    dlg.innerHTML = `
      <div class="plan-editor-mask"></div>
      <div class="plan-editor-body card pe-body">
        <h3>配置 ${escapeHtml(user)} 的总目标</h3>
        <div class="st-goal-dlg"><p class="muted">加载中…</p></div>
      </div>`
    document.body.appendChild(dlg)
    const close = () => dlg.remove()
    dlg.querySelector('.plan-editor-mask').addEventListener('click', close)

    ;(async () => {
      let g = {}
      try { g = (await api.planGoals({ user })).goals || {} } catch { /* 空 */ }
      const host = dlg.querySelector('.st-goal-dlg')
      host.innerHTML = `
        <label class="plan-field">冲刺截止日期
          <input id="sg-deadline" type="date" value="${g.deadline || '2027-01-31'}"/>
        </label>
        <label class="plan-field">词汇总目标（累计掌握词数）
          <input id="sg-vocab" type="number" min="0" value="${g.vocab_target ?? ''}" placeholder="2000"/>
        </label>
        <label class="plan-field">讲次总目标（哈1 课程推进到）
          <select id="sg-lecture">
            <option value="">— 选择 —</option>
            ${(editorData?.lectures || []).map((l) =>
              `<option value="${l.number}" ${g.lecture_target === l.number ? 'selected' : ''}>第 ${l.number} 讲 · ${escapeHtml(l.title)}</option>`).join('')}
          </select>
        </label>
        <label class="plan-field">FCE 总目标（完成 Test 套数）
          <input id="sg-fce" type="number" min="0" max="8" value="${g.fce_target ?? ''}" placeholder="8"/>
        </label>
        <div class="chip-row">
          <button class="btn-primary" id="sg-save">保存</button>
          <button class="chip" id="sg-cancel">取消</button>
        </div>`
      dlg.querySelector('#sg-cancel').addEventListener('click', close)
      dlg.querySelector('#sg-save').addEventListener('click', async () => {
        const ng = {}
        const dl = host.querySelector('#sg-deadline').value
        if (dl) ng.deadline = dl
        const vb = Number(host.querySelector('#sg-vocab').value)
        if (vb > 0) ng.vocab_target = vb
        const lc = Number(host.querySelector('#sg-lecture').value)
        if (lc > 0) ng.lecture_target = lc
        const fc = Number(host.querySelector('#sg-fce').value)
        if (fc > 0) ng.fce_target = fc
        if (g?.reading) ng.reading = g.reading
        try {
          await api.planGoalsPut(user, ng)
          toast(`✅ ${user} 的总目标已保存`)
          close()
          loadAllGoals()
        } catch (e) { alert('保存失败：' + e.message) }
      })
    })()
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
