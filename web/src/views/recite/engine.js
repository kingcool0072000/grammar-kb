import { api } from '../../api.js'
import { escapeHtml } from '../../render.js'
import { fmtDuration } from '../../fmt.js'
import { stopAtlasAudio, speakWord } from '../../atlas.js'
import { renderCard } from './cards.js'
import { ttsOn } from './shared.js'

// 背单词会话状态机（自 views/recite.js 原样搬移）：词池组装（shuffle）/
// 题型队列（makeQuestion、buildQuiz，错词答错后间隔 3 题重现）/ 逐题推进
// 与翻面卡片衔接（startSession + renderCard）/ 结算与上传（finish、
// uploadSession、sessionActive 防抖守卫）/ 结算页（mountResult）/
// 旧本地进度一次性迁移上云（migrateLegacyProgress）。

export function shuffle(arr) {
  const a = arr.slice()
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

function entryMeaning(e) {
  return (
    (e.gloss_lines && e.gloss_lines[0] && e.gloss_lines[0].text) ||
    e.gloss ||
    (e.meanings || [])[0] ||
    '—'
  )
}

/** 拼写挖空的遮罩：遮约 40% 字母（≥1 个），非字母字符（- ' 空格）保留。 */
function maskWord(word) {
  const w = (word || '').toLowerCase()
  const letters = [...w].map((c, i) => (/[a-z]/.test(c) ? i : -1)).filter((i) => i >= 0)
  if (!letters.length) return w
  const maskN = Math.max(1, Math.round(letters.length * 0.4))
  const pick = new Set(shuffle(letters).slice(0, maskN))
  return [...w].map((c, i) => (pick.has(i) ? '_' : c)).join('')
}

/**
 * 生成一道题。
 * - 'zh2en'  中译英（看中文释义，拼出单词）
 * - 'en2zh'  英译中（认词）
 * - 'cloze'  拼写挖空（缺字母补全）
 */
function makeQuestion(e) {
  const answer = e.display || e.word
  const roll = Math.random()
  if (roll < 0.5) return { type: 'zh2en', entry: e, answer, meaning: entryMeaning(e) }
  if (roll < 0.75) return { type: 'en2zh', entry: e, answer: '', meaning: entryMeaning(e) }
  return { type: 'cloze', entry: e, answer, masked: maskWord(answer), meaning: entryMeaning(e) }
}

function buildQuiz(pool, size) {
  return shuffle(pool).slice(0, size).map((e) => makeQuestion(e))
}

// ---- 判定 ----------------------------------------------------------------- //

function eqIgnoringCase(a, b) {
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}

/** 逐字符比对：返回 HTML（错的标红，缺的补位，多的截断）。 */
function diffHtml(answer, input) {
  const a = answer.toLowerCase()
  const b = input.toLowerCase()
  let out = ''
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (i >= b.length) {
      out += `<span class="diff-ch missing">${escapeHtml(a[i])}</span>`
    } else if (i >= a.length || b[i] !== a[i]) {
      out += `<span class="diff-ch wrong">${escapeHtml(b[i])}</span>`
    } else {
      out += `<span class="diff-ch">${escapeHtml(a[i])}</span>`
    }
  }
  return out
}

// ---- 进度（云端 recite_word_progress；本地仅遗留数据一次性迁移）---------- //

const LS_KEY = 'gkb-recite-progress-v1' // 旧版遗留，迁移后清除
const LS_SESSIONS = 'gkb-recite-sessions-v1' // 旧版遗留，迁移后清除（历史已上云）

/**
 * 旧本地进度一次性合并上云（max 语义幂等），成功后清掉遗留 key。
 * 返回是否执行了迁移（用于提示）；失败静默（下次进来再试）。
 */
export async function migrateLegacyProgress() {
  let legacy = null
  try {
    legacy = JSON.parse(localStorage.getItem(LS_KEY))
  } catch {
    legacy = null
  }
  if (!legacy || typeof legacy !== 'object' || !Object.keys(legacy).length) {
    // 无逐词遗留也清掉旧 sessions key（数据早已在云端）
    try {
      localStorage.removeItem(LS_SESSIONS)
    } catch { /* 忽略 */ }
    return false
  }
  try {
    await api.reciteProgressSync(legacy)
    localStorage.removeItem(LS_KEY)
    localStorage.removeItem(LS_SESSIONS)
    return true
  } catch {
    return false // 离线/接口异常：保留本地数据，下次再迁移
  }
}

// ---- 全屏会话 -------------------------------------------------------------- //

// 会话守卫：同一时间只允许一个全屏背词会话。曾因「到期」卡连点每击各开
// 一个 overlay，旧会话计时器仍在走、逐题结算各发一条 recite_session，
// 10-31 一晚刷出 49 条假会话（每条还各推一次艾宾浩斯节点）。所有入口
// （开始背单词 / 到期 / 逾期 / 再来一组）都经 startSession 收口。
let sessionActive = false

