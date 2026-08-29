// grammar-kb 后端 API 封装。
// 开发期 /api 由 vite.config.js 代理到 http://127.0.0.1:8000
const BASE = '/api'

// 后端统一返回 { code, message, data }，这里解包出 data
async function req(path, { searchParams } = {}) {
  const url = new URL(BASE + path, location.origin)
  if (searchParams) {
    for (const [k, v] of Object.entries(searchParams)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    }
  }
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} @ ${path}`)
  const json = await res.json()
  if (json && typeof json === 'object' && 'code' in json) {
    if (json.code !== 0) throw new Error(json.message || `API code ${json.code}`)
    return json.data
  }
  return json
}

export const api = {
  stats: () => req('/stats'),
  lectures: () => req('/lectures'),
  lecture: (number, format = 'markdown') =>
    req(`/lectures/${number}`, { searchParams: { format } }),
  kp: (id, format = 'markdown') =>
    req(`/kp/${id}`, { searchParams: { format } }),
  search: (q, { category, limit = 20 } = {}) =>
    req('/search', { searchParams: { q, category, limit } }),
  markers: ({ category, tense } = {}) =>
    req('/markers', { searchParams: { category, tense } }),
  relation: (type) => req('/relation', { searchParams: { type } }),
  vocabulary: ({ limit = 2000, min_freq = 2 } = {}) =>
    req('/vocabulary', { searchParams: { limit, min_freq } }),
  // 全量词典（ECDICT）：任意单词可查，不限于讲义语料
  dict: (word) => req(`/dict/${encodeURIComponent(word)}`),
  taxonomy: () => req('/taxonomy'),
  // 作业卷题干（后端 grammar.db homework_question 表）
  homework: (lecture) => req(`/homework/${lecture}`),
  homeworkBatch: (lectures) =>
    req('/homework', { searchParams: { lectures: lectures.join(',') } }),
  // 作业成绩（后端 exam.db 持久化）
  examsList: () => req('/exams'),
  examsAdd: (rec) =>
    fetch(new URL(BASE + '/exams', location.origin), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rec),
    }).then(async (r) => {
      const j = await r.json()
      if (!r.ok || j.code !== 0) throw new Error(j.detail || j.message || `HTTP ${r.status}`)
      return j.data
    }),
  examsUpdate: (id, rec) =>
    fetch(new URL(`${BASE}/exams/${id}`, location.origin), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rec),
    }).then(async (r) => {
      const j = await r.json()
      if (!r.ok || j.code !== 0) throw new Error(j.detail || j.message || `HTTP ${r.status}`)
      return j.data
    }),
  examsDelete: (id) =>
    fetch(new URL(`${BASE}/exams/${id}`, location.origin), { method: 'DELETE' }).then(
      async (r) => {
        const j = await r.json()
        if (!r.ok || j.code !== 0) throw new Error(j.detail || j.message || `HTTP ${r.status}`)
        return j.data
      },
    ),
}

// 规整单个知识点，保证集合字段为数组
function normalizePoint(p) {
  return {
    id: p.id,
    title: p.title || '（未命名）',
    lectureNumber: p.lecture_number,
    category: p.category || '其它',
    sectionPath: p.section_path || '',
    bodyMd: p.body_md || '',
    examplesMd: p.examples_md || '',
    tableMd: p.table_md || '',
    tableData: p.table_data || null,
    isTable: !!p.is_table,
    markers: Array.isArray(p.markers) ? p.markers : [],
    relations: Array.isArray(p.relations) ? p.relations : [],
    tags: Array.isArray(p.tags) ? p.tags : [],
    sourcePage: p.source_page ?? null,
    ord: p.ord ?? 0,
  }
}

// 后端没有「列出全部知识点」端点，search 是关键词检索。
// 用一组几乎必现的高频字去检索并按 id 去重，可覆盖绝大多数知识点。
// （实测可取回 95%+；剩余少量纯英文知识点可后续给后端加列表端点补齐。）
const PROBE_WORDS = ['的', '是', '时', '句', '词', '语态', '从句', '时态', '用法']

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export async function fetchAllPoints(onProgress) {
  // 后端（uvicorn 单 worker + sqlite）扛不住并发检索，
  // 多个 /search 同时到达会大面积 500。因此这里串行发起，
  // 每个词失败再退避重试一次，整体 ~0.5s 即可取回绝大多数知识点。
  const words = [...PROBE_WORDS]
  const map = new Map()
  const absorb = (items) => {
    if (!Array.isArray(items)) return
    for (const it of items) {
      const p = normalizePoint(it)
      map.set(p.id, { ...(map.get(p.id) || {}), ...p })
    }
  }

  for (let i = 0; i < words.length; i++) {
    let items = null
    try {
      items = (await api.search(words[i], { limit: 500 }))?.items
    } catch {
      await sleep(150)
      try {
        items = (await api.search(words[i], { limit: 500 }))?.items
      } catch {
        /* 单个词失败不影响其余 */
      }
    }
    if (onProgress) onProgress(i + 1, words.length)
    absorb(items)
  }

  if (map.size === 0) {
    throw new Error('知识点检索全部失败，请确认后端 /search 可用')
  }
  return [...map.values()].sort(
    (a, b) => a.lectureNumber - b.lectureNumber || a.ord - b.ord,
  )
}
