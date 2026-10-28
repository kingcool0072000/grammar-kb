import { routeSegs } from './dom.js'
import { mountCourses } from '../views/courses.js'
import { mountVocabulary } from '../views/vocabulary.js'
import { mountRecite } from '../views/recite.js'
import { mountTaxonomy } from '../views/taxonomy.js'
import { mountExams } from '../views/exams.js'
import { mountFce } from '../views/fce.js'
import { mountFcePapers } from '../views/fcePapers.js'
import { mountReading } from '../views/reading.js'
import { mountReadingAdmin } from '../views/readingAdmin.js'
import { mountReadingList } from '../views/readingList.js'
import { mountFceListen } from '../views/fceListen.js'
import { mountTimetable } from '../views/timetable.js'
import { mountConquered } from '../views/conquered.js'
import { mountLibraryShelf } from '../views/library/shelf.js'
import { mountLibraryReader } from '../views/library/reader.js'
import { mountLibraryManage } from '../views/library/manage.js'
import { mountLibrarySettings } from '../views/library/settings.js'
import { mountGrading } from '../views/grading.js'
import { mountPrep } from '../views/prep.js'
import { mountPlan } from '../views/plan.js'
import { mountStudents } from '../views/students.js'
import { mountPaperAdmin } from '../views/paperAdmin.js'
import { mountVocabLevel } from '../views/vocabLevel.js'
import { mountConfig } from '../views/config.js'
import { mountVocabExam } from '../views/vocabExam.js'

/** 按路由挂视图。教师壳 / 学生壳共用这一处，页签各自维护。 */
export function mountView(route, container, { role, state, ctx, pointsById }) {
  if (route === 'grading') return mountGrading(container)
  if (route === 'prep') return mountPrep(container, ctx)
  if (route === 'plan') return mountPlan(container)
  if (route === 'courses') {
    return mountCourses(container, { lectures: state.lectures, openLecture: ctx.openLecture })
  }
  if (route === 'vocab') {
    return mountVocabulary(container, { vocab: state.vocab, openWord: (e) => ctx.openWord(e) })
  }
  if (route === 'recite') return mountRecite(container, { vocab: state.vocab, role })
  if (route === 'timetable') return mountTimetable(container)
  if (route === 'conquered') return mountConquered(container)
  if (route === 'topics') {
    return import('../views/topics.js').then(({ mountTopics }) =>
      mountTopics(container, { role }))
  }
  if (route === 'topicsAdmin') {
    return import('../views/topicsAdmin.js').then(({ mountTopicsAdmin }) =>
      mountTopicsAdmin(container))
  }
  if (route === 'config') return mountConfig(container)
  if (route === 'taxonomy') {
    return mountTaxonomy(container, { pointsById, openKp: ctx.openKp })
  }
  if (route === 'fce') return mountFce(container)
  if (route === 'fcePapers') return mountFcePapers(container, { role })
  if (route === 'fceListen') return mountFceListen(container)
  if (route === 'readingList') return mountReadingList(container)
  if (route === 'readingAdmin') return mountReadingAdmin(container)
  if (route === 'reading') return mountReading(container, { role })
  if (route === 'videoAdmin') {
    return import('../views/videoAdmin.js').then(({ mountVideoAdmin }) =>
      mountVideoAdmin(container))
  }
  if (route === 'videoStudy') {
    return import('../views/videoStudy.js').then(({ mountVideoStudy }) =>
      mountVideoStudy(container, { role }))
  }
  if (route === 'library') return mountLibraryShelf(container, { role })
  if (route === 'libraryReader') {
    return mountLibraryReader(container, { role, bookId: Number(routeSegs()[1]) })
  }
  if (route === 'libraryManage') {
    return mountLibraryManage(container, { bookId: Number(routeSegs()[1]) })
  }
  if (route === 'librarySettings') return mountLibrarySettings(container)
  if (route === 'vocabLevel') {
    return mountVocabLevel(container, { level: Number(routeSegs()[1]) || 0 })
  }
  if (route === 'paperAdmin') return mountPaperAdmin(container)
  if (route === 'students') return mountStudents(container)
  if (route === 'vocabExam') return mountVocabExam(container)
  if (route === 'exams') return mountExams(container, { lectures: state.lectures })
  return undefined
}
