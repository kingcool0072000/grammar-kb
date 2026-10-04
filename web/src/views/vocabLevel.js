import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 背单词级别详情（备课中心子页）：
//   2.1 本级单词内容（词表+搜索，L0=哈一课本高频词=原词汇表）
//   2.2 单词考试设计（试卷生成/打印）
//   2.3 级别解锁配置（每学生手动解锁级别，与考试解锁取 max）
export async function mountVocabLevel(el, { level = 0 } = {}) {
  const lv = Math.max(0, Math.min(5, Number(level) || 0))
  el.innerHTML = `
    <div class="view-head">
      <h1>背单词 · L${lv}${lv === 0 ? '（课本高频词）' : ''}</h1>
      <p>本级词库 · 考试设计 · 解锁配置。</p>
    </div>
    <div id="vl-body"><p class="muted">加载中…</p></div>
  `
  const body = el.querySelector('#vl-body')

  // ---- 数据：本级词 + 解锁配置 + 学生列表 ----
  let data, unlock, students
  try {
    ;[data, unlock, students] = await Promise.all([
      api.vocabLevels({ maxLevel: 5, level: lv }),
      api.vocabUnlockGet().catch(() => ({})),
      api.users().catch(() => ({ users: [] })),
    ])
  } catch (e) {
    body.innerHTML = `<p style="color:#b42318">加载失败：${escapeHtml(e.message)}</p>`
    return
  }
  const words = (data.items || []).filter((w) => w.level === lv)
  const counts = data.counts || {}
  const unlocked = data.unlocked ?? 0
  const studs = (students.users || []).filter((u) => u.role === 'student')

  render()

  function glossOf(w) {
    if (w.gloss) return w.gloss
    try {
      const m = typeof w.meanings === 'string' ? JSON.parse(w.meanings || '[]') : (w.meanings || [])
      return (Array.isArray(m) ? m : []).slice(0, 2).join('；')
    } catch { return '' }
  }

  function render() {
    body.innerHTML = `
      <!-- 2.1 本级单词内容 -->
      <section class="vl-sec card">
        <header class="vl-head">
          <h3>📚 本级单词 <i>${words.length} 词${lv === 0 ? ' · 哈一课本高频词（原词汇表）' : ''}</i></h3>
          <div class="vl-tools">
            <input id="vl-search" placeholder="搜索单词 / 释义…"/>
          </div>
        </header>
        <div class="vl-words" id="vl-words"></div>
        <div class="vl-pager" id="vl-pager"></div>
      </section>

      <!-- 2.2 单词考试 · 试卷生成（考试资产，与学生无关） -->
      <section class="vl-sec card">
        <header class="vl-head">
          <h3>📝 单词考试 · 试卷生成</h3>
          <div class="vl-tools">
            <button class="btn-primary" id="vl-gen">🎲 生成试卷</button>
          </div>
        </header>
        <p class="muted">随机选词（不同首字母/词性，自动规避人名/地名）· 中译英 20 + 英译中 20 + 拼写挖空 10 · 每题带词性提示 · 与历史试卷重复 ≤10 词。流程：生成 → 预览 → 满意后保存存档。</p>
        <div id="vl-paper-out"></div>
        <div id="vl-paper-history"></div>
      </section>

      <!-- 2.3 级别解锁配置 -->
      <section class="vl-sec card">
        <header class="vl-head"><h3>🔓 级别解锁配置 <i>手动配置优先与考试解锁取最大值</i></h3></header>
        <div class="vl-unlock-rows">
          ${studs.map((u) => `
            <div class="vl-unlock-row" data-stu="${escapeHtml(u.user)}">
              <b>🎒 ${escapeHtml(u.user)}</b>
              <div class="vl-lv-btns">
                ${[0, 1, 2, 3, 4, 5].map((n) => `
                  <button class="chip ${(unlock[u.user] ?? 0) >= n ? 'primary' : ''}" data-stu-lv="${n}" title="解锁到 L${n}">L${n}</button>`).join('')}
              </div>
              <span class="vl-unlock-state">当前生效解锁到 <b>L${data.unlocked ?? 0}</b></span>
            </div>`).join('') || '<p class="muted">暂无学生账号（先在学生管理添加）。</p>'}
        </div>
      </section>`

    // 分页展示（每页 40 词；搜索时按结果分页）
    const PAGE = 40
    let page = 0
    const search = body.querySelector('#vl-search')
    const curList = () => {
      const q = search.value.trim().toLowerCase()
      return !q ? words : words.filter((w) =>
        (w.word || '').toLowerCase().includes(q) || glossOf(w).includes(q))
    }
    function renderPage() {
      const list = curList()
      const pages = Math.max(1, Math.ceil(list.length / PAGE))
      if (page >= pages) page = pages - 1
      const slice = list.slice(page * PAGE, page * PAGE + PAGE)
      body.querySelector('#vl-words').innerHTML = wordRows(slice)
      body.querySelector('#vl-pager').innerHTML = `
        <button class="chip" data-pg="prev" ${page === 0 ? 'disabled' : ''}>‹ 上一页</button>
        <span class="vl-page-info">第 ${page + 1} / ${pages} 页 · 共 ${list.length} 词</span>
        <button class="chip" data-pg="next" ${page >= pages - 1 ? 'disabled' : ''}>下一页 ›</button>`
      body.querySelectorAll('[data-pg]').forEach((b) =>
        b.addEventListener('click', () => {
          page += b.dataset.pg === 'next' ? 1 : -1
          if (page < 0) page = 0
          renderPage()
        }))
      // 点词卡 → 完整内容弹窗
      body.querySelectorAll('.vl-word[data-word]').forEach((w) =>
        w.addEventListener('click', () => openWordDialog(w.dataset.word)))
    }
    search.addEventListener('input', () => { page = 0; renderPage() })
    renderPage()

    // 词卡完整内容弹窗
    function openWordDialog(wordStr) {
      const w = words.find((x) => x.word === wordStr)
      if (!w) return
      const dlg = document.createElement('div')
      dlg.className = 'plan-editor'
      let exEn = '', exZh = ''
      try {
        const ex = typeof w.example === 'string' ? JSON.parse(w.example || '{}') : (w.example || {})
        exEn = ex.en || ''
        exZh = ex.zh || ex.explain || ''
      } catch { /* 无例句 */ }
      let forms = ''
      try {
        const extra = typeof w.extra === 'string' ? JSON.parse(w.extra || '{}') : (w.extra || {})
        const f = extra.forms || {}
        forms = Object.entries(f).map(([k, v]) => `${k}: ${v}`).join(' · ')
      } catch { /* 无词形 */ }
      dlg.innerHTML = `
        <div class="plan-editor-mask"></div>
        <div class="plan-editor-body card vl-word-dlg">
          <h3>${escapeHtml(w.word || '')} <i class="vl-dlg-pos">${(w.pos || []).join(' ')}</i></h3>
          <div class="vl-dlg-sec"><b>释义</b><p>${escapeHtml(glossOf(w) || '（暂无）')}</p></div>
          ${forms ? `<div class="vl-dlg-sec"><b>词形变化</b><p>${escapeHtml(forms)}</p></div>` : ''}
          ${exEn ? `<div class="vl-dlg-sec"><b>例句</b><p class="vl-dlg-en">${escapeHtml(exEn)}</p>${exZh ? `<p class="vl-dlg-zh">${escapeHtml(exZh)}</p>` : ''}</div>` : ''}
          <div class="chip-row"><button class="chip" id="vl-dlg-close">关闭</button></div>
        </div>`
      document.body.appendChild(dlg)
      const close = () => dlg.remove()
      dlg.querySelector('.plan-editor-mask').addEventListener('click', close)
      dlg.querySelector('#vl-dlg-close').addEventListener('click', close)
    }
    // 试卷生成：三步流程 生成（未存档）→ 预览 → 保存
    async function genPaper() {
      const out = body.querySelector('#vl-paper-out')
      out.innerHTML = '<p class="muted">生成中…</p>'
      try {
        const r = await api.vocabPaperGenerate(lv, false) // 先不存档
        const p = r.paper
        let saved = false
        out.innerHTML = `
          <div class="vl-paper-card">
            <div class="vl-paper-meta">
              <b>卷号 ${escapeHtml(p.paper_id)}</b>
              <span>中译英 ${p.zh2en.length} · 英译中 ${p.en2zh.length} · 拼写 ${p.spell.length} · 与历史重复 ${p.overlap_with_history} 词</span>
              <i class="vl-paper-state">${saved ? '已存档' : '未存档（预览态）'}</i>
            </div>
            <details class="vl-paper-preview">
              <summary>预览试卷内容</summary>
              <div class="vl-pv-sec"><b>一、中译英</b>${p.zh2en.slice(0, 5).map((w, i) => `<p>${i + 1}. ${escapeHtml(w.pos_cn || '')} ${escapeHtml(w.gloss)} → ______</p>`).join('')}<p class="muted">…共 ${p.zh2en.length} 题</p></div>
              <div class="vl-pv-sec"><b>二、英译中</b>${p.en2zh.slice(0, 5).map((w, i) => `<p>${i + 1}. ${escapeHtml(w.word)}（${escapeHtml(w.pos_cn || '')}）→ ______</p>`).join('')}<p class="muted">…共 ${p.en2zh.length} 题</p></div>
              <div class="vl-pv-sec"><b>三、拼写</b>${p.spell.map((w, i) => `<p>${i + 1}. ${escapeHtml(w.pos_cn || '')} ${escapeHtml(w.gloss.slice(0, 16))}： <code>${escapeHtml(w.blanked)}</code></p>`).join('')}</div>
            </details>
            <div class="vl-paper-ops">
              <button class="chip" id="vl-preview">👁 预览试卷</button>
              <button class="btn-primary" id="vl-save">💾 保存存档</button>
              <button class="chip" id="vl-print">🖨 打印</button>
            </div>
          </div>`
        const stateEl = out.querySelector('.vl-paper-state')
        // 预览：新标签页看完整卷
        out.querySelector('#vl-preview').addEventListener('click', () => {
          const w = window.open('', '_blank')
          w.document.write(r.html)
          w.document.close()
        })
        // 保存：调存档端点 → 状态变已存档 + 刷新历史
        out.querySelector('#vl-save').addEventListener('click', async () => {
          try {
            await api.vocabPaperSave(p)
            saved = true
            stateEl.textContent = '已存档 ✓'
            out.querySelector('#vl-save').disabled = true
            out.querySelector('#vl-save').textContent = '已保存'
            toast(`✅ 试卷 ${p.paper_id} 已存档`)
            loadHistory()
          } catch (e) { alert('保存失败：' + e.message) }
        })
        // 打印：新窗口写 HTML 直接打印
        out.querySelector('#vl-print').addEventListener('click', () => {
          const w = window.open('', '_blank')
          w.document.write(r.html)
          w.document.close()
          setTimeout(() => w.print(), 400)
        })
      } catch (e) {
        out.innerHTML = `<p style="color:#b42318">生成失败：${escapeHtml(e.message)}</p>`
      }
    }
    body.querySelector('#vl-gen').addEventListener('click', () => genPaper())

    async function loadHistory() {
      const host = body.querySelector('#vl-paper-history')
      try {
        const r = await api.vocabPaperHistory(lv)
        const items = r.papers || []
        host.innerHTML = items.length ? `
          <div class="vl-hist">
            <b>📚 已生成 ${items.length} 卷（考试资产）</b>
            ${items.slice(0, 8).map((x) => `
              <span class="vl-hist-item">
                ${escapeHtml(x.paper_id)} · ${x.count} 词 · ${x.created_at.slice(0, 10)}
                <button class="chip vl-hist-op" data-hprev="${escapeHtml(x.paper_id)}">👁</button>
                <button class="chip vl-hist-op del" data-hdel="${escapeHtml(x.paper_id)}">🗑</button>
              </span>`).join('')}
          </div>` : '<p class="muted">尚无生成记录。</p>'
        host.querySelectorAll('[data-hprev]').forEach((b) =>
          b.addEventListener('click', async () => {
            try {
              const r = await api.vocabPaperPreview(b.dataset.hprev)
              const w = window.open('', '_blank')
              w.document.write(r.html)
              w.document.close()
            } catch (e) { alert('预览失败：' + e.message) }
          }))
        host.querySelectorAll('[data-hdel]').forEach((b) =>
          b.addEventListener('click', async () => {
            if (!confirm(`删除试卷 ${b.dataset.hdel}？`)) return
            try {
              await api.vocabPaperDelete(b.dataset.hdel)
              toast('已删除')
              loadHistory()
            } catch (e) { alert(e.message) }
          }))
      } catch { host.innerHTML = '' }
    }
    loadHistory()
    // 解锁配置
    body.querySelectorAll('.vl-unlock-row').forEach((row) => {
      const stu = row.dataset.stu
      row.querySelectorAll('[data-stu-lv]').forEach((b) =>
        b.addEventListener('click', async () => {
          const n = Number(b.dataset.stuLv)
          try {
            const r = await api.vocabUnlockPut(stu, n)
            unlock[stu] = n
            row.querySelectorAll('[data-stu-lv]').forEach((x) =>
              x.classList.toggle('primary', Number(x.dataset.stuLv) <= n))
            row.querySelector('.vl-unlock-state').innerHTML =
              `当前生效解锁到 <b>L${r.unlocked}</b>`
            toast(`✅ ${stu} 手动解锁到 L${n}（生效 L${r.unlocked}）`)
          } catch (e) { alert('设置失败：' + e.message) }
        }))
    })
  }

  function wordRows(list) {
    if (!list.length) return '<p class="muted">没有匹配的词条。</p>'
    return list.map((w) => `
      <div class="vl-word" data-word="${escapeHtml(w.word || '')}" title="点击查看完整内容">
        <b>${escapeHtml(w.word || '')}</b>
        <span class="vl-pos">${(w.pos || []).join(' ')}</span>
        <i>${escapeHtml(glossOf(w).slice(0, 36))}</i>
      </div>`).join('')
  }

  function toast(msg) {
    const t = document.createElement('div')
    t.className = 'plan-toast'
    t.textContent = msg
    document.body.appendChild(t)
    setTimeout(() => t.remove(), 3200)
  }
}
