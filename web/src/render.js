import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

// marked 默认放行 markdown 内嵌的原始 HTML，而输入并不全部可信——
// AI 学情周报的内容由学生自由文本参与生成，可能被 prompt 注入带出
// <img onerror=...> 之类的载荷，教师打开周报即触发存储型 XSS，
// 因此所有 renderMd 产物统一过 DOMPurify 消毒后再进 innerHTML。
export function renderMd(md) {
  if (!md) return ''
  try {
    return DOMPurify.sanitize(marked.parse(md))
  } catch {
    return `<pre>${escapeHtml(md)}</pre>`
  }
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// 属性上下文转义：escapeHtml 不处理引号，attr="..." 里含 " 会截断属性甚至注入新属性
export const escAttr = (s) => escapeHtml(s).replace(/"/g, '&#34;').replace(/'/g, '&#39;')

// 本地日期 YYYY-MM-DD。new Date().toISOString() 是 UTC——中国时区每天
// 0-8 点会得到「昨天」，成绩登记/今日任务高亮全错位（时区坑统一出口）。
export function todayIso() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 计划任务完成原因（day_completion.why）。界面用它说明为什么亮，不靠打卡。 */
const TASK_WHY = {
  history: '历史快照（回填，不是当天流水）',
  goal_reached: '目标已达成',
  session_match: '阅读会话对上这本书',
  not_read: '当天没有读到这本书',
  quota_met: '背词达到当日配额',
  below_quota: '背词未达当日配额',
  signal: '学习信号已记上',
  no_signal: '还没有对应的学习信号',
  scheduled: '还没到这一天',
}

const TASK_WHY_SHORT = {
  history: '快照',
  goal_reached: '达标',
  session_match: '读过',
  not_read: '未读',
  quota_met: '配额够',
  below_quota: '未达配额',
  signal: '有记录',
  no_signal: '无记录',
  scheduled: '未到',
}

export function taskWhy(code) {
  if (!code) return ''
  return TASK_WHY[code] || String(code)
}

export function taskWhyShort(code) {
  if (!code) return ''
  return TASK_WHY_SHORT[code] || ''
}

/** 每日有效学习时长目标（分钟）：周中（周一~周五）45，周末（周六/日）90 */
export function studyGoalMin(dateIso) {
  if (!dateIso) return 45
  const dow = new Date(`${dateIso}T00:00:00`).getDay() // 0=周日
  return dow === 0 || dow === 6 ? 90 : 45
}
