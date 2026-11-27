import { escapeHtml } from '../../render.js'

const WEEK_CN = ['一', '二', '三', '四', '五', '六', '日']

function shortDate(iso) {
  const s = String(iso || '')
  if (s.length < 10) return s
  const m = Number(s.slice(5, 7))
  const d = Number(s.slice(8, 10))
  return Number.isFinite(m) && Number.isFinite(d) ? `${m}/${d}` : s.slice(5)
}

/**
 * 渲染未来 14 天（今天起 2 周）背词与艾宾浩斯复习日历 HTML。
 * 数据来源：/recite/review-summary 返回的 calendar_14d。
 */
export function vocabCalendarHtml(summary, { title = '📅 近两周背词与复习', compact = false, clickableToday = false } = {}) {
  const days = (summary && Array.isArray(summary.calendar_14d)) ? summary.calendar_14d : []
  if (!days.length) return ''
  const revSum = days.reduce((acc, d) => acc + (Number(d.review) || 0), 0)
  const hasUnplanned = days.some((d) => d.new_words == null)
  const metaBits = [
    `14 天复习 ${revSum} 词`,
    hasUnplanned ? '「—」为该周未排新词计划' : '新词按周计划日均摊派',
  ]
  const cells = days.map((d) => {
    const rev = Number(d.review) || 0
    const over = Number(d.overdue) || 0
    const due = Number(d.review_due) || 0
    const nw = d.new_words == null ? null : Number(d.new_words)
    const wd = WEEK_CN[Number(d.weekday)] || ''
    const isToday = !!d.is_today
    const isWeekend = d.weekday === 5 || d.weekday === 6
    const canClick = clickableToday && isToday && rev > 0
    const tag = canClick ? 'button' : 'div'
    const attrs = canClick ? ' type="button" data-cal-today="1"' : ''
    const revTitle = isToday && over > 0
      ? `今日到期 ${due} 词 + 逾期 ${over} 词（点击开始复习）`
      : (rev > 0 ? `艾宾浩斯到期复习 ${rev} 词` : '当日无到期复习词')
    const nwTitle = nw == null ? '该周尚未配置背词目标' : `计划新背 ${nw} 词`
    const cls = [
      'rc-cal-cell',
      isToday ? 'today' : '',
      isWeekend ? 'wknd' : '',
      over > 0 ? 'has-over' : (rev > 0 ? 'has-rev' : ''),
      canClick ? 'clickable' : '',
    ].filter(Boolean).join(' ')
    return `
      <${tag} class="${cls}"${attrs}>
        <div class="rc-cal-top">
          <span class="rc-cal-date">${escapeHtml(shortDate(d.date))}</span>
          <span class="rc-cal-wd">${isToday ? '<b>今</b>' : `周${wd}`}</span>
        </div>
        <div class="rc-cal-nums">
          <span class="rc-cal-row new ${nw == null ? 'na' : (nw > 0 ? 'on' : '')}" title="${escapeHtml(nwTitle)}">
            <i>新</i><b>${nw == null ? '—' : nw}</b>
          </span>
          <span class="rc-cal-row rev ${over > 0 ? 'over' : (rev > 0 ? 'on' : '')}" title="${escapeHtml(revTitle)}">
            <i>复</i><b>${rev}</b>
          </span>
        </div>
      </${tag}>`
  }).join('')
  return `
    <section class="rc-cal ${compact ? 'compact' : ''}">
      <div class="rc-cal-head">
        <span class="rc-cal-title">${escapeHtml(title)}</span>
        <span class="rc-cal-meta">${escapeHtml(metaBits.join(' · '))}</span>
      </div>
      <div class="rc-cal-grid">${cells}</div>
    </section>`
}

/**
 * 挂载 2 周背词与复习日历到指定容器。
 */
export function mountVocabCalendar(host, summary, { title, compact = false, onTodayReview } = {}) {
  if (!host) return
  const clickableToday = typeof onTodayReview === 'function'
  host.innerHTML = vocabCalendarHtml(summary, { title, compact, clickableToday })
  if (clickableToday) {
    const btn = host.querySelector('[data-cal-today="1"]')
    if (btn) btn.addEventListener('click', () => onTodayReview())
  }
}
