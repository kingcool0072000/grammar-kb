import { api } from '../api.js'

/** 学生壳启动：只探活。知识点留给教师壳。 */
export async function prepareStudent() {
  try {
    await api.stats()
    return true
  } catch {
    return false // 401 守卫会把人踢回登录页
  }
}
