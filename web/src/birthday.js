// 学生生日祝福。日期与「是不是生日」只信后端 /me/birthday（北京时间），
// 不用浏览器本地日期：人在别的时区、或 UTC 零点附近，本地「今天」会偏一天。
//
// 出现方式：
// - 横幅：每次登录的学生首屏都有，关掉后本次登录不再出现（sessionStorage）。
//   再次登录会清掉关闭标记。
// - 弹层：按后端返回的北京日期，每天一次（localStorage）。
// 教师角色、非生日：什么都不挂。

const BANNER_PREFIX = 'gkb-bday-banner:'
const MODAL_PREFIX = 'gkb-bday-modal:'

let current = null

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function bannerKey(user, date) {
  return `${BANNER_PREFIX}${user}:${date}`
}

export function modalKey(user, date) {
  return `${MODAL_PREFIX}${user}:${date}`
}

function readItem(storage, key) {
  if (!storage) return null
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function writeItem(storage, key, value) {
  if (!storage) return
  try {
    storage.setItem(key, value)
  } catch { /* 隐私模式写不进时，下次进来再看一次 */ }
}

function storageKeys(storage) {
  const keys = []
  if (!storage) return keys
  try {
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i)
      if (key) keys.push(key)
    }
  } catch { /* 读不到就当没有 */ }
  return keys
}

/** 关掉横幅，只记这次登录（按后端给出的北京日期分键）。 */
export function dismissBanner(storage, user, date) {
  writeItem(storage, bannerKey(user, date), '1')
}

/** 弹层已看过，当天不再出现。 */
export function markModalSeen(storage, user, date) {
  writeItem(storage, modalKey(user, date), '1')
}

/** 登录成功时调用：同日再次登录，首屏横幅再出现。弹层「每天一次」不动。 */
export function clearBannerDismissForLogin(storage) {
  for (const key of storageKeys(storage)) {
    if (key.startsWith(BANNER_PREFIX)) {
      try { storage.removeItem(key) } catch { /* 清不掉就留着 */ }
    }
  }
}

export function rememberBirthday(payload) {
  if (payload && payload.birthday === true && payload.role !== 'teacher') {
    current = payload
    return
  }
  current = null
}

export function birthdayPayload() {
  return current
}

export function isBirthdayToday() {
  return !!(current && current.birthday === true && current.role !== 'teacher')
}

/** 完成反馈上的一句祝福。非生日返回空串，调用方不用额外判断。 */
export function finishBlessing(kind) {
  if (!isBirthdayToday()) return ''
  if (kind === 'micro') return '🎂 生日快乐！今天的微练习完成啦，爸爸为你骄傲。'
  if (kind === 'paper') return '🎂 生日快乐！这份卷子交上了，爸爸为你骄傲。'
  if (kind === 'loop') return '🎂 生日快乐！今日闭环达成，新的一岁也在认真学习。'
  return '🎂 生日快乐！爸爸为你骄傲。'
}

/**
 * 首屏要不要挂横幅 / 弹层。
 * 非生日、教师、缺用户或日期 → null（什么都不渲染）。
 */
export function greetingPlan(payload, stores = {}) {
  if (!payload || payload.birthday !== true) return null
  if (payload.role && payload.role !== 'student') return null
  const user = String(payload.user || '')
  const date = String(payload.date || '')
  if (!user || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null
  const session = stores.sessionStorage
  const local = stores.localStorage
  return {
    banner: readItem(session, bannerKey(user, date)) !== '1',
    modal: readItem(local, modalKey(user, date)) !== '1',
  }
}

export function bannerHtml(payload) {
  const title = payload.title || `${payload.user}，生日快乐！🎂`
  const message = payload.message || '新的一岁，今天也好好学。爸爸为你骄傲。'
  return `
    <div class="bday-banner-inner">
      <span class="bday-banner-mark" aria-hidden="true">🎂</span>
      <div class="bday-banner-copy">
        <b>${escapeHtml(title)}</b>
        <p>${escapeHtml(message)}</p>
      </div>
      <button type="button" class="bday-banner-x" data-bday-dismiss-banner aria-label="关闭生日祝福">×</button>
    </div>`
}

export function modalHtml(payload) {
  const title = payload.title || `${payload.user}，生日快乐！🎂`
  const message = payload.message || '新的一岁，今天也好好学。爸爸为你骄傲。'
  return `
    <div class="bday-modal-mask" data-bday-close></div>
    <div class="bday-modal-card card" role="dialog" aria-modal="true" aria-labelledby="bday-title">
      <div class="bday-modal-mark" aria-hidden="true">🎂</div>
      <h2 id="bday-title">${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
      <button type="button" class="btn-primary bday-modal-ok" data-bday-close>谢谢，开始学习</button>
    </div>`
}

export function showBirthdayToast(kind) {
  const text = finishBlessing(kind)
  if (!text || typeof document === 'undefined') return text
  const toast = document.createElement('div')
  toast.className = 'plan-toast bday-toast'
  toast.textContent = text
  document.body.appendChild(toast)
  setTimeout(() => toast.remove(), 4600)
  return text
}

/**
 * 挂到学生壳。``payload`` 来自 GET /me/birthday。
 * 非生日时清掉内存里的生日态，完成页也不会多一句。
 */
export function mountBirthdayGreeting(payload, { root, localStorage, sessionStorage } = {}) {
  const student = payload && payload.birthday === true && payload.role !== 'teacher'
  rememberBirthday(student ? payload : null)
  const plan = greetingPlan(payload, { localStorage, sessionStorage })
  if (!plan || !root) return plan || { banner: false, modal: false }

  root.querySelectorAll('.bday-banner, .bday-modal').forEach((el) => el.remove())

  if (plan.banner) {
    const bar = document.createElement('div')
    bar.className = 'bday-banner'
    bar.setAttribute('role', 'status')
    bar.innerHTML = bannerHtml(payload)
    const header = root.querySelector('.app-header')
    if (header) header.insertAdjacentElement('afterend', bar)
    else root.prepend(bar)
    const close = bar.querySelector('[data-bday-dismiss-banner]')
    if (close) {
      close.addEventListener('click', () => {
        dismissBanner(sessionStorage, payload.user, payload.date)
        bar.remove()
      })
    }
  }

  if (plan.modal) {
    markModalSeen(localStorage, payload.user, payload.date)
    const mask = document.createElement('div')
    mask.className = 'bday-modal'
    mask.innerHTML = modalHtml(payload)
    root.append(mask)
    const close = () => mask.remove()
    mask.querySelectorAll('[data-bday-close]').forEach((el) => {
      el.addEventListener('click', close)
    })
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      document.removeEventListener('keydown', onKey)
      close()
    }
    document.addEventListener('keydown', onKey)
    const ok = mask.querySelector('.bday-modal-ok')
    if (ok) ok.focus()
  }

  return plan
}
