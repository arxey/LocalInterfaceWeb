import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'

const photo = { id: 1, title: 'Закат', image_url: '/media/photos/a.png', thumbnail_url: '/media/thumbnails/a.webp', created_at: '2026-09-27T08:00:00Z', updated_at: '2026-09-27T08:00:00Z' }
const page = (results = [photo], extra = {}) => ({ count: results.length, next: null, previous: null, results, ...extra })
const response = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
let authenticated = false
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  authenticated = false
  fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/auth/') {
      if (options?.method === 'POST') authenticated = true
      if (options?.method === 'DELETE') authenticated = false
      return response({ authenticated, username: authenticated ? 'admin' : '', csrfToken: authenticated ? 'logged-in-token' : 'guest-token' })
    }
    if (options?.method === 'DELETE') return response(null, 204)
    return response(page())
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('URL', URL)
  URL.createObjectURL = vi.fn(() => 'blob:test-photo')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function openEditor() {
  authenticated = true
  render(<App />)
  const opener = await screen.findByRole('button', { name: 'Открыть Закат' })
  opener.focus()
  fireEvent.click(opener)
  return screen.findByRole('dialog')
}

test('anonymous browsing opens the full image without edit controls', async () => {
  render(<App />)
  expect(await screen.findByText('Закат')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Добавить фотографию' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Удалить Закат' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть Закат' }))
  expect(await screen.findByRole('dialog', { name: 'Просмотр фотографии' })).toBeInTheDocument()
  expect(screen.getByAltText('Закат — полное изображение')).toHaveAttribute('src', photo.image_url)
  expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
})

test('login sends credentials with CSRF and enables changes; logout removes them', async () => {
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Войти' }))
  fireEvent.change(screen.getByLabelText('Имя пользователя'), { target: { value: 'admin' } })
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'local-password' } })
  fireEvent.click(screen.getByRole('button', { name: 'Войти в архив' }))
  expect(await screen.findByRole('button', { name: 'Добавить фотографию' })).toBeInTheDocument()
  const [, options] = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
  expect(options.headers).toMatchObject({ 'X-CSRFToken': 'guest-token', 'Content-Type': 'application/json' })
  expect(JSON.parse(options.body)).toEqual({ username: 'admin', password: 'local-password' })
  fireEvent.click(screen.getByRole('button', { name: 'Выйти' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Добавить фотографию' })).not.toBeInTheDocument())
})

test('login errors are visible without enabling changes', async () => {
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => url === '/api/auth/' ? response(options?.method ? { detail: 'Неверное имя пользователя или пароль.' } : { authenticated: false, username: '', csrfToken: 'guest-token' }, options?.method ? 400 : 200) : response(page()))
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Войти' }))
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'wrong' } })
  fireEvent.click(screen.getByRole('button', { name: 'Войти в архив' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Неверное имя пользователя или пароль.')
  expect(screen.queryByRole('button', { name: 'Добавить фотографию' })).not.toBeInTheDocument()
})

test('a delayed response from a cancelled search cannot replace the current results', async () => {
  let resolveOld!: (value: Response) => void
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/auth/') return response({ authenticated: false, username: '', csrfToken: 'guest-token' })
    if (url.includes('q=old')) return new Promise<Response>((resolve) => { resolveOld = resolve })
    return response(page([{ ...photo, title: url.includes('q=new') ? 'Новый результат' : 'Закат' }]))
  })
  render(<App />)
  await screen.findByText('Закат')
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск по названию' }), { target: { value: 'old' } })
  fireEvent.submit(screen.getByRole('search'))
  await waitFor(() => expect(resolveOld).toBeTypeOf('function'))
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск по названию' }), { target: { value: 'new' } })
  fireEvent.submit(screen.getByRole('search'))
  await screen.findByText('Новый результат')
  await act(async () => { resolveOld(response(page([{ ...photo, title: 'Устаревший результат' }]))); await Promise.resolve() })
  expect(screen.queryByText('Устаревший результат')).not.toBeInTheDocument()
  expect(screen.getByText('Новый результат')).toBeInTheDocument()
})

