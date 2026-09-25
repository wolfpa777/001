/**
 * 报告生成视图：
 * 1. 品类输入（支持口语词，自动映射 HS6）
 * 2. 数据期选择
 * 3. 触发生成，轮询任务状态
 * 4. 成功后跳转下载
 */

import api from '../api/client.js'

let PERIODS = []
let HS_CODE = ''
let HS_NAME = ''

export function renderReportGen(view) {
  view.innerHTML = `
    <section class="card">
      <h2>生成国际市场观测报告</h2>
      <p class="hint">输入品类名称或口语词（如「健身器材」），系统自动匹配海关 HS 编码。</p>

      <div class="form-row">
        <label>品类 / 口语词</label>
        <input id="q" type="text" placeholder="例如：健身器材、塑料制品、锂电池"
               value="健身器材" autocomplete="off" />
        <button id="btn-search" class="btn-ghost">匹配 HS 编码</button>
      </div>
      <div id="match-box"></div>

      <div class="form-row">
        <label>数据期</label>
        <select id="period">
          <option value="">加载中...</option>
        </select>
        <span class="hint">数据按月更新，通常滞后 1-2 个月</span>
      </div>

      <button id="btn-gen" class="btn-primary">生成报告</button>
      <div id="status-box"></div>
    </section>
  `

  const q = view.querySelector('#q')
  const matchBox = view.querySelector('#match-box')
  const periodSel = view.querySelector('#period')
  const statusBox = view.querySelector('#status-box')
  const btnGen = view.querySelector('#btn-gen')

  // 默认匹配
  searchAndShow(q.value.trim(), matchBox, periodSel)

  view.querySelector('#btn-search').onclick = () =>
    searchAndShow(q.value.trim(), matchBox, periodSel)

  btnGen.onclick = async () => {
    if (!HS_CODE) {
      statusBox.innerHTML = `<div class="alert alert-warn">请先匹配品类</div>`
      return
    }
    const period = periodSel.value
    if (!period) {
      statusBox.innerHTML = `<div class="alert alert-warn">请选择数据期</div>`
      return
    }
    statusBox.innerHTML = `<div class="alert alert-info">提交生成任务...</div>`
    try {
      const task = await api.generateReport({ hs_code: HS_CODE, period, report_type: 'monthly' })
      pollTask(task.task_id, statusBox)
    } catch (e) {
      statusBox.innerHTML = `<div class="alert alert-error">生成失败：${e.message}</div>`
    }
  }
}

async function searchAndShow(query, box, periodSel) {
  if (!query) return
  box.innerHTML = `<span class="hint">匹配中...</span>`
  try {
    const res = await api.searchCategory(query)
    const matches = res.matches || []
    if (!matches.length) {
      box.innerHTML = `<div class="alert alert-warn">未匹配到品类，可尝试更通用的名称</div>`
      HS_CODE = ''
      return
    }
    HS_CODE = matches[0].code
    HS_NAME = matches[0].name
    box.innerHTML = `
      <div class="match-list">
        ${matches.slice(0, 5).map(m => `
          <div class="match-item ${m.code === HS_CODE ? 'active' : ''}"
               onclick="window.__pickHs && window.__pickHs('${m.code}','${m.name.replace(/'/g, '')}')">
            <strong>${m.code}</strong> ${m.name}
            <span class="tag">${m.chapter || ''}章</span>
          </div>`).join('')}
      </div>
      <p class="hint">已选：<b>${HS_CODE}</b> ${HS_NAME}</p>
    `
    loadPeriods(periodSel)
  } catch (e) {
    box.innerHTML = `<div class="alert alert-error">匹配失败：${e.message}</div>`
  }
}

async function loadPeriods(sel) {
  if (!HS_CODE) return
  try {
    const res = await api.periods(HS_CODE)
    PERIODS = res.available_periods || []
    sel.innerHTML = PERIODS.map(p => `<option value="${p}">${p}</option>`).join('')
    sel.value = res.latest_period || PERIODS[PERIODS.length - 1] || ''
  } catch (e) {
    sel.innerHTML = `<option value="">加载失败</option>`
  }
}

function pollTask(taskId, box) {
  const timer = setInterval(async () => {
    try {
      const t = await api.taskStatus(taskId)
      const pct = t.progress || 0
      box.innerHTML = `
        <div class="alert alert-info">
          <div class="task-meta">${t.hs_code} · ${t.period} · ${t.report_type}</div>
          <div class="progress"><div class="bar" style="width:${pct}%"></div></div>
          <div class="task-status">状态：${t.status} · ${pct}%</div>
        </div>
      `
      if (t.status === 'success') {
        clearInterval(timer)
        const url = api.downloadReport(taskId)
        box.innerHTML += `
          <div class="alert alert-success">
            ✅ 报告已生成！
            <a class="btn-download" href="${url}" target="_blank">下载 PDF</a>
          </div>
        `
        location.hash = `#/report/${taskId}`
      } else if (t.status === 'failed') {
        clearInterval(timer)
        box.innerHTML += `<div class="alert alert-error">生成失败：${t.error || '未知错误'}</div>`
      }
    } catch (e) {
      clearInterval(timer)
      box.innerHTML += `<div class="alert alert-error">查询任务失败：${e.message}</div>`
    }
  }, 1500)
}
