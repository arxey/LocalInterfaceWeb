import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ArrowLeft, ImagePlus, Save, X } from 'lucide-react'
import { ApiError, savePhoto, type Photo } from './api'

interface Props {
  photo: Photo | null
  csrfToken: string
  readOnly: boolean
  onClose: () => void
  onSaved: () => void
  onAuthExpired: () => void
}

export default function PhotoEditor({ photo, csrfToken, readOnly, onClose, onSaved, onAuthExpired }: Props) {
  const [title, setTitle] = useState(photo?.title ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const panelRef = useRef<HTMLElement>(null)
  const titleRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    ;(titleRef.current ?? panelRef.current?.querySelector<HTMLButtonElement>('button'))?.focus()
    return () => {
      document.body.style.overflow = previousOverflow
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  useEffect(() => {
    if (!file) { setPreview(null); return }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  function close() { if (!savingRef.current) onClose() }

  function keyboard(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') { event.preventDefault(); close() }
    if (event.key !== 'Tab') return
    const controls = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]') ?? [])
    const first = controls[0]
    const last = controls[controls.length - 1]
    if (!first) { event.preventDefault(); panelRef.current?.focus(); return }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (savingRef.current || readOnly) return
    setError('')
    if (!title.trim()) { setError('Введите название фотографии.'); titleRef.current?.focus(); return }
    if (!photo && !file) { setError('Выберите изображение.'); return }
    try {
      savingRef.current = true
      setSaving(true)
      await savePhoto(title.trim(), file, photo?.id, csrfToken)
      onSaved()
    } catch (cause) {
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) onAuthExpired()
      else setError(cause instanceof Error ? cause.message : 'Не удалось сохранить фотографию.')
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  function selectFile(input: HTMLInputElement) {
    const selected = input.files?.[0]
    if (!selected) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(selected.type) || selected.size > 10 * 1024 * 1024) {
      setError('Выберите JPEG, PNG или WebP размером до 10 МБ.')
      input.value = ''
      return
    }
    setFile(selected)
    setError('')
  }

  return (
    <div className="editor-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
      <section ref={panelRef} className="editor-panel" role="dialog" aria-modal="true" aria-label={readOnly ? 'Просмотр фотографии' : photo ? 'Редактирование фотографии' : 'Новая фотография'} tabIndex={-1} onKeyDown={keyboard}>
        <div className="editor-topbar">
          <button type="button" className="text-button" onClick={close} disabled={saving}><ArrowLeft size={18} aria-hidden="true" /> К каталогу</button>
          <button type="button" className="icon-button" aria-label="Закрыть" onClick={close} disabled={saving}><X size={20} aria-hidden="true" /></button>
        </div>
        <div className="editor-scroll">
          <p className="eyebrow">ФОТОАРХИВ / {readOnly ? 'ПРОСМОТР' : photo ? 'РЕДАКТИРОВАНИЕ' : 'ДОБАВЛЕНИЕ'}</p>
          <h2>{readOnly ? photo?.title : photo ? 'Редактирование фотографии' : 'Новая фотография'}</h2>
          <p className="editor-intro">{readOnly ? 'Для изменения коллекции войдите в архив.' : 'Укажите название и выберите изображение. В каталоге будет показана его миниатюра.'}</p>
          <form onSubmit={submit} aria-busy={saving}>
            {!readOnly && <>
              <label className="field-label" htmlFor="photo-title">Название фотографии</label>
              <input ref={titleRef} id="photo-title" className="text-input" value={title} maxLength={120} disabled={saving} onChange={(event) => setTitle(event.target.value)} placeholder="Например, Вечерний город" />
              <label className="field-label image-label" htmlFor="photo-image">Изображение</label>
              <label className={`upload-area${saving ? ' is-disabled' : ''}`} htmlFor="photo-image">
                <ImagePlus size={25} strokeWidth={1.7} aria-hidden="true" />
                <strong>{file ? file.name : photo ? 'Выбрать другое изображение' : 'Выбрать изображение'}</strong>
                <span>JPEG, PNG или WebP, до 10 МБ</span>
              </label>
              <input id="photo-image" className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" disabled={saving} onChange={(event) => selectFile(event.target)} />
            </>}
            {(preview || photo?.image_url) && <div className="preview-block">
              <div className="preview-heading"><span>Просмотр изображения</span><span>{preview ? 'Новое изображение' : 'Исходное изображение'}</span></div>
              <div className="preview-frame"><img src={preview ?? photo?.image_url} alt={`${title || photo?.title} — полное изображение`} /></div>
            </div>}
            {error && <p role="alert" className="error-message">{error}</p>}
            {!readOnly && <div className="form-actions">
              <button type="button" className="secondary-button" onClick={close} disabled={saving}>Отмена</button>
              <button type="submit" className="primary-button" disabled={saving}><Save size={17} aria-hidden="true" /> {saving ? 'Сохранение…' : 'Сохранить'}</button>
            </div>}
            {saving && <p className="save-status" role="status">Дождитесь завершения сохранения.</p>}
          </form>
        </div>
      </section>
    </div>
  )
}
