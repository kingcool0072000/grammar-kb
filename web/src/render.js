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
