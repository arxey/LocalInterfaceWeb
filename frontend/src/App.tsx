import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Camera, ChevronLeft, ChevronRight, Image as ImageIcon, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { ApiError, deletePhoto, getAuth, listPhotos, login, logout, type AuthStatus, type Photo, type PhotoPage } from './api'
import PhotoEditor from './PhotoEditor'
import './styles.css'

const dateFormat = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' })
function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : dateFormat.format(date)
}

export default function App() {
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<PhotoPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [catalogError, setCatalogError] = useState('')
  const [error, setError] = useState('')
  const [editor, setEditor] = useState<Photo | 'new' | null>(null)
  const [revision, setRevision] = useState(0)
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [authRevision, setAuthRevision] = useState(0)
  const [authError, setAuthError] = useState('')
  const [authStatusError, setAuthStatusError] = useState('')
  const [showLogin, setShowLogin] = useState(false)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const [removing, setRemoving] = useState<number | null>(null)
  const authBusyRef = useRef(false)
  const authGeneration = useRef(0)
  const removingRef = useRef(false)
  const canEdit = auth?.authenticated === true

  useEffect(() => {
    const controller = new AbortController()
    const generation = ++authGeneration.current
    getAuth(controller.signal).then((result) => {
      if (!controller.signal.aborted && generation === authGeneration.current) { setAuth(result); setAuthStatusError('') }
    })
      .catch((cause) => { if (!controller.signal.aborted && generation === authGeneration.current) setAuthStatusError(cause instanceof Error ? cause.message : 'Не удалось проверить вход.') })
    return () => controller.abort()
  }, [authRevision])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setCatalogError('')
    setData(null)
    listPhotos(query, page, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        if (!result.results.length && page > 1) { setPage(1); return }
        setData(result)
      })
      .catch((cause) => {
        if (controller.signal.aborted) return
        if (cause instanceof ApiError && cause.status === 404 && page > 1) { setPage(1); return }
        setCatalogError(cause instanceof Error ? cause.message : 'Не удалось загрузить фотографии.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [query, page, revision])

  function expired() {
    setAuth(null)
    setAuthError('Сеанс завершён или изменился. Войдите снова, чтобы продолжить изменения.')
    setShowLogin(true)
    setEditor(null)
    setAuthRevision((value) => value + 1)
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!auth?.csrfToken || authBusyRef.current) return
    authBusyRef.current = true
    authGeneration.current += 1
    setAuthBusy(true)
    setAuthError('')
    try {
      setAuth(await login(username.trim(), password, auth.csrfToken))
      setPassword('')
      setShowLogin(false)
    } catch (cause) {
      setPassword('')
      setAuthError(cause instanceof Error ? cause.message : 'Не удалось войти.')
      setAuthRevision((value) => value + 1)
    } finally { authBusyRef.current = false; setAuthBusy(false) }
  }

  async function signOut() {
    if (!auth || authBusyRef.current || removingRef.current) return
    authBusyRef.current = true
    authGeneration.current += 1
    setAuthBusy(true)
    setAuthError('')
    try { setAuth(await logout(auth.csrfToken)); setShowLogin(false) }
    catch (cause) {
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) expired()
      else setAuthError(cause instanceof Error ? cause.message : 'Не удалось выйти. Повторите попытку.')
    } finally { authBusyRef.current = false; setAuthBusy(false) }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (removingRef.current) return
    setPage(1)
    setQuery(input.trim())
    setError('')
    // Submitting the same query also retries a failed request.
    setRevision((value) => value + 1)
  }

  async function remove(photo: Photo) {
    if (!canEdit || !auth || removingRef.current || authBusyRef.current) return
    if (!window.confirm(`Удалить фотографию «${photo.title}»?`)) return
    removingRef.current = true
    setRemoving(photo.id)
    setError('')
    try {
      await deletePhoto(photo.id, auth.csrfToken)
      setRevision((value) => value + 1)
    } catch (cause) {
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) expired()
      else setError(cause instanceof Error ? cause.message : 'Не удалось удалить фотографию.')
    } finally { removingRef.current = false; setRemoving(null) }
  }

  return (<>
    <div className="app-shell" inert={editor !== null}>
      <header className="site-header">
        <div className="header-inner">
          <div className="brand"><span className="brand-mark"><Camera size={21} strokeWidth={2.2} aria-hidden="true" /></span><span>Фотоархив</span></div>
          <div className="header-account"><span className="header-caption">{canEdit ? auth.username : 'Система хранения графических изображений'}</span>
            {canEdit ? <button className="secondary-button account-button" disabled={authBusy || removing !== null} onClick={signOut}>{authBusy ? 'Выходим…' : 'Выйти'}</button>
              : <button className="secondary-button account-button" disabled={authBusy} aria-expanded={showLogin} aria-controls="login-panel" onClick={() => { setShowLogin((value) => !value); setPassword('') }}>Войти</button>}
          </div>
        </div>
      </header>
      <main className="main-content">
        {!canEdit && showLogin && <section id="login-panel" className="login-panel" aria-label="Вход в архив">
          <div><h2>Вход в архив</h2><p>Просмотр доступен всем. Для изменений войдите с учётной записью, созданной при запуске. Пароль показан при первом запуске сервиса.</p></div>
          <form onSubmit={signIn} className="login-form" aria-busy={authBusy}>
            <div><label className="field-label" htmlFor="login-username">Имя пользователя</label><input id="login-username" className="text-input" value={username} required autoComplete="username" maxLength={150} disabled={authBusy} onChange={(event) => setUsername(event.target.value)} /></div>
            <div><label className="field-label" htmlFor="login-password">Пароль</label><input id="login-password" className="text-input" type="password" value={password} required autoComplete="current-password" disabled={authBusy} onChange={(event) => setPassword(event.target.value)} /></div>
            <button className="primary-button" type="submit" disabled={authBusy || !auth?.csrfToken}>{authBusy ? 'Входим…' : 'Войти в архив'}</button>
          </form>
        </section>}
        {authStatusError && <div className="inline-error auth-error" role="alert">{authStatusError}<button className="text-button" onClick={() => setAuthRevision((value) => value + 1)}>Повторить проверку входа</button></div>}
        {authError && <div className="inline-error auth-error" role="alert">{authError}</div>}
        <div className="page-heading">
          <div>
            <p className="eyebrow">ВАША КОЛЛЕКЦИЯ</p>
            <h1>Фотографии</h1>
            <p className="page-description">Храните снимки, находите нужное и управляйте коллекцией в одном месте.</p>
          </div>
          {canEdit && <button className="primary-button add-button" disabled={removing !== null || authBusy} onClick={() => setEditor('new')}><Plus size={19} aria-hidden="true" /> Добавить фотографию</button>}
        </div>

        <section className="catalog-panel" aria-label="Каталог фотографий" aria-busy={loading}>
          <div className="catalog-toolbar">
            <div className="section-title"><h2>Каталог</h2><span className="count-badge" aria-label="Всего фотографий">{data?.count ?? '—'}</span></div>
            <form className="search-form" onSubmit={submitSearch} role="search">
              <Search size={18} aria-hidden="true" />
              <input aria-label="Поиск по названию" value={input} maxLength={120} disabled={removing !== null} onChange={(event) => setInput(event.target.value)} placeholder="Поиск по названию" />
              <button type="submit" disabled={removing !== null}>Найти</button>
            </form>
          </div>
          {error && <div className="inline-error" role="alert">{error}</div>}
          {loading ? <div className="status-block" role="status">Загружаем фотографии…</div> : catalogError ? <div className="catalog-failure"><div className="inline-error" role="alert">{catalogError}</div><button className="secondary-button" onClick={() => setRevision((value) => value + 1)}>Повторить загрузку</button></div> : data?.results.length ? (
            <>
              <div className="table-wrap">
                <table>
                  <thead><tr><th scope="col">Изображение</th><th scope="col">Название</th><th scope="col">Добавлено</th><th scope="col" className="actions-heading">Действия</th></tr></thead>
                  <tbody>{data.results.map((photo) => (
                    <tr key={photo.id}>
                      <td><button className="thumb-button" disabled={removing !== null} onClick={() => setEditor(photo)} aria-label={`Открыть ${photo.title}`}><img src={photo.thumbnail_url} alt={`Миниатюра: ${photo.title}`} loading="lazy" /></button></td>
                      <td><div className="photo-name">{photo.title}</div><div className="photo-sub">Фотография #{photo.id}</div></td>
                      <td className="date-cell">{formatDate(photo.created_at)}</td>
                      <td><div className="row-actions"><button className="row-button" disabled={removing !== null} aria-label={`${canEdit ? 'Редактировать' : 'Просмотреть'} ${photo.title}`} onClick={() => setEditor(photo)}><Pencil size={16} aria-hidden="true" /> <span>{canEdit ? 'Изменить' : 'Открыть'}</span></button>{canEdit && <button className="row-button danger" disabled={removing !== null || authBusy} onClick={() => remove(photo)} aria-label={`Удалить ${photo.title}`}><Trash2 size={16} aria-hidden="true" /> <span>{removing === photo.id ? 'Удаление…' : 'Удалить'}</span></button>}</div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <div className="table-footer"><span>Показано {data.results.length} из {data.count}</span><nav className="pager" aria-label="Страницы каталога"><button disabled={!data.previous || removing !== null} onClick={() => setPage((value) => Math.max(1, value - 1))} aria-label="Предыдущая страница"><ChevronLeft size={18} aria-hidden="true" /></button><span aria-current="page">{page}</span><button disabled={!data.next || removing !== null} onClick={() => setPage((value) => value + 1)} aria-label="Следующая страница"><ChevronRight size={18} aria-hidden="true" /></button></nav></div>
            </>
          ) : <div className="empty-state"><div className="empty-icon"><ImageIcon size={27} strokeWidth={1.6} aria-hidden="true" /></div><h3>{query ? 'Ничего не найдено' : 'Пока нет фотографий'}</h3><p>{query ? 'Попробуйте изменить поисковый запрос.' : canEdit ? 'Добавьте первую фотографию, чтобы она появилась в каталоге.' : 'Войдите в архив, чтобы добавить первую фотографию.'}</p>{!query && canEdit && <button className="secondary-button" disabled={removing !== null || authBusy} onClick={() => setEditor('new')}><Plus size={17} aria-hidden="true" /> Добавить первую фотографию</button>}</div>}
        </section>
        <footer className="site-footer">Курсовой проект · Система хранения графических изображений</footer>
      </main>
    </div>
    {editor && <PhotoEditor key={editor === 'new' ? 'new' : editor.id} photo={editor === 'new' ? null : editor} readOnly={!canEdit} csrfToken={auth?.csrfToken ?? ''} onAuthExpired={expired} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); setPage(1); setRevision((value) => value + 1) }} />}
  </>)
}


