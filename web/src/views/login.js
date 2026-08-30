import { api, setAuth } from '../api.js'

// 登录页：学生/教师账号入口。成功后写入 localStorage 登录态并回调重启应用。
export function mountLogin(el, { onLogin }) {
  el.innerHTML = `
    <div class="login-wrap">
      <div class="login-card card">
        <div class="brand login-brand">
          <span class="logo">学</span>
          <div>和爸学<small>学生版 / 教师版</small></div>
        </div>
        <form id="login-form">
          <label class="login-field">
            <span>用户名</span>
            <input id="login-user" autocomplete="username" placeholder="student / teacher" required />
          </label>
          <label class="login-field">
            <span>密码</span>
            <input id="login-pass" type="password" autocomplete="current-password" placeholder="密码" required />
          </label>
          <p class="login-error" id="login-error"></p>
          <button class="btn-primary login-submit" type="submit">登 录</button>
        </form>
      </div>
    </div>
  `

  const $err = el.querySelector('#login-error')
  el.querySelector('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault()
    $err.textContent = ''
    const user = el.querySelector('#login-user').value.trim()
    const password = el.querySelector('#login-pass').value
    try {
      const auth = await api.login(user, password)
      setAuth(auth)
      onLogin(auth)
    } catch (err) {
      $err.textContent = err.message || '登录失败'
    }
  })
}
