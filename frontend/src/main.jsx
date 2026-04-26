import React, { useState, useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import LoginPage from './LoginPage.jsx'
import { webchatLogo } from './assets.js'
import { verifyToken, getSavedUser } from './auth.js'
import './index.css'

const favicon =
  document.querySelector("link[rel='icon']") ||
  document.head.appendChild(Object.assign(document.createElement('link'), { rel: 'icon' }))

favicon.type = 'image/png'
favicon.href = webchatLogo

function Root() {
  // null = 加载中, false = 未登录, object = 已登录用户
  const [user, setUser] = useState(null)

  useEffect(() => {
    // 页面加载时尝试用本地存储的 token 恢复登录态
    const saved = getSavedUser()
    if (!saved) {
      setUser(false)
      return
    }
    // 向后端验证 token 是否仍然有效
    verifyToken().then((verified) => {
      setUser(verified || false)
    })
  }, [])

  // 加载中：展示简单的 loading
  if (user === null) {
    return (
      <div className="auth-loading">
        <div className="auth-spinner-lg" />
      </div>
    )
  }

  // 未登录：展示登录/注册页
  if (!user) {
    return <LoginPage onLoginSuccess={(u) => setUser(u)} />
  }

  // 已登录：展示主应用
  return <App currentUser={user} onLogout={() => setUser(false)} />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
