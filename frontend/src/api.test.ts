import { afterEach, expect, test, vi } from 'vitest'
import { ApiError, deletePhoto, getAuth, listPhotos, login, logout, savePhoto } from './api'

afterEach(() => vi.unstubAllGlobals())
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

test('all mutations include CSRF and same-origin cookies without setting multipart content type', async () => {
  const fetchMock = vi.fn(async () => json({ authenticated: true, username: 'admin', csrfToken: 'rotated' }))
  vi.stubGlobal('fetch', fetchMock)
  await login('admin', 'password', 'initial')
  await savePhoto('Пейзаж', new File(['pixels'], 'a.png', { type: 'image/png' }), undefined, 'rotated')
  await savePhoto('Новое название', null, 3, 'rotated')
  await deletePhoto(3, 'rotated')
  await logout('rotated')
  const options = fetchMock.mock.calls.map((call) => (call as unknown as [string, RequestInit])[1])
  expect(options.every((init) => init.credentials === 'same-origin')).toBe(true)
  expect(options.map((init) => init.headers)).toEqual([
    { 'X-CSRFToken': 'initial', 'Content-Type': 'application/json' },
    { 'X-CSRFToken': 'rotated' }, { 'X-CSRFToken': 'rotated' }, { 'X-CSRFToken': 'rotated' }, { 'X-CSRFToken': 'rotated' },
  ])
  expect(options[1].body).toBeInstanceOf(FormData)
  expect((options[1].body as FormData).get('title')).toBe('Пейзаж')
  expect((options[2].body as FormData).has('image')).toBe(false)
})

test('authentication state is never served from the browser cache', async () => {
  const fetchMock = vi.fn(async () => json({ authenticated: false, username: '', csrfToken: 'token' }))
  vi.stubGlobal('fetch', fetchMock)
  await getAuth()
  expect(fetchMock).toHaveBeenCalledWith('/api/auth/', expect.objectContaining({ cache: 'no-store' }))
})

test('empty successful delete responses are accepted', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })))
  await expect(deletePhoto(1, 'token')).resolves.toBeUndefined()
})

test('API errors retain their status for session and pagination recovery', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ title: ['Название слишком длинное.'] }, 400)))
  await expect(savePhoto('name', null, 1, 'token')).rejects.toMatchObject({ status: 400, message: 'Название слишком длинное.' })
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Too large</html>', { status: 413 })))
  await expect(savePhoto('name', null, 1, 'token')).rejects.toMatchObject({ status: 413, message: expect.stringMatching(/10 МБ/) })
})

test('network and malformed server responses are readable errors', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
  await expect(listPhotos('', 1)).rejects.toThrow('Не удалось связаться с сервером.')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>error</html>', { status: 502 })))
  await expect(listPhotos('', 1)).rejects.toBeInstanceOf(ApiError)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('broken', { status: 200 })))
  await expect(listPhotos('', 1)).rejects.toThrow('Сервер вернул некорректный ответ.')
})
