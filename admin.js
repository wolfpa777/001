/**
 * 管理后台视图：邀请码管理、网站配置、公告管理、数据导入。
 * 需要管理员权限（后端依赖 get_admin_user 拦截）。
 */

import api from '../api/client.js'

export function renderAdmin(view) {
  view.innerHTML = `
    <section class="card">
      <h2>管理后台</h2>
      <div class="tabs">
        <button class="tab-btn active" data-tab="invite">邀请码管理</button>
        <button class="tab-btn" data-tab="config">网站配置</button>
        <button class="tab-btn" data-tab="ann">公告管理</button>
        <button class="tab-btn" data-tab="import">数据导入</button>
      </div>

      <div id="tab-invite" class="tab-panel active">
        <div class="form-row">
          <label>生成数量</label>
          <input id="invite-count" type="number" value="5" min="1" max="100" style="width:90px" />
          <label style="width:auto">批次名</label>
          <input id="invite-batch" type="text" placeholder="可选，便于分发统计" style="flex:1;min-width:160px" />
          <button id="btn-gen-invite" class="btn-primary">生成邀请码</button>
        </div>
        <div id="invite-result" class="msg"></div>
        <div id="invite-table"><div class="loading">加载中…</div></div>
      </div>

      <div id="tab-config" class="tab-panel">
        <div id="config-form"></div>
      </div>

      <div id="tab-ann" class="tab-panel">
        <div class="form-col">
          <label>公告标题</label>
          <input id="ann-title" type="text" placeholder="标题" />
          <label>公告内容（Markdown）</label>
          <textarea id="ann-content" rows="4" placeholder="支持 Markdown"></textarea>
          <label>级别</label>
          <select id="ann-level">
            <option value="info">普通</option>
            <option value="warn">警告</option>
            <option value="urgent">紧急</option>
          </select>
          <button id="btn-pub-ann" class="btn-primary">发布公告</button>
        </div>
        <div id="ann-table" style="margin-top:20px"><div class="loading">加载中…</div></div>
      </div>

      <div id="tab-import" class="tab-panel">
        <div class="form-col">
          <label>选择海关数据 CSV 文件</label>
          <input id="csv-file" type="file" accept=".csv" />
          <button id="btn-import" class="btn-primary">导入数据</button>
        </div>
        <div id="import-result" class="msg"></div>
        <div id="batch-list" style="margin-top:24px"></div>
      </div>
    </section>
  `

  bindTabs()
  loadInviteCodes()

  document.getElementById('btn-gen-invite').addEventListener('click', generateInviteCodes)
  document.getElementById('btn-pub-ann').addEventListener('click', publishAnnouncement)
  document.getElementById('btn-import').addEventListener('click', importCsv)
}

function bindTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'))
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'))
      btn.classList.add('active')
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active')

      if (btn.dataset.tab === 'invite') loadInviteCodes()
      if (btn.dataset.tab === 'config') loadSiteConfig()
      if (btn.dataset.tab === 'ann') loadAnnouncements()
      if (btn.dataset.tab === 'import') loadBatches()
    })
  })
}