export function startSession(rootEl, { pool, size, mode, scope = '', onFinish }) {
  if (sessionActive) return
  sessionActive = true
  const releaseSession = () => { sessionActive = false }
  // 播放队列：初始题 + 错词重现（答错后间隔 3 题再插一次）
  let queue = buildQuiz(pool, size)
  let done = 0
  const wrongEntries = []
  const details = [] // 逐词对错明细（首答），随组上报云端逐词进度
  const totalFirst = queue.length
  let idx = 0
  let overlay = null
  let finishing = false // 末题连点守卫：finish 只结算一次
  const t0 = Date.now() // 本组计时（结算页与累计时长都用）

  function currentQ() {
    return queue[idx]
  }

  function requeue(entry) {
    const at = Math.min(idx + 4, queue.length) // 间隔 3 题重现
    queue.splice(at, 0, makeQuestion(entry))
  }

  async function finish() {
    if (finishing) return
    finishing = true
    const total = done
    // 注意：wrongEntries 只记首答错；重现答对不影响它
    const uniqWrong = [...new Set(wrongEntries.map((e) => e.word))]
    const sec = Math.round((Date.now() - t0) / 1000)
    // 先上报再刷新摘要，新词额度才对得上这一组
    try {
      await uploadSession(total, uniqWrong, sec, details)
    } catch { /* 离线/未登录时静默丢弃 */ }
    releaseSession()
    overlay.remove()
    overlay = null
    mountResult(rootEl, {
      total,
      uniqWrong,
      acc: Math.round(((total - uniqWrong.length) / total) * 100),
      duration: sec,
      pool,
      size,
      mode,
      scope,
    })
    if (onFinish) onFinish()
  }

  function uploadSession(total, uniqWrong, sec, wordDetails) {
    return import('../../api.js').then(({ api }) => api.reciteSubmit({
      total,
      wrong: uniqWrong.length,
      acc: Math.round(((total - uniqWrong.length) / total) * 100),
      duration_sec: sec,
      wrong_words: uniqWrong,
      mode,
      scope,
      details: wordDetails,
    }))
  }

  overlay = document.createElement('div')
  overlay.className = 'recite-overlay'
  document.body.appendChild(overlay)

  // 卡片切换/退出前，清掉挂到 document 上的键盘监听
  const cleanupKeys = () => {
    if (overlay.__removeKey) {
      overlay.__removeKey()
      overlay.__removeKey = null
    }
  }

  function renderQ() {
    stopAtlasAudio()
    const q = currentQ()
    cleanupKeys()
    if (!q) return finish()
    renderCard(overlay, {
      q,
      idx: done + 1,
      total: totalFirst + (queue.length - totalFirst),
      mode,
      onAnswer(correct) {
        // 首答逐词明细（重现答题不计）：云端累计 right/wrong
        const w = q.entry.word
        if (!details.some((d) => d.word === w)) details.push({ word: w, correct })
        done += 1
        if (!correct) {
          if (!wrongEntries.some((e) => e.word === q.entry.word)) wrongEntries.push(q.entry)
          requeue(q.entry)
        }
        idx += 1
        renderQ()
      },
      onQuit() {
        cleanupKeys()
        overlay.remove()
        overlay = null
        releaseSession()
        // 中途退出：已作答部分照常上报（答对计云端进度、答错进艾宾浩斯
        // 周期、按已答题数记一组部分成绩），避免白背。
        if (done > 0) {
          const uniqWrong = [...new Set(wrongEntries.map((e) => e.word))]
          uploadSession(done, uniqWrong, Math.round((Date.now() - t0) / 1000), details)
          if (onFinish) onFinish()
        }
      },
    })
  }

  renderQ()
}

// ---- 结算页 ---------------------------------------------------------------- //

function mountResult(el, { total, uniqWrong, acc, duration, pool, size, mode, scope = '', onFinish }) {
  const wrap = document.createElement('div')
  wrap.className = 'recite-result card'
  wrap.innerHTML = `
    <h2>本组完成 🎉</h2>
    <p>共 ${total} 题 · 首答正确率 <b>${acc}%</b> · 用时 ${fmtDuration(duration)}</p>
    ${uniqWrong.length ? `<h3>错词（${uniqWrong.length}）· 点击发音</h3><p>${uniqWrong.map((w) => `<span class="vocab-form rc-say" data-say="${escapeHtml(w)}">${escapeHtml(w)}</span>`).join(' ')}</p>` : '<p>全对，太棒了！</p>'}
    <div class="chip-row">
      <button class="btn-primary" id="rr-again">再背一组</button>
      ${uniqWrong.length ? '<button class="btn-primary ghost" id="rr-wrong">只练错词</button>' : ''}
    </div>
  `
  el.appendChild(wrap)
  wrap.querySelectorAll('.rc-say').forEach((s) =>
    s.addEventListener('click', () => { if (ttsOn()) speakWord(s.dataset.say) }),
  )
  wrap.querySelector('#rr-again').addEventListener('click', () => {
    wrap.remove()
    startSession(el, { pool, size, mode, scope, onFinish })
  })
  const $wrong = wrap.querySelector('#rr-wrong')
  if ($wrong)
    $wrong.addEventListener('click', () => {
      const entries = pool.filter((e) => uniqWrong.includes(e.word))
      wrap.remove()
      startSession(el, { pool: entries, size: Math.min(size, entries.length), mode, scope, onFinish })
    })
  wrap.scrollIntoView({ behavior: 'smooth' })
}
