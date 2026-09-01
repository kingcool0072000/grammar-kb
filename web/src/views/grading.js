import { api } from '../api.js'
import { escapeHtml } from '../render.js'

// 教师版 · 批改中心：哈一作业成绩 / 英语阅读录音 / 背单词 / FCE 练习
// 四类学生作业的待办与最近动态汇总，一键跳到对应批改入口。
export async function mountGrading(el) {
  el.innerHTML = '<div class="view-head"><h1>批改中心</h1><p>加载中…</p></div>'
  let recs, fceSubs, recite, exams
  try {
    ;[recs, fceSubs, recite, exams] = await Promise.all([
      api.readingRecordings({ limit: 200 }),
      api.fceSubmissions({ limit: 200 }),
      api.reciteSessions({ limit: 200 }),
      api.examsList(),
    ])
  } catch (e) {
    el.querySelector('p').innerHTML = `<span style="color:#b42318">加载失败：${escapeHtml(e.message)}</span>`
    return
  }

  const pendRec = recs.filter((r) => r.status === 'pending')
  const pendEssay = fceSubs.filter((s) => s.status === 'pending')
  const today = new Date().toISOString().slice(0, 10)
  const reciteToday = recite.filter((s) => (s.created_at || '').slice(0, 10) === today)
  const latestExam = exams[0]

  el.innerHTML = `
    <div class="view-head">
      <h1>批改中心</h1>
      <p>四类学生作业的待办与动态：哈一作业 · 英语阅读 · 背单词 · FCE。</p>
    </div>
    <div class="grading-stats">
      <div class="grading-stat warn" data-go="reading">
        <b>${pendRec.length}</b><span>待批改录音</span>
      </div>
      <div class="grading-stat warn" data-go="essay">
        <b>${pendEssay.length}</b><span>待批改作文</span>
      </div>
      <div class="grading-stat" data-go="recite">
        <b>${reciteToday.length}</b><span>今日背单词（组）</span>
      </div>
      <div class="grading-stat" data-go="exams">
        <b>${latestExam ? latestExam.score : '—'}</b><span>最近哈一成绩${latestExam ? `（第${latestExam.lecture}讲）` : ''}</span>
      </div>
    </div>

    <section class="fce-group">
      <div class="fce-group-title">🎙 英语阅读${pendRec.length ? ` · 待批 ${pendRec.length}` : ''}</div>
      ${pendRec.length ? `
        <div class="grading-action">
          <button class="reading-btn primary" data-go="reading">去批改 ${pendRec.length} 条录音</button>
        </div>` : ''}
      ${recs.slice(0, 5).map((r) => `
        <div class="fce-his-row">
          <span class="fce-his-what">${escapeHtml(r.user)} · ${escapeHtml(r.article_title || `#${r.article_id}`)}</span>
          ${r.status === 'graded'
            ? `<b class="fce-his-score ok">${r.teacher_score}/10</b>`
            : '<b class="fce-his-score pend">待批改</b>'}
          <span class="fce-his-date">${fmtDur(r.duration_sec || 0)} · ${(r.created_at || '').slice(0, 10)}</span>
        </div>`).join('') || '<p class="reading-hint">暂无录音提交</p>'}
    </section>

    <section class="fce-group">
      <div class="fce-group-title">📝 FCE 练习${pendEssay.length ? ` · 待批作文 ${pendEssay.length}` : ''}</div>
      ${pendEssay.length ? `
        <div class="grading-action">
          <button class="reading-btn primary" data-go="essay">去批改 ${pendEssay.length} 篇作文</button>
        </div>` : ''}
      ${fceSubs.slice(0, 5).map((s) => `
        <div class="fce-his-row">
          <span class="fce-his-what">${escapeHtml(s.user)} · T${s.test_id} P${s.part}</span>
          ${s.status === 'auto'
            ? `<b class="fce-his-score">${s.auto_score}/${s.total}</b>`
            : s.status === 'graded'
              ? `<b class="fce-his-score ok">老师 ${s.teacher_score ?? '-'}</b>`
              : '<b class="fce-his-score pend">待批改</b>'}
          <span class="fce-his-date">${s.duration_sec ? fmtDur(s.duration_sec) + ' · ' : ''}${(s.created_at || '').slice(0, 10)}</span>
        </div>`).join('') || '<p class="reading-hint">暂无 FCE 练习</p>'}
    </section>

    <section class="fce-group">
      <div class="fce-group-title">📖 背单词 · 最近练习</div>
      ${recite.slice(0, 5).map((s) => `
        <div class="fce-his-row">
          <span class="fce-his-what">${escapeHtml(s.user)} · ${scopeCn(s.scope)}${s.mode === 'flip' ? '（自评）' : ''}</span>
          <b class="fce-his-score ${s.acc >= 80 ? 'ok' : ''}">${s.acc}%</b>
          <span class="fce-his-date">${s.total} 词 · 错 ${s.wrong} · ${fmtDur(s.duration_sec || 0)} · ${(s.created_at || '').slice(5, 10)}</span>
        </div>`).join('') || '<p class="reading-hint">暂无背单词记录（学生完成一组后自动上报）</p>'}
      ${recite.length ? `<p class="reading-hint">易错词（最近）：${topWrongWords(recite)}</p>` : ''}
    </section>

    <section class="fce-group">
      <div class="fce-group-title">📊 哈一作业成绩 · 最近</div>
      ${exams.slice(0, 5).map((e) => `
        <div class="fce-his-row">
          <span class="fce-his-what">第 ${e.lecture} 讲</span>
          <b class="fce-his-score ${e.score >= 90 ? 'ok' : e.score >= 70 ? '' : 'bad'}">${e.score}</b>
          <span class="fce-his-date">${e.date}${e.wrong && e.wrong.length ? ` · 错 ${e.wrong.length} 题` : ''}</span>
        </div>`).join('') || '<p class="reading-hint">暂无成绩</p>'}
      <div class="grading-action">
        <button class="reading-btn" data-go="exams">打开作业成绩（录入/编辑）</button>
      </div>
    </section>
  `

  // 跳转：阅读批改 / FCE 作文批改 / 作业成绩
  el.querySelectorAll('[data-go]').forEach((b) => {
    b.addEventListener('click', () => {
      const go = b.dataset.go
      if (go === 'reading') {
        sessionStorage.setItem('gkb-open-review', '1')
        location.hash = '#/readingAdmin'
      } else if (go === 'essay') {
        sessionStorage.setItem('gkb-open-essay', '1')
        location.hash = '#/fcePapers'
      } else if (go === 'exams') {
        location.hash = '#/exams'
      } else if (go === 'recite') {
        b.closest('.fce-group').scrollIntoView({ behavior: 'smooth' })
      }
    })
  })
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