/* ---------- 邀请码 ---------- */
async function loadInviteCodes() {
  const el = document.getElementById('invite-table')
  try {
    const codes = await api.listInviteCodes()
    if (!codes.length) { el.innerHTML = '<div class="empty">暂无邀请码</div>'; return }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>邀请码</th><th>状态</th><th>报告数</th><th>批次</th><th>使用者</th><th>激活时间</th></tr></thead>
        <tbody>
          ${codes.map(c => `
            <tr>
              <td><code>${c.code}</code></td>
              <td>${c.status === 'used' ? '<span class="badge badge-error">已使用</span>' : c.status === 'revoked' ? '<span class="badge">已撤销</span>' : '<span class="badge badge-success">未使用</span>'}</td>
              <td>${c.reports_count || '-'}</td>
              <td>${c.batch_name || '-'}</td>
              <td>${c.activated_by || '-'}</td>
              <td>${formatTime(c.activated_at)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `
  } catch (e) {
    el.innerHTML = `<div class="error">加载失败：${e.message}</div>`
  }
}

async function generateInviteCodes() {
  const count = parseInt(document.getElementById('invite-count').value, 10) || 5
  const batchName = document.getElementById('invite-batch').value.trim() || undefined
  const result = document.getElementById('invite-result')
  try {
    const data = await api.createInviteCodes({ count, batch_name: batchName })
    result.innerHTML = `<span class="success">✅ 已生成 ${data.codes ? data.codes.length : count} 个邀请码：${data.codes ? data.codes.join('、') : ''}</span>`
    loadInviteCodes()
  } catch (e) {
    result.innerHTML = `<span class="error">❌ 生成失败：${e.message}</span>`
  }
}

/* ---------- 网站配置 ---------- */
async function loadSiteConfig() {
  const form = document.getElementById('config-form')
  try {
    const configs = await api.listSiteConfig()
    if (!configs.length) { form.innerHTML = '<div class="empty">暂无配置项</div>'; return }
    form.innerHTML = configs.map(c => `
      <div class="form-row">
        <label style="width:200px">${c.key}<span style="color:var(--ink-3);font-size:11px;display:block">${c.description || ''}</span></label>
        <input type="text" data-key="${c.key}" class="config-input" value="${(c.value || '').replace(/"/g, '&quot;')}" />
      </div>
    `).join('') + '<button id="btn-save-config" class="btn-primary">保存配置</button>'
    document.getElementById('btn-save-config').addEventListener('click', saveSiteConfig)
  } catch (e) {
    form.innerHTML = `<div class="error">加载失败：${e.message}</div>`
  }
}

async function saveSiteConfig() {
  const inputs = document.querySelectorAll('.config-input')
  // 后端是逐个 upsert（PUT /api/admin/content/config/{key}），这里逐条提交
  try {
    for (const i of inputs) {
      await api.updateSiteConfig(i.dataset.key, { value: i.value })
    }
    alert('配置已保存')
  } catch (e) {
    alert('保存失败：' + e.message)
  }
}

/* ---------- 公告 ---------- */
async function loadAnnouncements() {
  const el = document.getElementById('ann-table')
  try {
    const anns = await api.listAnnouncements()
    if (!anns.length) { el.innerHTML = '<div class="empty">暂无公告</div>'; return }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>标题</th><th>级别</th><th>发布时间</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          ${anns.map(a => `
            <tr>
              <td>${a.title}</td>
              <td>${a.level || '-'}</td>
              <td>${formatTime(a.created_at)}</td>
              <td>${a.is_published ? '<span class="badge badge-success">展示中</span>' : '<span class="badge">已下线</span>'}</td>
              <td>
                <button class="btn-outline btn-sm" data-toggle="${a.id}" data-active="${a.is_published ? 0 : 1}">${a.is_published ? '下线' : '上线'}</button>
                <button class="btn-outline btn-sm" data-del="${a.id}">删除</button>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `
    el.querySelectorAll('button[data-toggle]').forEach(btn => {
      btn.addEventListener('click', () => toggleAnnouncement(btn.dataset.toggle, btn.dataset.active === '1'))
    })
    el.querySelectorAll('button[data-del]').forEach(btn => {
      btn.addEventListener('click', () => deleteAnnouncement(btn.dataset.del))
    })
  } catch (e) {
    el.innerHTML = `<div class="error">加载失败：${e.message}</div>`
  }
}

async function publishAnnouncement() {
  const title = document.getElementById('ann-title').value.trim()
  const content = document.getElementById('ann-content').value.trim()
  const level = document.getElementById('ann-level').value
  if (!title || !content) { alert('请填写标题和内容'); return }
  try {
    await api.createAnnouncement({ title, content, level, is_published: true })
    alert('公告已发布')
    document.getElementById('ann-title').value = ''
    document.getElementById('ann-content').value = ''
    loadAnnouncements()
  } catch (e) {
    alert('发布失败：' + e.message)
  }
}

async function toggleAnnouncement(id, active) {
  try {
    await api.updateAnnouncement(id, { is_published: active })
    loadAnnouncements()
  } catch (e) {
    alert('操作失败：' + e.message)
  }
}

async function deleteAnnouncement(id) {
  if (!confirm('确定删除此公告？')) return
  try {
    await api.deleteAnnouncement(id)
    loadAnnouncements()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

/* ---------- 数据导入 ---------- */
async function importCsv() {
  const fileInput = document.getElementById('csv-file')
  const result = document.getElementById('import-result')
  const file = fileInput.files[0]
  if (!file) { result.innerHTML = '<span class="error">请选择 CSV 文件</span>'; return }

  const btn = document.getElementById('btn-import')
  btn.disabled = true
  result.innerHTML = '<span>上传中…</span>'
  try {
    const resp = await api.importCsv(file)
    const lines = []
    lines.push(resp.ok ? `✅ 导入${resp.inserted} 条` : `⚠ 部分导入（${resp.inserted} 条）`)
    if (resp.ignored) lines.push(`跳过重复 ${resp.ignored} 条`)
    if (resp.failed) lines.push(`失败 ${resp.failed} 条`)
    if (resp.warnings && resp.warnings.length) lines.push(`警告：${resp.warnings.join('；')}`)
    if (resp.errors && resp.errors.length) lines.push(`错误：${resp.errors.join('；')}`)
    result.innerHTML = `<span class="${resp.ok ? 'success' : 'error'}">${lines.join(' · ')}</span>`
    fileInput.value = ''
    loadBatches()
  } catch (e) {
    result.innerHTML = `<span class="error">❌ 导入失败：${e.message}</span>`
  } finally {
    btn.disabled = false
  }
}

async function loadBatches() {
  const el = document.getElementById('batch-list')
  el.innerHTML = '<div class="loading">加载中…</div>'
  try {
    const batches = await api.listBatches()
    if (!batches.length) { el.innerHTML = '<div class="empty">暂无导入记录</div>'; return }
    el.innerHTML = `
      <h3 style="font-size:15px;margin-bottom:8px">导入批次记录</h3>
      <table class="data-table">
        <thead><tr><th>ID</th><th>源文件</th><th>总行数</th><th>导入</th><th>失败</th><th>状态</th><th>导入时间</th></tr></thead>
        <tbody>
          ${batches.map(b => `
            <tr>
              <td>${b.id}</td>
              <td><code style="font-size:11px">${b.source_file}</code></td>
              <td>${b.total_rows}</td>
              <td>${b.imported_rows}</td>
              <td>${b.failed_rows || 0}</td>
              <td>${b.status === 'success' ? '<span class="badge badge-success">成功</span>' : '<span class="badge badge-error">失败</span>'}</td>
              <td>${formatTime(b.imported_at)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `
  } catch (e) {
    el.innerHTML = `<div class="error">加载失败：${e.message}</div>`
  }
}

function formatTime(s) {
  if (!s) return '-'
  const d = new Date(s)
  if (isNaN(d)) return s
  return d.toLocaleString('zh-CN', { hour12: false })
}
