import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'
import { fmtDur } from '../fmt.js'

// NCE2 听力跟读批改 & 成绩记录页（共享模块，支持独立页 #/nce2Review 及备课中心队列内嵌）：
//   - 按学生筛选、按类型筛选（全部 / 跟读录音 / 精听打卡）
//   - 顶部统计摘要（待批跟读 / 已批跟读与过关数 / 精听打卡数）
//   - 待批改区 + 默认展开的「已批成绩与打卡记录」（头部分数/过关徽标、录音回放、改分、多行评语、删除）

function kindTag(k) {
  return k === 'listen'
    ? '<span class="tag vr-kind-listen">👂 精听打卡</span>'
    : '<span class="tag vr-kind-shadow">🗣 跟读录音</span>'
}

function subSourceHtml(r) {
  return `${kindTag(r.kind)} Lesson ${r.lesson_id ?? '—'} · ${escapeHtml(r.lesson_title || '')}`
}

export async function mountNce2Review(el) {
  el.innerHTML = `
    <button class="fce-back-btn gd-tool-back" id="nce2r-back">← 返回备课中心</button>
    <div class="view-head"><h1>听力成绩与批改</h1><p>加载中…</p></div>
  `
  el.querySelector('#nce2r-back').addEventListener('click', () => {
    location.hash = '/prep'
  })
  let subs = []
  let students = []
  try {
    const [sList, uRes] = await Promise.all([
      api.nce2Queue('all'),
      api.users().catch(() => ({ users: [] })),
    ])
    subs = Array.isArray(sList) ? sList : []
    students = (uRes.users || []).filter((u) => u.role === 'student').map((u) => u.user)
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }
  const urlUser = new URLSearchParams(location.hash.split('?')[1] || '').get('user') || ''
  await renderNce2Review(el, subs, {
    students,
    initialUser: urlUser,
    onBack: () => {
      location.hash = '/prep'
      return true
    },
  })
}

