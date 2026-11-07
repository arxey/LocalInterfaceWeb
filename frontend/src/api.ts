export interface Photo {
  id: number
  title: string
  image_url: string
  thumbnail_url: string
  created_at: string
  updated_at: string
}

export interface PhotoPage {
  count: number
  next: string | null
  previous: string | null
  results: Photo[]
}

export interface AuthStatus {
  authenticated: boolean
  username: string
  csrfToken: string
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, { credentials: 'same-origin', ...options })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new Error('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.')
  }
  if (response.ok) {
    if (response.status === 204) return undefined as T
    try { return await response.json() as T } catch { throw new Error('Сервер вернул некорректный ответ. Повторите попытку.') }
  }
  const body = await response.json().catch(() => null) as Record<string, unknown> | null
  const first = body && Object.values(body)[0]
  const message = response.status === 413 ? 'Размер изображения превышает допустимый предел 10 МБ.'
    : Array.isArray(first) ? String(first[0]) : typeof first === 'string' ? first : 'Не удалось выполнить запрос. Повторите попытку.'
  throw new ApiError(message, response.status)
}

export function getAuth(signal?: AbortSignal): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/', { signal, cache: 'no-store' })
}

export function login(username: string, password: string, csrfToken: string): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify({ username, password }),
  })
}

export function logout(csrfToken: string): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/', { method: 'DELETE', headers: { 'X-CSRFToken': csrfToken } })
}

export function listPhotos(query: string, page: number, signal?: AbortSignal): Promise<PhotoPage> {
  const params = new URLSearchParams({ page: String(page) })
  if (query.trim()) params.set('q', query.trim())
  return request<PhotoPage>(`/api/photos/?${params}`, { signal })
}

export function savePhoto(title: string, image: File | null, id: number | undefined, csrfToken: string): Promise<Photo> {
  const form = new FormData()
  form.append('title', title)
  if (image) form.append('image', image)
  return request<Photo>(id ? `/api/photos/${id}/` : '/api/photos/', {
    method: id ? 'PATCH' : 'POST',
    headers: { 'X-CSRFToken': csrfToken },
    body: form,
  })
}

export function deletePhoto(id: number, csrfToken: string): Promise<void> {
  return request<void>(`/api/photos/${id}/`, { method: 'DELETE', headers: { 'X-CSRFToken': csrfToken } })
}
