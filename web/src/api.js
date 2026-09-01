// grammar-kb 后端 API 封装。
// 开发期 /api 由 vite.config.js 代理到 http://127.0.0.1:8000
const BASE = '/api'

// ---- 登录态（学生/教师角色，token 存 localStorage）----
const LS_AUTH = 'gkb-auth-v1'

export function getAuth() {
  try {
    return JSON.parse(localStorage.getItem(LS_AUTH)) || null
  } catch {
    return null
  }
}

export function setAuth(auth) {
  if (auth) localStorage.setItem(LS_AUTH, JSON.stringify(auth))
  else localStorage.removeItem(LS_AUTH)
}

function authHeaders() {
  const a = getAuth()
  return a && a.token ? { Authorization: `Bearer ${a.token}` } : {}
}

// 401 时清掉本地登录态并回到登录页（token 过期/被重置）
function handle401() {
  setAuth(null)
  if (!location.hash.startsWith('#/login')) location.hash = '/login'
}

// 后端统一返回 { code, message, data }，这里解包出 data
async function req(path, { searchParams } = {}) {
  const url = new URL(BASE + path, location.origin)
  if (searchParams) {
    for (const [k, v] of Object.entries(searchParams)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    }
  }
  const res = await fetch(url, { headers: authHeaders() })
  if (res.status === 401) {
    handle401()
    throw new Error('登录已过期，请重新登录')
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} @ ${path}`)
  const json = await res.json()
  if (json && typeof json === 'object' && 'code' in json) {
    if (json.code !== 0) throw new Error(json.message || `API code ${json.code}`)
    return json.data
  }
  return json
}

async function reqJson(path, method, body) {
  const res = await fetch(new URL(BASE + path, location.origin), {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.status === 401) {
    handle401()
    throw new Error('登录已过期，请重新登录')
  }
  const j = await res.json()
  if (!res.ok || j.code !== 0) throw new Error(j.detail || j.message || `HTTP ${res.status}`)
  return j.data
}

export const api = {
  login: (user, password) => reqJson('/auth/login', 'POST', { user, password }),
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
  // 哈一作业成绩（后端 exam.db 持久化；学生只可提交，管理需教师）
  examsList: () => req('/exams'),
  examsAdd: (rec) => reqJson('/exams', 'POST', rec),
  examsUpdate: (id, rec) => reqJson(`/exams/${id}`, 'PUT', rec),
  examsDelete: (id) => reqJson(`/exams/${id}`, 'DELETE'),
  // FCE 真题（后端 data/fce.db 只读；练习提交与批改可写）
  fcePapers: () => req('/fce-papers'),
  fcePaper: (testId) => req(`/fce-papers/${testId}`),
  fceSubmissions: ({ user, status, limit = 100 } = {}) =>
    req('/fce-submissions', { searchParams: { user, status, limit } }),
  fceSubmit: (rec) => reqJson('/fce-submissions', 'POST', rec),
  fceGrade: (id, rec) => reqJson(`/fce-submissions/${id}`, 'PUT', rec),
  fceSubmission: (id) => req(`/fce-submissions/${id}`),
  fceDeleteSubmission: (id) => reqJson(`/fce-submissions/${id}`, 'DELETE'),
  // 阅读训练：列表（默认只返回派生文；教师 kind=base 查原文段）/ 详情 / 录音提交与批改
  readingArticles: ({ kind } = {}) =>
    req('/reading/articles', { searchParams: { kind } }),
  readingArticle: (id) => req(`/reading/articles/${id}`),
  readingAddDerived: (rec) => reqJson('/reading/articles', 'POST', rec),
  readingUpdateDerived: (id, rec) => reqJson(`/reading/articles/${id}`, 'PUT', rec),
  readingDeleteDerived: (id) => reqJson(`/reading/articles/${id}`, 'DELETE'),
  readingRecordings: ({ user, status, limit = 100 } = {}) =>
    req('/reading/recordings', { searchParams: { user, status, limit } }),
  readingRecording: (id) => req(`/reading/recordings/${id}`),
  readingSubmitRecording: (rec) => reqJson('/reading/recordings', 'POST', rec),
  readingGradeRecording: (id, rec) => reqJson(`/reading/recordings/${id}`, 'PUT', rec),
  readingDeleteRecording: (id) => reqJson(`/reading/recordings/${id}`, 'DELETE'),
  // 背单词成绩上报（学生自动上报；教师批改中心查看）
  reciteSubmit: (rec) => reqJson('/recite/sessions', 'POST', rec),
  reciteSessions: ({ user, limit = 100 } = {}) =>
    req('/recite/sessions', { searchParams: { user, limit } }),
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
