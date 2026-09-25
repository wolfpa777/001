/**
 * 前端入口：极简 SPA，无框架依赖，原生 JS 渲染。
 *
 * 路由：
 *   #/                  报告生成
 *   #/report/:taskId    报告详情/下载
 *   #/subscription      订阅管理
 *   #/reports           我的报告列表
 *   #/admin             管理后台（管理员）
 *   #/login | #/register  登录 / 注册
 */

import './styles.css'
import './styles/auth.css'
import api from './api/client.js'
import { renderReportGen } from './views/report-gen.js'
import { renderReport } from './views/report.js'
import { renderAuth } from './views/auth.js'
import { renderSubscription } from './views/subscription.js'
import { renderReportList } from './views/report-list.js'
import { renderAdmin } from './views/admin.js'

const root = document.getElementById('app')

function getUser() {
  try { return JSON.parse(localStorage.getItem('user') || 'null') } catch { return null }
}

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  location.hash = '#/'
}

function requireAuth() {
  const token = localStorage.getItem('token')
  if (!token) {
    location.hash = '#/login'
    return false
  }
  return true
}

function render() {
  const hash = location.hash || '#/'
  root.innerHTML = layout()

  const view = root.querySelector('#view')

  // 公开路由
  if (hash === '#/login' || hash === '#/register') {
    renderAuth(view, hash === '#/register' ? 'register' : 'login')
    return
  }

  // 以下均需登录
  if (!requireAuth()) return

  if (hash === '#/subscription') {
    renderSubscription(view)
  } else if (hash === '#/reports') {
    renderReportList(view)
  } else if (hash === '#/admin') {
    renderAdmin(view)
  } else if (hash.startsWith('#/report/')) {
    const taskId = hash.slice('#/report/'.length)
    renderReport(view, taskId)
  } else {
    renderReportGen(view)
  }
}

function navLinks() {
  return `
    <a href="#/" class="${location.hash === '' || location.hash === '#/' ? 'active' : ''}">生成报告</a>
    <a href="#/reports">我的报告</a>
    <a href="#/subscription">订阅</a>
    <a href="#/admin">后台</a>
  `
}

function layout() {
  const user = getUser()
  return `
    <header class="topbar">
      <div class="brand">
        <span class="logo">📊</span>
        <div>
          <h1>出口商品国际市场观测报告工具</h1>
          <p>海关数据统计 · 自动生成观测报告</p>
        </div>
      </div>
      ${user ? `<div class="user-bar">
        <nav class="nav-inline">${navLinks()}</nav>
        <span class="avatar">${(user.nickname || '?').charAt(0)}</span>
        <span>${user.nickname || '用户'}</span>
        <button class="btn-ghost" id="btn-logout">退出</button>
      </div>` : `<nav>
        <a href="#/login">登录</a>
        <a href="#/register" class="btn-ghost" style="padding:6px 14px;border-radius:6px;">注册</a>
      </nav>`}
    </header>
    <main id="view"></main>
    <footer>数据来源：中国海关统计月报 · 本报告仅供参考，不构成投资建议</footer>
  `
}

window.addEventListener('hashchange', render)
window.__logout = logout
root.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'btn-logout') logout()
})
render()