test('failed catalogue requests show a retry instead of an empty collection', async () => {
  fetchMock.mockImplementation(async (url: string) => url === '/api/auth/' ? response({ authenticated: false, username: '', csrfToken: 'guest-token' }) : response({ detail: 'Сервис недоступен.' }, 503))
  render(<App />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Сервис недоступен.')
  expect(screen.queryByText('Пока нет фотографий')).not.toBeInTheDocument()
  fetchMock.mockImplementation(async (url: string) => response(url === '/api/auth/' ? { authenticated: false, username: '', csrfToken: 'guest-token' } : page()))
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  expect(await screen.findByText('Закат')).toBeInTheDocument()
})

test('the editor traps keyboard focus and Escape restores focus to its opener', async () => {
  const dialog = await openEditor()
  const save = within(dialog).getByRole('button', { name: 'Сохранить' })
  save.focus()
  fireEvent.keyDown(dialog, { key: 'Tab' })
  expect(within(dialog).getByRole('button', { name: 'К каталогу' })).toHaveFocus()
  fireEvent.keyDown(dialog, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Открыть Закат' })).toHaveFocus()
})

test('failed saves preserve the form for retry and cannot be submitted twice or closed while saving', async () => {
  let failSave!: (reason: Error) => void
  const dialog = await openEditor()
  fetchMock.mockImplementation((url: string, options?: RequestInit) => options?.method === 'PATCH' ? new Promise<Response>((_, reject) => { failSave = reject }) : Promise.resolve(response(url === '/api/auth/' ? { authenticated: true, username: 'admin', csrfToken: 'token' } : page())))
  fireEvent.change(within(dialog).getByLabelText('Название фотографии'), { target: { value: 'Исправленное название' } })
  const form = within(dialog).getByRole('button', { name: 'Сохранить' }).closest('form')!
  fireEvent.submit(form)
  fireEvent.submit(form)
  fireEvent.keyDown(dialog, { key: 'Escape' })
  fireEvent.mouseDown(dialog.parentElement!)
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
  await act(async () => { failSave(new TypeError('Failed to fetch')) })
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(/Не удалось/)
  expect(within(dialog).getByLabelText('Название фотографии')).toHaveValue('Исправленное название')
  expect(within(dialog).getByRole('button', { name: 'Сохранить' })).toBeEnabled()
})

test('expired sessions remove write controls and explain how to sign in again', async () => {
  const dialog = await openEditor()
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => options?.method === 'PATCH' ? response({ detail: 'Authentication credentials were not provided.' }, 403) : response(url === '/api/auth/' ? { authenticated: false, username: '', csrfToken: 'guest-token' } : page()))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Добавить фотографию' })).not.toBeInTheDocument())
  expect(await screen.findByRole('alert')).toHaveTextContent(/Сеанс.*Войдите/)
})

test('deleting the last photo on a page recovers to the previous page', async () => {
  authenticated = true
  let removed = false
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === '/api/auth/') return response({ authenticated: true, username: 'admin', csrfToken: 'token' })
    if (options?.method === 'DELETE') { removed = true; return response(null, 204) }
    if (url.includes('page=2')) return removed ? response({ detail: 'Invalid page.' }, 404) : response(page([{ ...photo, id: 2, title: 'Последний' }], { count: 21, previous: '/api/photos/?page=1' }))
    return response(page([photo], { count: removed ? 20 : 21, next: removed ? null : '/api/photos/?page=2' }))
  })
  render(<App />)
  await screen.findByText('Закат')
  fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
  await screen.findByText('Последний')
  fireEvent.click(screen.getByRole('button', { name: 'Удалить Последний' }))
  expect(await screen.findByText('Закат')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Предыдущая страница' })).toBeDisabled()
})


test('upload validation rejects oversize files without discarding the selected valid image', async () => {
  authenticated = true
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Добавить фотографию' }))
  const dialog = screen.getByRole('dialog')
  const imageInput = within(dialog).getByLabelText('Изображение')
  const good = new File(['pixels'], 'valid.png', { type: 'image/png' })
  fireEvent.change(imageInput, { target: { files: [good] } })
  expect(await within(dialog).findByText('valid.png')).toBeInTheDocument()
  const large = new File(['pixels'], 'large.png', { type: 'image/png' })
  Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 })
  fireEvent.change(imageInput, { target: { files: [large] } })
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('до 10 МБ')
  expect(within(dialog).getByText('valid.png')).toBeInTheDocument()
  fireEvent.change(within(dialog).getByLabelText('Название фотографии'), { target: { value: 'Новый снимок' } })
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => options?.method === 'POST' ? response({ ...photo, title: 'Новый снимок' }, 201) : response(url === '/api/auth/' ? { authenticated: true, username: 'admin', csrfToken: 'token' } : page()))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const [, options] = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
  expect((options.body as FormData).get('image')).toBe(good)
})

test('duplicate delete clicks issue a single mutation', async () => {
  authenticated = true
  let finishDelete!: (value: Response) => void
  render(<App />)
  const remove = await screen.findByRole('button', { name: 'Удалить Закат' })
  fetchMock.mockImplementation((url: string, options?: RequestInit) => options?.method === 'DELETE' ? new Promise<Response>((resolve) => { finishDelete = resolve }) : Promise.resolve(response(url === '/api/auth/' ? { authenticated: true, username: 'admin', csrfToken: 'token' } : page())))
  fireEvent.click(remove)
  fireEvent.click(remove)
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(1)
  expect(remove).toBeDisabled()
  await act(async () => { finishDelete(response(null, 204)) })
})

test('an older authentication check cannot overwrite a successful login', async () => {
  let checks = 0
  let attempts = 0
  let finishOldCheck!: (value: Response) => void
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url !== '/api/auth/') return response(page())
    if (options?.method === 'POST') {
      attempts += 1
      return attempts === 1 ? response({ detail: 'Неверный пароль.' }, 401) : response({ authenticated: true, username: 'admin', csrfToken: 'signed-in' })
    }
    checks += 1
    if (checks > 1) return new Promise<Response>((resolve) => { finishOldCheck = resolve })
    return response({ authenticated: false, username: '', csrfToken: 'guest' })
  })
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Войти' }))
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'wrong' } })
  fireEvent.click(screen.getByRole('button', { name: 'Войти в архив' }))
  await screen.findByText('Неверный пароль.')
  await waitFor(() => expect(finishOldCheck).toBeTypeOf('function'))
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'correct' } })
  fireEvent.click(screen.getByRole('button', { name: 'Войти в архив' }))
  await screen.findByRole('button', { name: 'Добавить фотографию' })
  await act(async () => { finishOldCheck(response({ authenticated: false, username: '', csrfToken: 'guest' })) })
  expect(screen.getByRole('button', { name: 'Добавить фотографию' })).toBeInTheDocument()
})

test('a successful retry clears a failed authentication status check', async () => {
  let authChecks = 0
  fetchMock.mockImplementation(async (url: string) => {
    if (url !== '/api/auth/') return response(page())
    authChecks += 1
    return authChecks === 1 ? response({ detail: 'Проверка входа недоступна.' }, 503) : response({ authenticated: false, username: '', csrfToken: 'guest' })
  })
  render(<App />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Проверка входа недоступна.')
  fireEvent.click(screen.getByRole('button', { name: 'Повторить проверку входа' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }))
  expect(screen.getByRole('button', { name: 'Войти в архив' })).toBeEnabled()
})
