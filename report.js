/**
 * 报告详情视图：展示任务状态、预览图占位、下载入口。
 */

import api from '../api/client.js'

export function renderReport(view, taskId) {
  view.innerHTML = `
    <section class="card">
      <h2>报告详情</h2>
      <p class="hint">任务 ID：<code>${taskId}</code></p>
      <div id="report-box">
        <div class="alert alert-info">加载中...</div>
      </div>
    </section>
  `
  const box = view.querySelector('#report-box')
  refresh()

  async function refresh() {
    try {
      const t = await api.taskStatus(taskId)
      if (t.status === 'success') {
        box.innerHTML = `
          <div class="report-success">
            <h3>${t.hs_code} · ${t.period} 观测报告</h3>
            <p>类型：${t.report_type === 'monthly' ? '月报' : '季报'}</p>
            <p>生成时间：${new Date(t.finished_at).toLocaleString('zh-CN')}</p>
            <div class="report-actions">
              <a class="btn-primary" href="${api.downloadReport(taskId)}" target="_blank">
                ⬇ 下载 PDF 报告
              </a>
              <button class="btn-ghost" onclick="location.hash='#'">返回重新生成</button>
            </div>
          </div>
        `
      } else if (t.status === 'failed') {
        box.innerHTML = `<div class="alert alert-error">生成失败：${t.error || '未知错误'}</div>`
      } else {
        box.innerHTML = `
          <div class="alert alert-info">
            <div class="task-status">${t.status} · ${t.progress || 0}%</div>
            <div class="progress"><div class="bar" style="width:${t.progress || 0}%"></div></div>
          </div>
        `
        setTimeout(refresh, 2000)
      }
    } catch (e) {
      box.innerHTML = `<div class="alert alert-error">加载失败：${e.message}</div>`
    }
  }
}
