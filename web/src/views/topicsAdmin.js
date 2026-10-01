import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 专题内容管理：JSON 导入制作 + 上架/开放学生配置 + 删除。
// 专题是「内容」（类似生成的考卷）：制作后上架，可配置给特定学生。
// 结构：{nodes:[{key,title,explain,wrong_qs[],general_qs[],children[]}]}，
// 题目 = {ref:{lecture,qnum}} 引用题库 或 自含 {stem,answer,explain}。
export async function mountTopicsAdmin(el) {
  el.innerHTML = '<p class="muted">加载中…</p>'
  let students = []
  try {
    const r = await api.users()
    students = (r.users || []).filter((u) => u.role === 'student').map((u) => u.user)
  } catch { /* 学生列表不可用时仍可管理 */ }
  await render()

  async function render() {
    let list = []
    try { list = await api.topicsAdminList() }
    catch (e) { el.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`; return }
    el.innerHTML = `
      <div class="view-head">
        <h1>专题内容管理</h1>
        <p>制作结构化专题（JSON 导入）→ 上架 → 配置开放学生。学生端「专题学习」自学并标记进度。</p>
      </div>
      <section class="card ta-import">
        <h3>📥 JSON 导入 / 更新专题</h3>
        <p class="reading-hint">同 topic_id 覆盖更新（学生进度保留）。meta：topic_id/title/subtitle/emoji/assigned_to（逗号分隔=指定学生，空=全员）。</p>
        <textarea id="ta-json" rows="10" placeholder='${escapeHtml(JSON.stringify({
          meta: { topic_id: 'tense-26-28', title: '⏰ 时态错题歼灭手册', assigned_to: 'malin' },
          content: { nodes: [{ key: 't1', title: '现在完成时', explain: '讲解…',
            wrong_qs: [{ ref: { lecture: 27, qnum: 3 }, note: '做错过' },
                       { stem: 'He ___ (live) here since 2020.', answer: 'has lived', explain: 'since+过去点' }],
            general_qs: [], children: [] }] },
        }, null, 1))}'></textarea>
        <div class="chip-row">
          <button class="btn-primary" id="ta-import-btn">导入</button>
          <button class="chip" id="ta-validate-btn">仅校验</button>
        </div>
        <p id="ta-msg" class="reading-hint"></p>
      </section>
      <section class="ta-list">
        ${list.length ? list.map((t) => `
          <div class="ta-row card" data-tid="${escapeHtml(t.topic_id)}">
            <div class="ta-info">
              <b>${escapeHtml(t.emoji)} ${escapeHtml(t.title)}</b>
              <span class="muted">${escapeHtml(t.subtitle || '')} · ${t.node_count} 个知识点节点 · 更新 ${(t.updated_at || '').slice(0, 10)}</span>
            </div>
            <div class="ta-ops">
              <button class="chip" data-pub="${escapeHtml(t.topic_id)}" data-on="${t.published ? '0' : '1'}">${t.published ? '⏸ 下架' : '▲ 上架'}</button>
              <button class="chip" data-asg="${escapeHtml(t.topic_id)}">👤 开放: ${t.assigned_to ? escapeHtml(t.assigned_to) : '全员'}</button>
              <button class="chip" data-prev="${escapeHtml(t.topic_id)}">👁 预览</button>
              <button class="chip ta-del" data-del="${escapeHtml(t.topic_id)}">删除</button>
            </div>
            ${t.published ? '' : '<i class="ta-off-flag">已下架</i>'}
          </div>`).join('') : '<p class="muted">暂无专题。用上方 JSON 导入第一个。</p>'}
      </section>`
    bind(list)
  }

  function bind(list) {
    const msg = (ok, text) => {
      const m = el.querySelector('#ta-msg')
      m.innerHTML = ok ? `✅ ${escapeHtml(text)}` : `<span style="color:#b42318">❌ ${escapeHtml(text)}</span>`
    }
    const parseInput = () => {
      const raw = el.querySelector('#ta-json').value.trim()
      if (!raw) throw new Error('请粘贴 JSON')
      let d
      try { d = JSON.parse(raw) } catch (e) { throw new Error(`JSON 语法错误：${e.message}`) }
      if (!d.meta || !d.content) throw new Error('顶层须含 meta 与 content')
      return d
    }
    el.querySelector('#ta-import-btn').addEventListener('click', async () => {
      try {
        const { meta, content } = parseInput()
        const got = await api.topicsAdminImport(meta, content)
        msg(true, `已导入 ${got.topic_id}（${got.title}）`)
        setTimeout(render, 600)
      } catch (e) { msg(false, e.message) }
    })
    el.querySelector('#ta-validate-btn').addEventListener('click', async () => {
      try {
        const { meta, content } = parseInput()
        await api.topicsAdminImport({ ...meta, topic_id: `validate-${Date.now()}` }, content)
        msg(true, '结构校验通过（已导入临时专题，5 秒后自动清理）')
        setTimeout(render, 300)
      } catch (e) { msg(false, e.message) }
    })
    el.querySelectorAll('[data-pub]').forEach((b) =>
      b.addEventListener('click', async () => {
        try {
          await api.topicsAdminPublish(b.dataset.pub, b.dataset.on === '1')
          render()
        } catch (e) { alert(e.message) }
      }))
    el.querySelectorAll('[data-asg]').forEach((b) =>
      b.addEventListener('click', async () => {
        const tid = b.dataset.asg
        const cur = (list.find((x) => x.topic_id === tid) || {}).assigned_to || ''
        const input = prompt(
          `开放给哪些学生？（逗号分隔用户名；留空 = 全员可见）\n可用学生：${students.join(', ') || '（学生列表不可用）'}`,
          cur,
        )
        if (input === null) return
        try {
          await api.topicsAdminAssign(tid, input.trim())
          render()
        } catch (e) { alert(e.message) }
      }))
    el.querySelectorAll('[data-prev]').forEach((b) =>
      b.addEventListener('click', () => {
        sessionStorage.setItem('gkb-preview-topic', b.dataset.prev)
        location.hash = `#/topics/${encodeURIComponent(b.dataset.prev)}`
      }))
    el.querySelectorAll('[data-del]').forEach((b) =>
      b.addEventListener('click', async () => {
        if (!confirm(`删除专题「${b.dataset.del}」？学生进度记录保留。`)) return
        try { await api.topicsAdminDelete(b.dataset.del); render() }
        catch (e) { alert(e.message) }
      }))
  }
}
