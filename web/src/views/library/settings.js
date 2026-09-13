import { api } from '../../api.js'
import { ICONS } from './icons.js'
import { escapeHtml } from '../../render.js'
import './settings.css'

// 属性上下文转义：escapeHtml 不处理引号，placeholder="..." 里含 " 会截断属性
const escAttr = (s) => escapeHtml(s).replace(/"/g, '&#34;').replace(/'/g, '&#39;')

// 泛读馆 · 教师设置（三张卡：AI 学习 / 字典 / 学生阅读主题）
// 自 FCEReadingLib SettingsPage 的全局段直译；账号管理与个人偏好卡不复刻（本项目有自己的 auth）。
// GET/PUT 均为教师接口（路由已挡学生）；PUT 契约为浅合并三段（ai / dict / student）。
const FALLBACK_MODELS = ['glm-4-flash', 'glm-4-air', 'glm-4-airx', 'glm-4-plus', 'glm-4-long', 'glm-4-flashx']

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

  // ---------- 自制 toast ----------
  function toast(msg, type = 'info') {
    let wrap = root.querySelector(':scope > .lib-toast-wrap')
    if (!wrap) {
      wrap = document.createElement('div')
      wrap.className = 'lib-toast-wrap'
      root.append(wrap)
    }
    const t = document.createElement('div')
    t.className = `lib-toast ${type}`
    t.textContent = msg
    wrap.append(t)
    setTimeout(() => {
      t.classList.add('out')
      setTimeout(() => t.remove(), 260)
    }, type === 'error' ? 5000 : 3000)
  }

  let cfg = null // GET 到的设置
  let models = [] // 已拉取的模型列表（空 = 未拉取，用内置候选）

  root.innerHTML = `
    <header class="ls-head">
      <div>
        <h1>泛读馆设置</h1>
        <p>AI 预习生成 · 字典兜底 · 学生阅读主题（每张卡独立保存）</p>
      </div>
    </header>
    <div data-body><div class="ls-skel"></div></div>
  `
  const bodyEl = root.querySelector('[data-body]')

  function buildCards() {
    const ai = cfg.ai || {}
    const dict = cfg.dict || {}
    const student = cfg.student || {}
    const defaultPrompt = cfg.defaultPrompt || ''
    const currentModel = ai.model || 'glm-4-flash'

    bodyEl.innerHTML = `
      <!-- ======== 卡1 AI 学习 ======== -->
      <section class="ls-card">
        <h3>AI 学习</h3>
        <p class="ls-desc">智谱 GLM 配置，用于生成章节预习（困难单词 + 俚语）。Key 只保存在本机服务端，不会传到别处。</p>

        <div class="ls-row">
          <div class="ls-label">
            <div class="name">API Key</div>
            <div class="hint">在 open.bigmodel.cn 控制台获取${cfg.hasKey ? '（已保存，可覆盖更新）' : ''}</div>
          </div>
          <div class="ls-ctrl">
            <input type="password" class="ls-input ls-key" placeholder="智谱 open.bigmodel.cn 的 API Key" autocomplete="off">
            <button class="lib-btn small sec" id="ls-test">测试连接</button>
          </div>
        </div>
        <div class="ls-feedback" id="ls-test-result" hidden></div>

        <div class="ls-row">
          <div class="ls-label">
            <div class="name">预置模型</div>
            <div class="hint">点击「拉取模型列表」从智谱获取可用模型</div>
          </div>
          <div class="ls-ctrl">
            <select class="ls-input ls-model" id="ls-model"></select>
            <button class="lib-btn small sec" id="ls-models">拉取模型列表</button>
          </div>
        </div>

        <div class="ls-row">
          <div class="ls-label">
            <div class="name">难度档位</div>
            <div class="hint">决定哪些词算「困难词」：L1 基础（4千词内算已会）· L2 FCE（默认，8千词）· L3 CAE（1.5万词）</div>
          </div>
          <div class="ls-ctrl">
            <div class="ls-seg" id="ls-diff">
              <button data-v="L1">L1 基础</button>
              <button data-v="L2">L2 FCE</button>
              <button data-v="L3">L3 CAE</button>
            </div>
          </div>
        </div>

        <div class="ls-row">
          <div class="ls-label">
            <div class="name">每章生词上限</div>
            <div class="hint">AI 最终输出的困难单词数量上限</div>
          </div>
          <div class="ls-ctrl">
            <div class="ls-slider-row">
              <input type="range" min="5" max="25" step="1" id="ls-mw">
              <output id="ls-mw-out">12</output>
            </div>
          </div>
        </div>

        <div class="ls-prompt-block">
          <div class="ls-label">
            <div class="name">自定义 System 提示词</div>
            <div class="hint">留空 = 使用默认提示词；清空并保存即恢复默认</div>
          </div>
          <textarea class="ls-textarea" id="ls-prompt" rows="10" spellcheck="false" placeholder="${escAttr(defaultPrompt)}"></textarea>
        </div>

        <div class="ls-card-actions">
          <button class="lib-btn" id="ls-save-ai">保存</button>
        </div>
      </section>

      <!-- ======== 卡2 字典 ======== -->
      <section class="ls-card">
        <h3>字典</h3>
        <p class="ls-desc">查词优先使用内置离线词典 ECDICT（36.5 万词条，中文释义 + 音标，无需联网），查不到时按下方开关走 AI 兜底。</p>
        <div class="ls-row">
          <div class="ls-label">
            <div class="name">查词 AI 兜底</div>
            <div class="hint">内置词典查不到时，用 AI 生成中文解释</div>
          </div>
          <div class="ls-ctrl">
            <label class="ls-switch">
              <input type="checkbox" id="ls-aifb">
              <span class="ls-switch-track"></span>
            </label>
          </div>
        </div>
        <div class="ls-card-actions">
          <button class="lib-btn" id="ls-save-dict">保存</button>
        </div>
      </section>

      <!-- ======== 卡3 学生阅读主题 ======== -->
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
    `
    bindCards()
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
    const ai = cfg.ai || {}
    const dict = cfg.dict || {}
    const student = cfg.student || {}

    const keyInput = root.querySelector('.ls-key')
    const modelSel = root.querySelector('#ls-model')
    const testBtn = root.querySelector('#ls-test')
    const testResult = root.querySelector('#ls-test-result')
    const modelsBtn = root.querySelector('#ls-models')
    const diffSeg = root.querySelector('#ls-diff')
    const mwRange = root.querySelector('#ls-mw')
    const mwOut = root.querySelector('#ls-mw-out')
    const promptTA = root.querySelector('#ls-prompt')
    const aiFallbackCb = root.querySelector('#ls-aifb')
    const presetsEl = root.querySelector('#ls-presets')
    const fontSeg = root.querySelector('#ls-font')

    // ---- 初始值 ----
    keyInput.value = ai.apiKey || ''
    rebuildModelOptions(ai.model || 'glm-4-flash')
    setSeg(diffSeg, ai.difficulty || 'L2')
    setSlider(Number(ai.maxWords) || 12)
    promptTA.value = ai.prompt || ''
    aiFallbackCb.checked = dict.aiFallback !== false
    setPreset(student.preset || 'paper')
    setSeg(fontSeg, student.fontFamily || 'serif')

    // ---- 模型 select：当前值不在列表时也要能显示 ----
    function rebuildModelOptions(current) {
      const base = models.length ? models : FALLBACK_MODELS
      const list = [...new Set([current, ...base])]
      modelSel.innerHTML = list.map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('')
      modelSel.value = current
    }

    // ---- seg / 预设 / 滑杆小工具 ----
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
    function setSlider(v) {
      if (v < 5) v = 5
      if (v > 25) v = 25
      mwRange.value = v
      mwOut.textContent = v
      mwRange.style.setProperty('--fill', `${((v - 5) / 20) * 100}%`)
    }
    diffSeg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]')
      if (b) setSeg(diffSeg, b.dataset.v)
    })
    fontSeg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]')
      if (b) setSeg(fontSeg, b.dataset.v)
    })
    presetsEl.addEventListener('click', (e) => {
      const b = e.target.closest('.ls-preset')
      if (b) setPreset(b.dataset.v)
    })
    mwRange.addEventListener('input', () => setSlider(Number(mwRange.value)))

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

    // ---- 测试连接 ----
    testBtn.addEventListener('click', async () => {
      const key = keyInput.value.trim()
      if (!key) { toast('请先填写 API Key', 'error'); return }
      busy(testBtn, true, '测试中…')
      testResult.hidden = true
      try {
        await api.libraryTestAi(key)
        testResult.hidden = false
        testResult.className = 'ls-feedback ok'
        testResult.textContent = '✓ 连接成功，模型可用'
      } catch (e) {
        testResult.hidden = false
        testResult.className = 'ls-feedback err'
        testResult.textContent = `✗ ${e.message || '测试失败'}`
      } finally {
        busy(testBtn, false)
      }
    })

    // ---- 拉取模型列表 ----
    modelsBtn.addEventListener('click', async () => {
      const key = keyInput.value.trim()
      busy(modelsBtn, true, '获取中…')
      try {
        const r = await api.libraryModels(key || undefined)
        if (!r || !Array.isArray(r.models) || r.models.length === 0) throw new Error('返回列表为空')
        models = r.models
        rebuildModelOptions(modelSel.value)
        toast(`获取到 ${r.models.length} 个模型`, 'success')
      } catch (e) {
        models = [...FALLBACK_MODELS]
        rebuildModelOptions(modelSel.value)
        toast(`模型列表获取失败（${e.message || '接口异常'}），已显示内置候选`, 'error')
      } finally {
        busy(modelsBtn, false)
      }
    })

    // ---- 三张卡各自保存（PUT 浅合并：传整段对象） ----
    const saveBtn = root.querySelector('#ls-save-ai')
    saveBtn.addEventListener('click', async () => {
      busy(saveBtn, true, '保存中…')
      try {
        await api.librarySaveSettings({
          ai: {
            apiKey: keyInput.value.trim(),
            model: modelSel.value,
            difficulty: segGet(diffSeg) || 'L2',
            maxWords: Number(mwRange.value),
            prompt: promptTA.value,
          },
        })
        toast('AI 学习设置已保存', 'success')
      } catch (e) {
        toast(e.message || '保存失败', 'error')
      } finally {
        busy(saveBtn, false)
      }
    })

    const dictBtn = root.querySelector('#ls-save-dict')
    dictBtn.addEventListener('click', async () => {
      busy(dictBtn, true, '保存中…')
      try {
        await api.librarySaveSettings({ dict: { aiFallback: !!aiFallbackCb.checked } })
        toast('字典设置已保存', 'success')
      } catch (e) {
        toast(e.message || '保存失败', 'error')
      } finally {
        busy(dictBtn, false)
      }
    })

    const studentBtn = root.querySelector('#ls-save-student')
    studentBtn.addEventListener('click', async () => {
      busy(studentBtn, true, '保存中…')
      try {
        await api.librarySaveSettings({
          student: { preset: presetGet(), fontFamily: segGet(fontSeg) || 'serif' },
        })
        toast('学生阅读主题已保存', 'success')
      } catch (e) {
        toast(e.message || '保存失败', 'error')
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
