/**
 * 认证 API 工具函数
 * 管理 JWT 令牌的存取，以及对后端认证接口的调用。
 */

const TOKEN_KEY = 'nekro_webchat_token'
const USER_KEY = 'nekro_webchat_user'
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '')

function isAbsoluteUrl(value) {
  return /^https?:\/\//i.test(value)
}

export function getApiBaseUrl() {
  return API_BASE_URL
}

export function withApiBase(url) {
  if (!url) return url
  if (isAbsoluteUrl(url)) return url
  if (!API_BASE_URL) return url
  return `${API_BASE_URL}${url.startsWith('/') ? url : `/${url}`}`
}

function normalizeUserAssets(user) {
  if (!user || typeof user !== 'object') return user
  const next = { ...user }
  if (typeof next.avatar === 'string' && (next.avatar.startsWith('/data/') || next.avatar.startsWith('/uploads/') || next.avatar.startsWith('/api/'))) {
    next.avatar = withApiBase(next.avatar)
  }
  if (typeof next.ai_avatar === 'string' && (next.ai_avatar.startsWith('/data/') || next.ai_avatar.startsWith('/uploads/') || next.ai_avatar.startsWith('/api/'))) {
    next.ai_avatar = withApiBase(next.ai_avatar)
  }
  return next
}

export function getWsBaseUrl() {
  if (!API_BASE_URL) return ''
  const wsProtocol = API_BASE_URL.startsWith('https://') ? 'wss://' : 'ws://'
  return API_BASE_URL.replace(/^https?:\/\//i, wsProtocol)
}

/** 从 localStorage 获取已保存的 token */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

/** 保存 token 和用户信息到 localStorage */
export function saveAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(normalizeUserAssets(user)))
}

/** 从 localStorage 获取已保存的用户信息 */
export function getSavedUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? normalizeUserAssets(JSON.parse(raw)) : null
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
  const res = await fetch(withApiBase(url), { ...options, headers })
  if (res.status === 401) {
    clearAuth()
    window.location.reload()
  }
  return res
}

const API_URL = import.meta.env.VITE_API_URL || ''

/** 注册 */
export async function register(username, password, displayName) {
  const res = await fetch(withApiBase('/api/auth/register'), {
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
  const res = await fetch(withApiBase('/api/auth/login'), {
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
    const res = await fetch(withApiBase('/api/auth/me'), {
      headers: { 'Authorization': `Bearer ${token}` },
    })
    if (!res.ok) {
      clearAuth()
      return null
    }
    const user = await res.json()
    const normalizedUser = normalizeUserAssets(user)
    saveAuth(token, normalizedUser)
    return normalizedUser
  } catch {
    return null
  }
}

