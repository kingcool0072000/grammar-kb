import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  bannerHtml,
  bannerKey,
  clearBannerDismissForLogin,
  dismissBanner,
  finishBlessing,
  greetingPlan,
  markModalSeen,
  modalKey,
  rememberBirthday,
} from '../src/birthday.js'

function mem() {
  const data = new Map()
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
    key: (index) => [...data.keys()][index] ?? null,
    get length() { return data.size },
  }
}

const MALIN = {
  birthday: true,
  user: 'malin',
  role: 'student',
  date: '2026-11-10',
  title: 'malin，生日快乐！🎂',
  message: '新的一岁，今天也好好学。爸爸为你骄傲。',
}

test('非生日、教师、缺日期都不出现', () => {
  const stores = { sessionStorage: mem(), localStorage: mem() }
  assert.equal(greetingPlan({ ...MALIN, birthday: false }, stores), null)
  assert.equal(greetingPlan({ ...MALIN, role: 'teacher' }, stores), null)
  assert.equal(greetingPlan({ ...MALIN, date: '' }, stores), null)
  assert.equal(greetingPlan(null, stores), null)
})

test('横幅每次登录可再出现，弹层按后端日期每天一次', () => {
  const session = mem()
  const local = mem()
  const stores = { sessionStorage: session, localStorage: local }

  assert.deepEqual(greetingPlan(MALIN, stores), { banner: true, modal: true })

  dismissBanner(session, 'malin', '2026-11-10')
  markModalSeen(local, 'malin', '2026-11-10')
  assert.equal(session.getItem(bannerKey('malin', '2026-11-10')), '1')
  assert.equal(local.getItem(modalKey('malin', '2026-11-10')), '1')
  assert.deepEqual(greetingPlan(MALIN, stores), { banner: false, modal: false })

  // 再次登录只清横幅。键用的是后端给的北京日期，不是浏览器本地今天。
  clearBannerDismissForLogin(session)
  assert.deepEqual(greetingPlan(MALIN, stores), { banner: true, modal: false })

  const nextDay = { ...MALIN, date: '2026-11-11' }
  assert.deepEqual(greetingPlan(nextDay, stores), { banner: true, modal: true })
})

test('横幅文案转义，避免用户名进 HTML', () => {
  const html = bannerHtml({
    user: 'a<b',
    title: 'a<b，生日快乐！🎂',
    message: '说"加油"',
  })
  assert.match(html, /a&lt;b，生日快乐/)
  // escapeHtml（fmt.js 统一版）转义 & < >；「"」在元素文本节点里无特殊含义，
  // 不转义也安全，属性上下文由 escAttr 负责
  assert.match(html, /说"加油"/)
  assert.equal(html.includes('a<b'), false)
})

test('完成反馈只在生日态有文案', () => {
  rememberBirthday(null)
  assert.equal(finishBlessing('micro'), '')
  assert.equal(finishBlessing('paper'), '')
  assert.equal(finishBlessing('loop'), '')

  rememberBirthday(MALIN)
  assert.match(finishBlessing('micro'), /微练习/)
  assert.match(finishBlessing('paper'), /卷子/)
  assert.match(finishBlessing('loop'), /闭环/)

  rememberBirthday({ ...MALIN, role: 'teacher', birthday: true })
  assert.equal(finishBlessing('loop'), '')
  rememberBirthday(null)
})
