import { escapeHtml } from '../../render.js'
import { fmtDur } from '../../fmt.js'

// 批改中心多子 Tab 共用的帮助函数与行渲染：看板/学习日志/专注详情弹窗都引用，
// 只放纯函数，不绑定单个子 Tab。

// 专注会话标题：泛读 = 「泛读-书名-章节」；精读/专题=内容名（后端已按
// book_id 补书名，章节用 range_label）
export function focusTitle(s) {
  if (s.module === 'reading') return `精读-${s.book_title || ''}`
  if (s.module === 'topics') return `专题-${s.book_title || ''}`
  const title = (s.book_title || '').trim() || (s.book_id != null ? `#${s.book_id}` : '')
  const label = (s.range_label || '').trim()
  if (title && label) return `泛读-${title}-${label}`
  return title ? `泛读-${title}` : '泛读'
}

/** 位置区间文案（统计口径=当前阅读章节的章内百分比）：
 * 单章 →「第N章 X% → Y%」；跨章 →「第N章 X% → 第M章 Z%」；
 * 后端缺章节数据时才回落全书 %。 */
export function posRangeText(rw) {
  const chs = Array.isArray(rw.chapters) ? rw.chapters : []
  if (chs.length === 1) {
    const c = chs[0]
    return `第${c.ch}章 ${c.s_in}% → ${c.e_in}%`
  }
  if (chs.length > 1) {
    const a = chs[0]
    const z = chs[chs.length - 1]
    return `第${a.ch}章 ${a.s_in}% → 第${z.ch}章 ${z.e_in}%`
  }
  return `${rw.s_pct}% → ${rw.e_pct}%`
}

// 学习日志-泛读/精读/专题会话行（带学生名；泛读附精确词数+位置区间+
// 与上会话衔接/异常标记，rw 来自后端 /focus/words）
export function focusRow(s) {
  const score = typeof s.score === 'number' ? Math.round(s.score) : null
  const scoreCls = score === null ? 'pend' : score >= 80 ? 'ok' : score >= 60 ? '' : 'bad'
  const mod = s.module === 'reading' ? '精读' : s.module === 'topics' ? '专题' : '泛读'
  const modCls = s.module === 'reading' ? 'gd-focus-mod reading' : 'gd-focus-mod'
  const rw = s.rw
  const linkChip = rw
    ? (rw.link === 'next' ? '<i class="gd-focus-chip ok">↪ 衔接</i>'
      : rw.link === 'first' ? '<i class="gd-focus-chip">首读</i>'
        : rw.link_note ? `<i class="gd-focus-chip warn">⚠ ${escapeHtml(rw.link_note)}</i>` : '')
    : ''
  const chips = [
    `<i class="${modCls}">${mod}</i>`,
    ...(rw
      ? [
          // words=本次新学词数（基线=该书历史有效阅读最远水位）；
          // 重合=水位以下已读过部分，只展示不重复计数
          ...(rw.words > 0 || !(rw.overlap > 0) ? [`<i class="gd-focus-chip">${rw.words} 词</i>`] : []),
          ...(rw.overlap > 0 ? [`<i class="gd-focus-chip dup">重合 ${rw.overlap} 词</i>`] : []),
          `<i class="gd-focus-chip">${posRangeText(rw)}</i>`,
          linkChip,
          ...(rw.flags || []).map((f) => `<i class="gd-focus-chip warn">⚠ ${escapeHtml(f)}</i>`),
        ]
      : []),
    `<i class="gd-focus-chip">查词 ${s.lookups || 0}</i>`,
    `<i class="gd-focus-chip">发音 ${s.plays || 0}</i>`,
    ...(s.away_count ? [`<i class="gd-focus-chip">离开 ${s.away_count}</i>`] : []),
    ...(s.fast_scroll_flags ? [`<i class="gd-focus-chip">快滚 ${s.fast_scroll_flags}</i>`] : []),
  ].join('')
  return `
    <div class="fce-his-row gd-focus-row" data-focus-id="${s.id}" title="点开专注详情">
      <span class="fce-his-what">${escapeHtml(s.user || '')} · ${escapeHtml(focusTitle(s))}</span>
      <b class="fce-his-score ${scoreCls}">${score === null ? '—' : score}</b>
      <span class="fce-his-date">${fmtDur(s.total_sec || 0)} · ${fmtCnTime(s.started_at || s.created_at)}</span>
      <span class="gd-focus-chips">${chips}</span>
    </div>`
}

/** ISO 时间（UTC 带 Z / 本地串）→ 北京时间「MM-DD HH:mm」。
 *  采集端 isoSec() 生成 UTC 字符串，直接 slice 会早 8 小时——必须经 Date 转换。
 *  无 Z 后缀的裸串（历史数据/本地时钟）按已是中国时间直解。 */
export function fmtCnTime(iso, withDate = true) {
  if (!iso) return '—'
  const raw = String(iso)
  const d = new Date(/[Z+]/.test(raw.slice(-6)) ? raw : raw + '+08:00')
  if (isNaN(d.getTime())) return raw.slice(0, 16).replace('T', ' ')
  const p = (n) => String(n).padStart(2, '0')
  const hm = `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  if (!withDate) return hm.slice(6)
  // 非当年补年份：跨年日志混排时「09-03」排「10-06」上面会像排序坏了
  const yr = d.getFullYear() !== new Date().getFullYear() ? `${d.getFullYear()}-` : ''
  return yr + hm
}
