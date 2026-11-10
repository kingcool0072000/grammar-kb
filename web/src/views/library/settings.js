import { api } from '../../api.js'
import { ICONS } from './icons.js'
import { escapeHtml } from '../../render.js'
import { toast } from '../../toast.js'
import './settings.css'

// 泛读馆 · 教师设置（两张卡：学生阅读主题 / 书籍解锁）
// 自 FCEReadingLib SettingsPage 的全局段直译；账号管理与个人偏好卡不复刻（本项目有自己的 auth）。
// GET/PUT 契约为浅合并（student 段）；书籍解锁走独立的 book-unlock 接口。

// 学生阅读主题四预设（swatch 预览用同一组配色）
const PRESETS = [
  { key: 'paper', label: '纸白', bg: '#ffffff', fg: '#2b2722' },
  { key: 'sepia', label: '护眼米黄', bg: '#f5efe0', fg: '#5b4636' },
  { key: 'night', label: '夜间', bg: '#1f2430', fg: '#c9d1d9' },
  { key: 'contrast', label: '高对比', bg: '#000000', fg: '#ffffff' },
]

export async function mountLibrarySettings(viewEl) {
  viewEl.innerHTML = ''
  const root = document.createElement('div')
  root.className = 'lib-settings'
  viewEl.append(root)

  let cfg = null // GET 到的设置

  root.innerHTML = `
    <header class="ls-head">
      <div>
        <h1>泛读馆设置</h1>
        <p>学生阅读主题 · 书籍解锁（每张卡独立保存）</p>
      </div>
    </header>
    <div data-body><div class="ls-skel"></div></div>
  `
  const bodyEl = root.querySelector('[data-body]')

  function buildCards() {
    const student = cfg.student || {}

    bodyEl.innerHTML = `
      <!-- ======== 卡1 学生阅读主题 ======== -->
      <section class="ls-card">
        <h3>学生阅读主题</h3>
        <p class="ls-desc">统一配置学生在泛读馆的阅读配色与字体（学生仅能自行调字号）。</p>
        <div class="ls-row">
          <div class="ls-label"><div class="name">预设配色</div></div>
          <div class="ls-ctrl ls-presets" id="ls-presets">
            ${PRESETS.map((p) => `
              <button class="ls-preset" data-v="${p.key}" title="${p.label}">
                <span class="ls-swatch" style="background:${p.bg};color:${p.fg}">Aa</span>
                <span class="ls-preset-name">${p.label}</span>
              </button>`).join('')}
          </div>
        </div>
        <div class="ls-row">
          <div class="ls-label"><div class="name">字体</div></div>
          <div class="ls-ctrl">
            <div class="ls-seg" id="ls-font">
              <button data-v="serif">衬线</button>
              <button data-v="sans">无衬线</button>
              <button data-v="kai">楷体</button>
            </div>
          </div>
        </div>
        <div class="ls-card-actions">
          <button class="lib-btn" id="ls-save-student">保存</button>
        </div>
      </section>

      <!-- ======== 卡2 书籍解锁（学生可见性门控） ======== -->
      <section class="ls-card">
        <h3>书籍解锁</h3>
        <p class="ls-desc">给每个学生勾选可见的书（教师手动解锁；未配置 = 全部可见）。正在读的书不会被收回。</p>
        <div id="ls-books-unlock"><p class="ls-desc">加载中…</p></div>
        <div class="ls-card-actions">
          <button class="lib-btn" id="ls-save-unlock">保存解锁配置</button>
        </div>
      </section>
    `
    bindCards()
    loadBookUnlock()
  }

  async function loadBookUnlock() {
    const host = viewEl.querySelector('#ls-books-unlock')
    if (!host) return
    try {
      const [unlockRes, booksRes, usersRes] = await Promise.all([
        api.libraryBookUnlock(),
        api.libraryBooks(),
        api.users(),
      ])
      const unlock = unlockRes || {}
      const students = (usersRes.users || []).filter((u) => u.role === 'student')
      const books = (booksRes.books || []).filter((b) => b.id != null)
      if (!books.length) { host.innerHTML = '<p class="ls-desc">书架还没有书。</p>'; return }
      const configured = students.some((st) => (unlock[st.user] || []).length)
      host.innerHTML = `
        <p class="ls-desc">${configured ? '' : '（当前未配置，学生可见全部书目）'}</p>
        <div class="ls-unlock-matrix">
          <div class="ls-unlock-head">
            <span></span>
            ${books.map((b) => `<span title="${escapeHtml(b.title)}">${escapeHtml(b.title.slice(0, 14))}</span>`).join('')}
          </div>
          ${students.map((st) => `
            <div class="ls-unlock-row" data-stu="${escapeHtml(st.user)}">
              <span class="ls-unlock-name">🎒 ${escapeHtml(st.user)}</span>
              ${books.map((b) => `<label class="ls-unlock-cell"><input type="checkbox" data-ck="${b.id}" ${((unlock[st.user] || []).includes(b.id)) ? 'checked' : ''}/></label>`).join('')}
            </div>`).join('')}
        </div>`
      viewEl.querySelector('#ls-save-unlock').addEventListener('click', async () => {
        const mapping = {}
        host.querySelectorAll('.ls-unlock-row').forEach((row) => {
          const ids = [...row.querySelectorAll('input:checked')].map((i) => Number(i.dataset.ck))
          if (ids.length) mapping[row.dataset.stu] = ids
        })
        try {
          await api.libraryBookUnlockPut(mapping)
          toast('书籍解锁已保存', { root })
        } catch (e) { toast('保存失败：' + e.message, { type: 'error', root }) }
      })
    } catch (e) {
      host.innerHTML = `<p class="ls-desc">加载失败：${escapeHtml(e.message)}</p>`
    }
  }

  function showErrorCard(err) {
    bodyEl.innerHTML = `
      <div class="ls-error-card">
        <div class="art">${ICONS.warn(40)}</div>
        <h3>设置加载失败</h3>
        <p>${escapeHtml(err)}</p>
        <button class="lib-btn" data-retry>重试</button>
      </div>`
    bodyEl.querySelector('[data-retry]').addEventListener('click', load)
  }

  // ---------- 卡片交互 ----------
  function bindCards() {
    const student = cfg.student || {}

    const presetsEl = root.querySelector('#ls-presets')
    const fontSeg = root.querySelector('#ls-font')

    // ---- 初始值 ----
    setPreset(student.preset || 'paper')
    setSeg(fontSeg, student.fontFamily || 'serif')

    // ---- seg / 预设小工具 ----
    function setSeg(seg, v) {
      seg.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === v))
    }
    function segGet(seg) {
      const b = seg.querySelector('button.active')
      return b ? b.dataset.v : null
    }
    function setPreset(v) {
      presetsEl.querySelectorAll('.ls-preset').forEach((b) => b.classList.toggle('active', b.dataset.v === v))
    }
    function presetGet() {
      const b = presetsEl.querySelector('.ls-preset.active')
      return b ? b.dataset.v : 'paper'
    }
    fontSeg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]')
      if (b) setSeg(fontSeg, b.dataset.v)
    })
    presetsEl.addEventListener('click', (e) => {
      const b = e.target.closest('.ls-preset')
      if (b) setPreset(b.dataset.v)
    })

    // ---- 按钮忙态 ----
    function busy(btn, on, keepHtml) {
      btn.disabled = on
      if (on) {
        btn.dataset.label = btn.innerHTML
        btn.innerHTML = '<span class="lib-spin"></span>' + (keepHtml || '')
      } else if (btn.dataset.label) {
        btn.innerHTML = btn.dataset.label
      }
    }

    // ---- 各卡保存（PUT 浅合并：传整段对象） ----
    const studentBtn = root.querySelector('#ls-save-student')
    studentBtn.addEventListener('click', async () => {
      busy(studentBtn, true, '保存中…')
      try {
        const unlockSeg = root.querySelector('#ls-vunlock button.active')
        await api.librarySaveSettings({
          student: {
            preset: presetGet(),
            fontFamily: segGet(fontSeg) || 'serif',
          },
        })
        toast('学生阅读主题已保存', { type: 'success', root })
      } catch (e) {
        toast(e.message || '保存失败', { type: 'error', root })
      } finally {
        busy(studentBtn, false)
      }
    })
  }

  // 返回由全局 hiddenTab 返回条（← 返回备课中心）承担，页内不再放返回控件

  // ---------- 数据加载 ----------
  async function load() {
    bodyEl.innerHTML = '<div class="ls-skel"></div>'
    try {
      cfg = await api.librarySettings()
      buildCards()
    } catch (e) {
      showErrorCard(e.message || '无法连接后端')
    }
  }

  await load()
}
