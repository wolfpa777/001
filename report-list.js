/**
 * 报告列表视图：历史报告浏览、下载。
 *
 * 后端 GET /api/reports/tasks 返回扁平数组（ReportTaskOut），
 * 无分页、无品类/状态筛选，故前端仅做本地过滤与分页。
 */

import api from '../api/client.js'

const PAGE_SIZE = 10
let allReports = []
let currentPage = 1
let filterHs = ''
let filterStatus = ''

export function renderReportList(view) {
  view.innerHTML = `
    <section class="card">
      <div class="card-head">
        <h2>我的报告</h2>
        <button id="btn-refresh" class="btn-ghost">↻ 刷新</button>
      </div>
      <div class="filters">
        <input id="filter-hs" type="text" placeholder="HS6 编码筛选，如 950691" style="padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px;width:200px" />
        <select id="filter-status">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
          <option value="running">生成中</option>
          <option value="pending">等待中</option>
        </select>
        <button id="btn-filter" class="btn-primary">筛选</button>
      </div>
      <div id="report-list"><div class="loading">加载中…</div></div>
      <div id="pagination" class="pagination"></div>
    </section>
  `

  loadReports()
  document.getElementById('btn-refresh').addEventListener('click', loadReports)
  document.getElementById('btn-filter').addEventListener('click', applyFilter)
}

async function loadReports() {
  const list = document.getElementById('report-list')
  list.innerHTML = '<div class="loading">加载中…</div>'
  try {
    allReports = await api.listReports()
    allReports = Array.isArray(allReports) ? allReports : []
    currentPage = 1
    renderTable()
  } catch (e) {
    list.innerHTML = `<div class="error">加载失败：${e.message}</div>`
  }
}

function applyFilter() {
  filterHs = document.getElementById('filter-hs').value.trim().toLowerCase()
  filterStatus = document.getElementById('filter-status').value
  currentPage = 1
  renderTable()
}

function filtered() {
  return allReports.filter(r => {
    const okHs = !filterHs || (r.hs_code || '').toLowerCase().includes(filterHs)
    const okSt = !filterStatus || r.status === filterStatus
    return okHs && okSt
  })
}

function renderTable() {
  const list = document.getElementById('report-list')
  const data = filtered()
  if (data.length === 0) {
    list.innerHTML = '<div class="empty">暂无报告，去生成一份吧</div>'
    document.getElementById('pagination').innerHTML = ''
    return
  }

  const statusClass = { success: 'badge-success', failed: 'badge-error', running: 'badge-warning', pending: 'badge-warning' }
  const page = data.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  list.innerHTML = `
    <table class="data-table">
      <thead>
        <tr><th>品类</th><th>数据期</th><th>类型</th><th>生成时间</th><th>状态</th><th>操作</th></tr>
      </thead>
      <tbody>
        ${page.map(r => `
          <tr>
            <td>${r.hs_code || '-'}</td>
            <td>${r.period || '-'}</td>
            <td>${r.report_type || '-'}</td>
            <td>${formatTime(r.created_at)}</td>
            <td><span class="badge ${statusClass[r.status] || 'badge-warning'}">${r.status}</span></td>
            <td class="actions">
              ${r.status === 'success' ? `<button class="btn-outline btn-sm" data-act="download" data-id="${r.task_id}">下载</button>` : ''}
              ${r.status === 'failed' ? `<span style="color:var(--error);font-size:12px">${r.error || '失败'}</span>` : ''}
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `

  list.querySelectorAll('button[data-act="download"]').forEach(btn => {
    btn.addEventListener('click', () => downloadReport(btn.dataset.id))
  })

  renderPagination(data.length)
}

function renderPagination(total) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  let html = ''
  if (totalPages > 1) {
    for (let i = 1; i <= totalPages; i++) {
      html += `<button class="${i === currentPage ? 'active' : ''}" data-page="${i}">${i}</button>`
    }
  }
  const pagination = document.getElementById('pagination')
  pagination.innerHTML = html
  pagination.querySelectorAll('button[data-page]').forEach(btn => {
    btn.addEventListener('click', () => {
      currentPage = parseInt(btn.dataset.page, 10)
      renderTable()
    })
  })
}

function downloadReport(taskId) {
  const url = api.downloadReport(taskId)
  window.open(url, '_blank')
}

function formatTime(s) {
  if (!s) return '-'
  const d = new Date(s)
  if (isNaN(d)) return s
  return d.toLocaleString('zh-CN', { hour12: false })
}
