/**
 * 认证 API 工具函数
 * 管理 JWT 令牌的存取，以及对后端认证接口的调用。
 */

const TOKEN_KEY = 'nekro_webchat_token'
const USER_KEY = 'nekro_webchat_user'

/** 从 localStorage 获取已保存的 token */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

/** 保存 token 和用户信息到 localStorage */
export function saveAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 从 localStorage 获取已保存的用户信息 */
export function getSavedUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

/** 清除所有登录状态 */
export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/**
 * 带认证头的 fetch 封装
 * 如果收到 401 响应则自动清除登录态。
 */
export async function authFetch(url, options = {}) {
  const token = getToken()
  const headers = { ...options.headers }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(url, { ...options, headers })
  if (res.status === 401) {
    clearAuth()
    window.location.reload()
  }
  return res
}

/** 注册 */
export async function register(username, password, displayName) {
  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, display_name: displayName }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || '注册失败')
  saveAuth(data.access_token, data.user)
  return data
}

/** 登录 */
export async function login(username, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || '登录失败')
  saveAuth(data.access_token, data.user)
  return data
}

/** 验证当前 token 是否有效 */
export async function verifyToken() {
  const token = getToken()
  if (!token) return null
  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` },
    })
    if (!res.ok) {
      clearAuth()
      return null
    }
    const user = await res.json()
    saveAuth(token, user)
    return user
  } catch {
    return null
  }
}