export async function renderNce2Review(el, subs, opts) {
  const onBack = (opts || {}).onBack
  const items = Array.isArray(subs) ? subs : []
  const allStudents = [...new Set([
    ...((opts && opts.students) || []),
    ...items.map((r) => r.user).filter(Boolean),
  ])]
  let filterUser = (opts && opts.initialUser && allStudents.includes(opts.initialUser))
    ? opts.initialUser
    : ''
  let filterKind = 'all' // 'all' | 'shadow' | 'listen'

  el.innerHTML = `
    <button class="fce-back-btn gd-tool-back" id="nce2r-back">← 返回备课中心</button>
    <div class="view-head">
      <h1>听力成绩与批改</h1>
      <p>听学生的跟读录音（NCE2），10 分制打分（1 位小数）+ 评语；≥7 分显示「过关」，支持回听改分及查看精听打卡记录。</p>
    </div>
    <div class="pw-stubar">
      ${allStudents.length > 1 ? `
      <label class="pw-student">学生
        <select id="nce2r-user">
          <option value="">全部学生</option>
          ${allStudents.map((u) => `<option value="${escapeHtml(u)}" ${filterUser === u ? 'selected' : ''}>${escapeHtml(u)}</option>`).join('')}
        </select>
      </label>` : ''}
      <div class="vl-lv-filters" id="nce2r-kinds" style="margin-bottom:0">
        <button type="button" class="chip vl-lv-filter on" data-kind="all">全部</button>
        <button type="button" class="chip vl-lv-filter" data-kind="shadow">🗣 跟读录音</button>
        <button type="button" class="chip vl-lv-filter" data-kind="listen">👂 精听打卡</button>
      </div>
      <span class="muted" id="nce2r-summary" style="font-size:13px"></span>
    </div>
    <section class="fce-group" id="nce2r-pend-sec">
      <div class="fce-group-title" id="nce2r-pend-title">🎙 待批改</div>
      <div id="nce2r-list"></div>
    </section>
    <details class="rd-done-fold" id="nce2r-done-fold" open>
      <summary id="nce2r-done-sum">📈 已批成绩与打卡记录</summary>
      <div id="nce2r-done"></div>
    </details>
  `

  const box = el.querySelector('#nce2r-list')
  const doneBox = el.querySelector('#nce2r-done')
  const summaryEl = el.querySelector('#nce2r-summary')
  const pendTitle = el.querySelector('#nce2r-pend-title')
  const doneSum = el.querySelector('#nce2r-done-sum')
  const token = getAuth()?.token || ''

  const scoreBadgeHtml = (r) => {
    if (r.kind === 'listen') return '<b class="fce-his-score ok" data-badge>✅ 已打卡</b>'
    if (r.status === 'graded') {
      const s = Number(r.teacher_score)
      return `<b class="fce-his-score ${s >= 7 ? 'ok' : ''}" data-badge>${r.teacher_score}/10${s >= 7 ? ' 过关' : ''}</b>`
    }
    return '<b class="fce-his-score pend" data-badge>待批改</b>'
  }

  // 单行卡片构造（待批区/已处理区共用）：shadow 行带 audio + 分数/评语 +
  // 提交批改 + 删除；listen 行只有提示文案 + 删除按钮
  const buildCard = (r) => {
    const isGraded = r.status === 'graded'
    const row = document.createElement('div')
    row.className = 'reading-review-card'
    row.innerHTML = `
      <div class="reading-review-head">
        <b>${escapeHtml(r.user)}</b> · ${subSourceHtml(r)}
        ${scoreBadgeHtml(r)}
        <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span>
      </div>
      ${r.kind === 'listen'
        ? '<p class="reading-hint">👂 精听打卡（无录音）——打卡本身无需批改，只可删除。</p>'
        : '<audio controls preload="metadata" style="width:100%;margin:8px 0"></audio>'}
      ${r.kind === 'listen'
        ? '<button class="reading-btn danger" data-del title="删除这条提交">删除</button>'
        : `<div class="reading-review-grade">
        <label>分数 <input type="number" min="0" max="10" step="0.1" value="${r.teacher_score ?? 8}" style="width:68px" data-score></label>
        <textarea rows="3" placeholder="评语（听懂程度/发音/语调建议）" data-comment>${escapeHtml(r.teacher_comment || '')}</textarea>
        <button class="reading-btn primary" data-grade>${isGraded ? '更新批改' : '提交批改'}</button>
        <button class="reading-btn danger" data-del title="删除这条提交">删除</button>
      </div>`}
      <div class="reading-review-msg"></div>
    `
    const audio = row.querySelector('audio')
    if (audio && token) {
      audio.src = `/api/nce2/submissions/${r.id}/audio?token=${encodeURIComponent(token)}`
    }
    const msgEl = row.querySelector('.reading-review-msg')
    const gradeBtn = row.querySelector('[data-grade]')
    if (gradeBtn) {
      const scoreInput = row.querySelector('[data-score]')
      const commentInput = row.querySelector('[data-comment]')
      const reenable = () => { gradeBtn.disabled = false }
      scoreInput.addEventListener('input', reenable)
      commentInput.addEventListener('input', reenable)

      gradeBtn.addEventListener('click', async () => {
        const rawStr = scoreInput.value.trim()
        const rawScore = rawStr === '' ? NaN : Number(rawStr)
        const comment = commentInput.value
        if (!Number.isFinite(rawScore) || rawScore < 0 || rawScore > 10) {
          msgEl.textContent = '分数须为 0-10'
          return
        }
        const score = Math.round(rawScore * 10) / 10
        gradeBtn.disabled = true
        try {
          await api.nce2Grade(r.id, score, comment)
          r.status = 'graded'
          r.teacher_score = score
          r.teacher_comment = comment
          const badge = row.querySelector('[data-badge]')
          if (badge) badge.outerHTML = scoreBadgeHtml(r)
          gradeBtn.textContent = '更新批改'
          msgEl.innerHTML =
            `✅ 已批改：${score}/10${score >= 7 ? ' <b class="nce2-pass-tag">过关</b>' : ''}`
          paintStats()
        } catch (e) {
          msgEl.textContent = `失败：${e.message}`
          gradeBtn.disabled = false
        }
      })
    }
    row.querySelector('[data-del]').addEventListener('click', async () => {
      if (!confirm(`确定删除 ${r.user} 的这条 NCE2 提交（Lesson ${r.lesson_id} · ${fmtDur(r.duration_sec || 0)}）？删除后不可恢复。`)) return
      try {
        await api.nce2Delete(r.id)
        const idx = items.indexOf(r)
        if (idx >= 0) items.splice(idx, 1)
        renderLists()
      } catch (e) {
        msgEl.textContent = `删除失败：${e.message}`
      }
    })
    return row
  }

  function filteredItems() {
    return items.filter((r) => {
      if (filterUser && r.user !== filterUser) return false
      if (filterKind === 'listen' && r.kind !== 'listen') return false
      if (filterKind === 'shadow' && r.kind === 'listen') return false
      return true
    })
  }

  function paintStats() {
    const list = filteredItems()
    const pend = list.filter((r) => r.status === 'pending')
    const gradedShadow = list.filter((r) => r.kind !== 'listen' && r.status === 'graded')
    const passShadow = gradedShadow.filter((r) => Number(r.teacher_score) >= 7)
    const listenDone = list.filter((r) => r.kind === 'listen')
    const done = list.filter((r) => r.status !== 'pending')
    summaryEl.textContent =
      `待批跟读 ${pend.length} · 已批跟读 ${gradedShadow.length}（过关 ${passShadow.length}） · 精听打卡 ${listenDone.length}`
    pendTitle.textContent = `🎙 待批改（${pend.length}）`
    doneSum.innerHTML = `📈 已批成绩与打卡记录（${done.length}）<i>已批跟读与精听打卡 · 点开可回听录音、改分或删除</i>`
  }

  function renderLists() {
    const list = filteredItems()
    const pend = list.filter((r) => r.status === 'pending')
    const done = list.filter((r) => r.status !== 'pending')
      .slice()
      .sort((a, b) => (b.id || 0) - (a.id || 0))
    paintStats()
    box.innerHTML = ''
    doneBox.innerHTML = ''
    if (pend.length) {
      for (const r of pend.slice(0, 50)) box.append(buildCard(r))
    } else {
      box.innerHTML = done.length
        ? '<p class="reading-hint">没有待批的提交 🎉</p>'
        : '<p class="reading-hint">暂无 NCE2 听力提交</p>'
    }
    if (done.length) {
      for (const r of done.slice(0, 100)) doneBox.append(buildCard(r))
    } else {
      doneBox.innerHTML = '<p class="reading-hint">暂无已批成绩或打卡记录</p>'
    }
  }

  const userSel = el.querySelector('#nce2r-user')
  if (userSel) {
    userSel.addEventListener('change', (e) => {
      filterUser = e.target.value
      renderLists()
    })
  }
  el.querySelectorAll('#nce2r-kinds [data-kind]').forEach((btn) =>
    btn.addEventListener('click', () => {
      filterKind = btn.dataset.kind
      el.querySelectorAll('#nce2r-kinds [data-kind]').forEach((x) =>
        x.classList.toggle('on', x === btn))
      renderLists()
    }))

  el.querySelector('#nce2r-back').addEventListener('click', () => {
    try {
      if (onBack) {
        const result = onBack()
        if (result !== false) return
      }
    } catch { /* 回退到路由 */ }
    location.hash = '/prep?from=nce2Review' // 带 query 防同 hash（#/prep）赋值不触发路由
  })

  renderLists()
}

// 队列/已批折叠区共用行：学生名 + 课标题 + 类型 + 状态/分数 + 时长日期
export function nce2SubRow(r) {
  const graded = r.status === 'graded'
  // listen 行 status 恒 'done'，打卡本身无需批改，直接标「已打卡」
  const score = r.kind === 'listen'
    ? '<b class="fce-his-score ok">✅ 已打卡</b>'
    : graded
      ? `<b class="fce-his-score ${r.teacher_score >= 7 ? 'ok' : ''}">${r.teacher_score}/10${r.teacher_score >= 7 ? ' 过关' : ''}</b>`
      : '<b class="fce-his-score pend">待批改</b>'
  return `
    <div class="fce-his-row" data-nce2="${r.id}">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${subSourceHtml(r)}</span>
      ${score}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}
