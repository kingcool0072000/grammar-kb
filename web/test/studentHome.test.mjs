import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  hashAfterLogin, homeRoute, isStudentDeepLink, resolveRoute,
} from '../src/shell/catalog.js'

function useHash(hash) {
  const loc = {
    hash,
    replace(next) { this.hash = String(next) },
  }
  globalThis.location = loc
  return loc
}

test('学生登录后去课程表；教师登录不改地址', () => {
  assert.equal(homeRoute('student'), 'timetable')
  assert.equal(homeRoute('teacher'), 'prep')

  assert.equal(hashAfterLogin('student', ''), '#/timetable')
  assert.equal(hashAfterLogin('student', '#'), '#/timetable')
  assert.equal(hashAfterLogin('student', '#/'), '#/timetable')
  assert.equal(hashAfterLogin('student', '#/login'), '#/timetable')
  assert.equal(hashAfterLogin('student', '#/prep'), '#/timetable')
  assert.equal(hashAfterLogin('student', '#/reading'), '#/timetable')

  assert.equal(hashAfterLogin('student', '#/recite'), '#/recite')
  assert.equal(hashAfterLogin('student', '#/timetable'), '#/timetable')
  assert.equal(hashAfterLogin('student', '#/micro'), '#/micro')
  assert.equal(hashAfterLogin('student', '#/library'), '#/library')
  assert.equal(hashAfterLogin('student', '#/nce2?lid=12'), '#/nce2?lid=12')
  assert.equal(hashAfterLogin('student', '#/videoStudy?vid=abcdefghijk'), '#/videoStudy?vid=abcdefghijk')
  assert.equal(hashAfterLogin('student', '#/topics/lecture-30?node=k2'), '#/topics/lecture-30?node=k2')
  assert.equal(hashAfterLogin('student', '#/libraryReader/7'), '#/libraryReader/7')
  assert.equal(hashAfterLogin('student', '#/today'), '#/today')
  assert.equal(isStudentDeepLink('#/recite'), true)
  assert.equal(isStudentDeepLink(''), false)
  assert.equal(isStudentDeepLink('#/login'), false)

  assert.equal(hashAfterLogin('teacher', ''), '')
  assert.equal(hashAfterLogin('teacher', '#/'), '#/')
  assert.equal(hashAfterLogin('teacher', '#/login'), '#/login')
  assert.equal(hashAfterLogin('teacher', '#/prep'), '#/prep')
  assert.equal(hashAfterLogin('teacher', '#/plan'), '#/plan')
  assert.equal(hashAfterLogin('teacher', '#/timetable'), '#/timetable')
  assert.equal(hashAfterLogin('teacher', '#/recite'), '#/recite')
})

test('已登录时学生默认页是课程表，教师默认页仍是备课中心', () => {
  const studentHome = [
    ['', 'timetable', ''],
    ['#/', 'timetable', '#/'],
    ['#', 'timetable', '#'],
    ['#/login', 'timetable', '#/login'],
    ['#/prep', 'timetable', '#/prep'],
    ['#/no-such-page', 'timetable', '#/no-such-page'],
  ]
  for (const [hash, route, hashAfter] of studentHome) {
    const loc = useHash(hash)
    assert.equal(resolveRoute('student'), route, `student ${hash || '(empty)'}`)
    assert.equal(loc.hash, hashAfter, `student hash ${hash || '(empty)'}`)
  }

  const studentDeep = [
    ['#/timetable', 'timetable'],
    ['#/recite', 'recite'],
    ['#/micro', 'micro'],
    ['#/topics', 'topics'],
    ['#/topics/lecture-30', 'topics'],
    ['#/library', 'library'],
    ['#/libraryReader/7', 'libraryReader'],
    ['#/videoStudy?vid=abcdefghijk', 'videoStudy'],
    ['#/nce2?lid=3', 'nce2'],
    ['#/fcePapers', 'fcePapers'],
    ['#/conquered', 'conquered'],
    ['#/config', 'config'],
  ]
  for (const [hash, route] of studentDeep) {
    const loc = useHash(hash)
    assert.equal(resolveRoute('student'), route, `deep ${hash}`)
    assert.equal(loc.hash, hash, `deep hash ${hash}`)
  }

  // 旧「今日」书签仍进背单词，不改成课程表。
  const today = useHash('#/today')
  assert.equal(resolveRoute('student'), 'recite')
  assert.equal(today.hash, '#/recite')

  const teacherHome = [
    ['', 'prep', ''],
    ['#/', 'prep', '#/'],
    ['#/login', 'prep', '#/login'],
    ['#/timetable', 'prep', '#/timetable'],
    ['#/recite', 'prep', '#/recite'],
  ]
  for (const [hash, route, hashAfter] of teacherHome) {
    const loc = useHash(hash)
    assert.equal(resolveRoute('teacher'), route, `teacher ${hash || '(empty)'}`)
    assert.equal(loc.hash, hashAfter, `teacher hash ${hash || '(empty)'}`)
  }

  const teacherDeep = [
    ['#/prep', 'prep'],
    ['#/plan', 'plan'],
    ['#/plan?tab=board', 'plan'],
  ]
  for (const [hash, route] of teacherDeep) {
    const loc = useHash(hash)
    assert.equal(resolveRoute('teacher'), route, `teacher deep ${hash}`)
    assert.equal(loc.hash, hash)
  }

  // 精读旧链：学生课程表，教师备课中心。
  const readingStudent = useHash('#/reading')
  assert.equal(resolveRoute('student'), 'timetable')
  assert.equal(readingStudent.hash, '#/timetable')
  const readingTeacher = useHash('#/readingAdmin')
  assert.equal(resolveRoute('teacher'), 'prep')
  assert.equal(readingTeacher.hash, '#/prep')
})
