import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

// 内容来自自有后端的题库 markdown，可信。如未来接入外部内容，需在此加 sanitize。
export function renderMd(md) {
  if (!md) return ''
  try {
    return marked.parse(md)
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
