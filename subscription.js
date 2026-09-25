/**
 * 订阅管理视图：查看订阅状态、剩余报告次数、兑换邀请码、暂停/恢复。
 *
 * 兑换流程（与后端一致，两步）：
 *   1. POST /api/subscriptions/activate  校验邀请码有效性
 *   2. POST /api/subscriptions           绑定品类，激活订阅
 */

import api from '../api/client.js'

export function renderSubscription(view) {
  view.innerHTML = `
    <section class="card">
      <h2>我的订阅</h2>
      <div id="subscription-info">
        <div class="loading">加载中…</div>
      </div>
    </section>

    <section class="card">
      <h2>兑换邀请码</h2>
      <p class="hint">输入邀请码激活订阅，每个邀请码含 12 份报告额度。</p>
      <div class="form-col" style="max-width:520px">
        <label>邀请码</label>
        <input id="invite-code" type="text" placeholder="请输入邀请码" />
        <div id="activate-info" class="msg"></div>
        <label>绑定品类（兑换后生成该品类的报告）</label>
        <input id="invite-hs" type="text" placeholder="HS6 编码，如 950691" value="950691" />
        <button id="btn-redeem" class="btn-primary">立即兑换</button>
      </div>
      <div id="redeem-msg" class="msg"></div>
    </section>
  `

  loadSubscription()

  const codeInput = document.getElementById('invite-code')
  codeInput.addEventListener('blur', () => checkInviteCode(codeInput.value.trim()))
  codeInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') redeem()
  })
  document.getElementById('btn-redeem').addEventListener('click', redeem)
}

async function loadSubscription() {
  const el = document.getElementById('subscription-info')
  try {
    const list = await api.mySubscriptions()
    const subs = Array.isArray(list) ? list : []
    if (subs.length === 0) {
      el.innerHTML = `
        <div class="empty">
          <p>暂无有效订阅</p>
          <p style="margin-top:8px;color:var(--ink-3);font-size:13px">使用邀请码兑换后，即可绑定品类并生成报告</p>
        </div>
      `
      return
    }
    el.innerHTML = subs.map(s => {
      const status = s.status || 'none'
      const badge =
        status === 'active' ? '<span class="badge badge-success">活跃</span>' :
        status === 'paused' ? '<span class="badge badge-warning">已暂停</span>' :
        '<span class="badge">其他</span>'
      const remaining = (s.total_reports || 0) - (s.delivered_reports || 0)
      return `
        <div class="kv-row">
          <span class="k">${s.display_name || s.hs_name || s.hs_code || ''}</span>
          <span class="v">${badge}</span>
        </div>
        <div class="kv-row"><span class="k">剩余报告次数</span><span class="v">${remaining}</span></div>
        <div class="kv-row"><span class="k">总可用报告</span><span class="v">${s.total_reports || 0}</span></div>
        <div class="kv-row"><span class="k">有效期</span><span class="v">${formatDate(s.started_at)} ~ ${formatDate(s.expires_at)}</span></div>
        <div class="actions">
          ${status === 'active' ? `<button class="btn-secondary" data-pause="${s.id}">⏸ 暂停订阅</button>` : ''}
          ${status === 'paused' ? `<button class="btn-primary" data-resume="${s.id}">▶ 恢复订阅</button>` : ''}
        </div>
      `
    }).join('<hr style="border:none;border-top:1px dashed var(--line);margin:12px 0">')

    el.querySelectorAll('button[data-pause]').forEach(btn => {
      btn.addEventListener('click', () => togglePause(btn.dataset.pause, 'pause'))
    })
    el.querySelectorAll('button[data-resume]').forEach(btn => {
      btn.addEventListener('click', () => togglePause(btn.dataset.resume, 'resume'))
    })
  } catch (e) {
    el.innerHTML = `<div class="error">加载失败：${e.message}</div>`
  }
}

async function checkInviteCode(code) {
  const info = document.getElementById('activate-info')
  if (!code) { info.innerHTML = ''; return }
  try {
    const data = await api.activateInviteCode({ invite_code: code })
    info.innerHTML = `<span class="success">✓ 有效邀请码，含 ${data.reports_count} 份报告（${data.price / 100} 元）</span>`
  } catch (e) {
    info.innerHTML = `<span class="error">✗ ${e.message}</span>`
  }
}

async function redeem() {
  const code = document.getElementById('invite-code').value.trim()
  const hsCode = document.getElementById('invite-hs').value.trim()
  const msg = document.getElementById('redeem-msg')
  const btn = document.getElementById('btn-redeem')

  if (!code) { msg.innerHTML = '<span class="error">请输入邀请码</span>'; return }
  if (!hsCode) { msg.innerHTML = '<span class="error">请输入 HS6 品类编码</span>'; return }

  btn.disabled = true
  msg.innerHTML = ''
  try {
    const sub = await api.createSubscription({ invite_code: code, hs_code: hsCode })
    msg.innerHTML = `<span class="success">✅ 兑换成功！订阅「${sub.display_name || sub.hs_code}」，剩余 ${sub.total_reports - sub.delivered_reports} 份报告</span>`
    document.getElementById('invite-code').value = ''
    document.getElementById('activate-info').innerHTML = ''
    loadSubscription()
  } catch (e) {
    msg.innerHTML = `<span class="error">❌ 兑换失败：${e.message}</span>`
  } finally {
    btn.disabled = false
  }
}

async function togglePause(id, action) {
  try {
    if (action === 'pause') await api.pauseSubscription(id)
    else await api.resumeSubscription(id)
    loadSubscription()
  } catch (e) {
    alert((action === 'pause' ? '暂停' : '恢复') + '失败：' + e.message)
  }
}

function formatDate(s) {
  if (!s) return '-'
  return String(s).slice(0, 10)
}
