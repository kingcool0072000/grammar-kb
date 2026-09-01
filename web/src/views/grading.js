import { api, getAuth } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 批改中心（首页）：学生提交的作业按两大板块归类——
//   FCE 听说读写（朗读录音批改 / 作文批改 / 练习明细）
//   哈1 语法（哈一作业成绩 / 背单词记录）
// 每个分区：待办数字 + 最近动态 + 一键跳到对应工具。
export async function mountGrading(el) {
  el.innerHTML = '<div class="view-head"><h1>批改中心</h1><p>加载中…</p></div>'
  let recs, fceSubs, recite, exams
  try {
    ;[recs, fceSubs, recite, exams] = await Promise.all([
      api.readingRecordings({ limit: 200 }),
      api.fceSubmissions({ limit: 200 }),
      api.reciteSessions({ limit: 100 }),
      api.examsList(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }

  // ---- 待办汇总 ----
  const pendRec = recs.filter((r) => r.status === 'pending')
  const pendEssay = fceSubs.filter((s) => s.status === 'pending')
  const today = new Date().toISOString().slice(0, 10)
  const reciteToday = recite.filter((s) => (s.created_at || '').slice(0, 10) === today)
  const latestExam = exams[0]
  const totalPend = pendRec.length + pendEssay.length

  el.innerHTML = `
    <div class="view-head">
      <h1>批改中心</h1>
      <p>学生作业的待批与动态汇总。${totalPend ? `<b class="gd-pend-badge">${totalPend} 项待批</b>` : '当前没有待批项 ✅'}</p>
    </div>

    <div class="grading-stats">
      <div class="grading-stat ${pendRec.length ? 'warn' : ''}" data-go="recording" title="FCE 口语朗读录音批改">
        <b>${pendRec.length}</b><span>FCE 朗读录音待批</span>
      </div>
      <div class="grading-stat ${pendEssay.length ? 'warn' : ''}" data-go="essay" title="FCE 写作批改">
        <b>${pendEssay.length}</b><span>FCE 作文待批</span>
      </div>
      <div class="grading-stat" data-go="haya-exam" title="哈一作业成绩管理">
        <b>${latestExam ? latestExam.score : '—'}</b><span>最近哈一成绩${latestExam ? `（第${latestExam.lecture}讲）` : ''}</span>
      </div>
      <div class="grading-stat" data-go="recite" title="背单词练习记录">
        <b>${reciteToday.length}</b><span>今日背单词（组）</span>
      </div>
    </div>

    <div class="gd-board">
      <!-- ================= FCE 听说读写 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>🎧 FCE 听说读写</h2>
          <nav>
            <button class="reading-btn small primary" data-go="recording">🎙 朗读录音${pendRec.length ? `（${pendRec.length}）` : ''}</button>
            <button class="reading-btn small primary" data-go="essay">✍️ 作文${pendEssay.length ? `（${pendEssay.length}）` : ''}</button>
            <button class="reading-btn small" data-go="fce-subs">📋 练习明细</button>
          </nav>
        </header>
        <div class="gd-subgroup">
          <h3>🎙 朗读录音${pendRec.length ? ` · 待批 ${pendRec.length}` : ''}</h3>
          ${recs.slice(0, 5).map((r) => recRow(r)).join('') || '<p class="reading-hint">暂无录音提交</p>'}
        </div>
        <div class="gd-subgroup">
          <h3>✍️ 作文与练习</h3>
          ${fceSubs.slice(0, 5).map((s) => fceRow(s)).join('') || '<p class="reading-hint">暂无 FCE 练习</p>'}
        </div>
      </section>

      <!-- ================= 哈1 语法 ================= -->
      <section class="gd-section">
        <header class="gd-section-head">
          <h2>📚 哈1 语法</h2>
          <nav>
            <button class="reading-btn small primary" data-go="haya-exam">📊 哈一作业成绩</button>
            <button class="reading-btn small" data-go="recite">📖 背单词记录</button>
          </nav>
        </header>
        <div class="gd-subgroup">
          <h3>📊 哈一作业成绩 · 最近</h3>
          ${exams.slice(0, 5).map((e) => `
            <div class="fce-his-row">
              <span class="fce-his-what">第 ${e.lecture} 讲</span>
              <b class="fce-his-score ${e.score >= 90 ? 'ok' : e.score >= 70 ? '' : 'bad'}">${e.score}</b>
              <span class="fce-his-date">${e.date}${e.wrong && e.wrong.length ? ` · 错 ${e.wrong.length} 题` : ''}</span>
            </div>`).join('') || '<p class="reading-hint">暂无成绩</p>'}
        </div>
        <div class="gd-subgroup">
          <h3>📖 背单词 · 最近练习</h3>
          ${recite.slice(0, 5).map((s) => `
            <div class="fce-his-row">
              <span class="fce-his-what">${escapeHtml(s.user)} · ${scopeCn(s.scope)}${s.mode === 'flip' ? '（自评）' : ''}</span>
              <b class="fce-his-score ${s.acc >= 80 ? 'ok' : ''}">${s.acc}%</b>
              <span class="fce-his-date">${s.total} 词 · 错 ${s.wrong} · ${fmtDur(s.duration_sec || 0)} · ${(s.created_at || '').slice(5, 10)}</span>
            </div>`).join('') || '<p class="reading-hint">暂无背单词记录</p>'}
          ${recite.length ? `<p class="reading-hint">易错词（最近）：${topWrongWords(recite)}</p>` : ''}
        </div>
      </section>
    </div>
  `

  el.querySelectorAll('[data-go]').forEach((b) => {
    b.addEventListener('click', () => go(b.dataset.go))
  })
}

function recRow(r) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
      ${r.status === 'graded'
        ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
        : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
    </div>`
}

function fceRow(s) {
  return `
    <div class="fce-his-row">
      <span class="fce-his-what">${escapeHtml(s.user)} · T${s.test_id} P${s.part}</span>
      ${s.status === 'auto'
        ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b>`
        : s.status === 'graded'
          ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b>`
          : '<b class="fce-his-score pend">待批改</b>'}
      <span class="fce-his-date">${s.duration_sec ? fmtDur(s.duration_sec) + ' · ' : ''}${(s.created_at || '').slice(0, 10)}</span>
    </div>`
}

// 跳转路由（sessionStorage 联动目标页自动打开对应面板）
export function go(target) {
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
  }
}

function fmtDur(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

function scopeCn(scope) {
  return { all: '全部词表', verb: '只动词', special: '特殊拼写' }[scope] || scope || '全部词表'
}

function topWrongWords(sessions) {
  const freq = new Map()
  for (const s of sessions.slice(0, 20)) {
    for (const w of s.wrong_words || []) freq.set(w, (freq.get(w) || 0) + 1)
  }
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([w, n]) => `${escapeHtml(w)}×${n}`)
    .join('、') || '（暂无）'
}
