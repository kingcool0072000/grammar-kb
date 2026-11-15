import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mountTimetable } from '../src/views/timetable.js'

const NOTE = 'UNIQUE_NOTE_今晚把错题看完'

function pageHost() {
  return {
    innerHTML: '',
    querySelector() { return null },
    querySelectorAll() { return [] },
  }
}

test('课程表不渲染老师的话，周作业和微练习打卡还在', async () => {
  globalThis.location = {
    origin: 'http://127.0.0.1:5173',
    hash: '#/timetable',
    replace() {},
  }
  globalThis.localStorage = {
    getItem() { return null },
    setItem() {},
    removeItem() {},
  }
  globalThis.fetch = async (url) => {
    const path = String(url)
    assert.match(path, /\/plan\/my-week$/)
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      async json() {
        return {
          code: 0,
          data: {
            week_start: '2026-11-09',
            notes: `${NOTE}\n第二行也别显示`,
            tasks: { lectures: [3] },
            days: [{
              date: '2026-11-16',
              future: false,
              done: 0,
              total: 1,
              percent: 0,
              tasks: [{ type: 'lecture', text: '第3讲', key: 'lecture:3', done: false }],
              acts: {},
            }],
            completion: { done: 0, total: 1, percent: 0 },
            micro_checkins: [
              { label: '一', status: 'done', required: true },
              { label: '二', status: 'open', required: true },
            ],
            week_pool: [
              { text: '语法第3讲', key: 'lecture:3', done: false },
            ],
            goal_progress: {},
          },
        }
      },
    }
  }

  const el = pageHost()
  await mountTimetable(el)
  const html = el.innerHTML
  assert.match(html, /课程表/)
  assert.match(html, /第3讲/)
  assert.match(html, /微练习打卡/)
  assert.match(html, /本周作业/)
  assert.match(html, /语法第3讲/)
  assert.equal(html.includes('老师的话'), false)
  assert.equal(html.includes('tt-notes'), false)
  assert.equal(html.includes(NOTE), false)
  assert.equal(html.includes('第二行也别显示'), false)
})
