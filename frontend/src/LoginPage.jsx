import React, { useState } from 'react'
import { webchatLogo } from './assets'
import { login, register } from './auth'

/**
 * 登录 / 注册页面组件
 * 双面卡片翻转动画，包含登录和注册两个表单。
 */
export default function LoginPage({ onLoginSuccess }) {
  const [isRegister, setIsRegister] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const captchaInstanceRef = useRef(null)
  const stateRef = useRef()
  stateRef.current = { username, password, displayName, isRegister }

  const proceedWithAuth = async (verifyParam) => {
    setLoading(true)
    setError('')
    try {
      const { username, password, displayName, isRegister } = stateRef.current
      if (isRegister) {
        const data = await register(username, password, displayName, verifyParam)
        onLoginSuccess(data.user)
      } else {
        const data = await login(username, password, verifyParam)
        onLoginSuccess(data.user)
      }
    } catch (err) {
      setError(err.message)
      if (captchaInstanceRef.current && typeof captchaInstanceRef.current.refresh === 'function') {
        captchaInstanceRef.current.refresh()
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (typeof window.initAliyunCaptcha !== 'function') {
      console.warn('window.initAliyunCaptcha is not available')
      return
    }

    const sceneId = isRegister
      ? import.meta.env.VITE_ALIYUN_CAPTCHA_REGISTER_SCENE
      : import.meta.env.VITE_ALIYUN_CAPTCHA_LOGIN_SCENE


    window.initAliyunCaptcha({
      SceneId: sceneId,
      mode: "popup",
      element: "#captcha-element",
      button: "#captcha-trigger-btn",
      success: function (captchaVerifyParam) {
        proceedWithAuth(captchaVerifyParam)
      },
      fail: function (result) {
        console.error('Captcha Fail:', result)
      },
      getInstance: function (instance) {
        captchaInstanceRef.current = instance
      },
      server: ['captcha-esa-open.aliyuncs.com', 'captcha-esa-open-b.aliyuncs.com'],
      slideStyle: {
        width: 360,
        height: 40,
      },
    })
  }, [isRegister])

  const handleSubmit = (e) => {
    e.preventDefault()
    setError('')

    const btn = document.getElementById('captcha-trigger-btn')
    if (btn) {
      btn.click()
    } else {
      setError('验证码组件加载中，请稍候')
    }
  }


  const switchMode = () => {
    setError('')
    setIsRegister(!isRegister)
  }

  return (
    <div className="auth-page">
      {/* 背景装饰 */}
      <div className="auth-bg-orb auth-bg-orb-1" />
      <div className="auth-bg-orb auth-bg-orb-2" />
      <div className="auth-bg-orb auth-bg-orb-3" />

      <div className="auth-card">
        {/* Logo */}
        <div className="auth-logo">
          <img src={webchatLogo} alt="Logo" />
        </div>
        <h1 className="auth-title">Nekro WebChat</h1>
        <p className="auth-subtitle">
          {isRegister ? '创建你的账号' : '欢迎回来，请登录'}
        </p>

        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="auth-field">
            <label htmlFor="auth-username">用户名</label>
            <input
              id="auth-username"
              type="text"
              placeholder="输入用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={2}
              maxLength={32}
              autoComplete="username"
            />
          </div>

          {isRegister && (
            <div className="auth-field">
              <label htmlFor="auth-display-name">显示名称</label>
              <input
                id="auth-display-name"
                type="text"
                placeholder="可选，你希望别人看到的名字"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                maxLength={64}
              />
            </div>
          )}

          <div className="auth-field">
            <label htmlFor="auth-password">密码</label>
            <input
              id="auth-password"
              type="password"
              placeholder={isRegister ? '至少 6 位密码' : '输入密码'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              maxLength={128}
              autoComplete={isRegister ? 'new-password' : 'current-password'}
            />
          </div>

          {error && <div className="auth-error">{error}</div>}

          {/* 预留的验证码元素及隐藏触发器 */}
          <div id="captcha-element"></div>
          <button id="captcha-trigger-btn" type="button" style={{ display: 'none' }}></button>

          <button
            className="auth-submit"
            type="submit"
            disabled={loading}
          >
            {loading ? (
              <span className="auth-spinner" />
            ) : (
              isRegister ? '注册' : '登录'
            )}
          </button>
        </form>

        <div className="auth-switch">
          {isRegister ? '已有账号？' : '没有账号？'}
          <button type="button" onClick={switchMode}>
            {isRegister ? '去登录' : '去注册'}
          </button>
        </div>
      </div>
    </div>
  )
}
