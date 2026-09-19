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
  // 错误体可能是网关/代理返回的非 JSON（如 502 的 HTML 页），直接 res.json() 会抛
  // 难懂的 SyntaxError——对齐 reqBlob：先判 ok，再尝试按 JSON 取 detail/message
  if (!res.ok) {
    let msg = `HTTP ${res.status} ${res.statusText} @ ${path}`
    try {
      const j = await res.json()
      msg = j.detail || j.message || msg
    } catch { /* 非 JSON 错误体，保留上面的 HTTP 消息 */ }
    throw new Error(msg)
  }
  const j = await res.json()
  if (j.code !== 0) throw new Error(j.detail || j.message || `HTTP ${res.status}`)
  return j.data
}

// 二进制（音频等）：同样带登录态，返回 Blob
async function reqBlob(path) {
  const res = await fetch(new URL(BASE + path, location.origin), { headers: authHeaders() })
  if (res.status === 401) {
    handle401()
    throw new Error('登录已过期，请重新登录')
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { msg = (await res.json()).message || msg } catch { /* 非 JSON 错误体 */ }
    throw new Error(msg)
  }
  return res.blob()
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
  // 派生文范读 WAV（二进制流，带登录态）
  readingAudioBlob: (id) => reqBlob(`/reading/audio/${id}`),
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
  // 泛读馆：书架 / 阅读器 / 预习 / 设置 / 查词（library.db）
  libraryBooks: () => req('/library/books'),
  libraryUpload: (file) => {
    const fd = new FormData()
    fd.append('file', file, file.name)
    return reqUpload('/library/books', fd)
  },
  libraryFileBlob: (id) => reqBlob(`/library/books/${id}/file`),
  libraryCoverBlob: (id) => reqBlob(`/library/books/${id}/cover`),
  libraryChapters: (bookId) => req(`/library/books/${bookId}/chapters`),
  libraryDeleteBook: (id) => reqJson(`/library/books/${id}`, 'DELETE'),
  librarySkipChapter: (bookId, idx, skip) =>
    reqJson(`/library/books/${bookId}/chapters/${idx}/skip`, 'PUT', { skip }),
  libraryOpenBook: (bookId) => reqJson(`/library/books/${bookId}/open`, 'POST'),
  libraryGetProgress: (bookId) => req(`/library/books/${bookId}/progress`),
  libraryPutProgress: (bookId, rec) =>
    reqJson(`/library/books/${bookId}/progress`, 'PUT', rec),
  libraryPrep: (bookId, chapterIndex) =>
    req(`/library/prep/${bookId}/${chapterIndex}`),
  libraryPrepBatch: (bookId, chapterIndexes) =>
    reqJson('/library/prep/batch', 'POST', { bookId, chapterIndexes }),
  libraryPrepBatchStatus: (jobId) => req(`/library/prep/batch/${jobId}`),
  libraryPrepSingle: (bookId, chapterIndex) =>
    reqJson('/library/prep/single', 'POST', { bookId, chapterIndex }),
  librarySettings: () => req('/library/settings'),
  librarySaveSettings: (patch) => reqJson('/library/settings', 'PUT', patch),
  libraryModels: (key) => req('/library/models', { searchParams: { key } }),
  libraryTestAi: (apiKey) => reqJson('/library/ai/test', 'POST', { apiKey }),
  libraryDict: (word, context) =>
    req(`/library/dict/${encodeURIComponent(word)}`, { searchParams: { context } }),
  // 学情分析 · AI 周报（手动触发上一自然周，教师专属；模型配置复用泛读馆设置）
  analyticsAiTrigger: () => reqJson('/analytics/ai/weekly', 'POST', {}),
  analyticsAiReports: ({ limit = 12 } = {}) =>
    req('/analytics/ai/reports', { searchParams: { limit } }),
  // 专注力（泛读馆阅读器行为采集；学生上报，教师批改中心查看）
  focusSubmit: (rec) => reqJson('/focus/sessions', 'POST', rec),
  focusSessions: ({ user, limit = 50 } = {}) =>
    req('/focus/sessions', { searchParams: { user, limit } }),
  focusSession: (id) => req(`/focus/sessions/${id}`),
  // 专题学习（学生自学手册进度；谁登录记谁的行，跨设备同步）
  topicsProgress: () => req('/topics/progress'),
  topicProgress: (topicId) => req(`/topics/${encodeURIComponent(topicId)}/progress`),
  topicPutProgress: (topicId, doneKeys, assignedUser = '') =>
    reqJson(`/topics/${encodeURIComponent(topicId)}/progress`, 'PUT', {
      done_keys: doneKeys,
      assigned_user: assignedUser,
    }),
}

// multipart 上传（epub 等大文件）：带登录态，返回解包后的 data
async function reqUpload(path, formData) {
  const res = await fetch(new URL(BASE + path, location.origin), {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  })
  if (res.status === 401) {
    handle401()
    throw new Error('登录已过期，请重新登录')
  }
  const j = await res.json()
  if (!res.ok || j.code !== 0) throw new Error(j.detail || j.message || `HTTP ${res.status}`)
  return j.data
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
