/**
 * 登录 / 注册视图
 */

import api from '../api/client.js'

export function renderAuth(view, mode = 'login') {
  view.innerHTML = `
    <section class="card auth-card">
      <h2>${mode === 'login' ? '登录' : '注册'}</h2>
      <div class="tabs">
        <button class="${mode === 'login' ? 'active' : ''}" data-mode="login">登录</button>
        <button class="${mode === 'register' ? 'active' : ''}" data-mode="register">注册</button>
      </div>

      <div class="form-col">
        <label>手机号 / 邮箱</label>
        <input id="account" type="text" placeholder="手机号或邮箱" autocomplete="username" />

        <label>密码</label>
        <input id="password" type="password" placeholder="至少 6 位" autocomplete="current-password" />

        <div id="register-fields" style="${mode === 'register' ? '' : 'display:none'}">
          <label>昵称</label>
          <input id="nickname" type="text" placeholder="选填" />
          <label>公司</label>
          <input id="company" type="text" placeholder="选填" />
        </div>

        <button id="btn-submit" class="btn-primary btn-block">
          ${mode === 'login' ? '登 录' : '注 册'}
        </button>
        <div id="auth-msg"></div>
      </div>
    </section>
  `

  const msg = view.querySelector('#auth-msg')
  view.querySelectorAll('.tabs button').forEach(b => {
    b.onclick = () => renderAuth(view, b.dataset.mode)
  })

  view.querySelector('#btn-submit').onclick = async () => {
    const account = view.querySelector('#account').value.trim()
    const password = view.querySelector('#password').value
    if (!account || !password) {
      msg.innerHTML = `<div class="alert alert-warn">请填写账号和密码</div>`
      return
    }
    msg.innerHTML = `<div class="alert alert-info">处理中...</div>`

    try {
      let res
      if (mode === 'login') {
        res = await api.login({ account, password })
      } else {
        const isPhone = /^1\d{10}$/.test(account)
        res = await api.register({
          phone: isPhone ? account : undefined,
          email: isPhone ? undefined : account,
          password,
          nickname: view.querySelector('#nickname').value || undefined,
          company: view.querySelector('#company').value || undefined,
        })
      }
      localStorage.setItem('token', res.access_token)
      localStorage.setItem('user', JSON.stringify({ id: res.user_id, nickname: res.nickname, role: res.role }))
      msg.innerHTML = `<div class="alert alert-success">${mode === 'login' ? '登录成功' : '注册成功'}，跳转中...</div>`
      setTimeout(() => location.hash = '#/', 800)
    } catch (e) {
      msg.innerHTML = `<div class="alert alert-error">${e.message}</div>`
    }
  }
}
