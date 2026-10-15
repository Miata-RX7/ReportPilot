const TOKEN_KEY = 'rp_token'

/**
 * token 的唯一持久化出口。
 * http 拦截器直接读 storage（而非 Pinia store），避免 store <-> http 循环依赖；
 * store 负责保持内存状态与 storage 同步。
 */
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
