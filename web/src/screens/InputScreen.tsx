/**
 * Экран ввода URL главы.
 *
 * Здесь каркас (T1.13): поле, отправка, переход к чтению. Опрос статуса и
 * человеческие сообщения по каждому `error_kind` — задача T1.14.
 */

import { useState } from 'react'
import { ApiError, api } from '../api'
import { navigate } from '../router'

export function InputScreen() {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!url.trim() || busy) return

    setBusy(true)
    setError(null)
    try {
      const accepted = await api.createChapter(url.trim())
      navigate({ name: 'chapter', id: accepted.id })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1>Читать главу</h1>
      <form onSubmit={submit}>
        <div className="field">
          <label className="label" htmlFor="url">
            Адрес главы
          </label>
          <input
            id="url"
            className="input"
            type="url"
            inputMode="url"
            autoComplete="url"
            placeholder="https://51shucheng.net/…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>

        {error && (
          <div className="note note--error" role="alert">
            <div className="note__title">Не получилось</div>
            <p className="note__detail">{error}</p>
          </div>
        )}

        <button className="button" type="submit" disabled={busy || !url.trim()}>
          {busy ? 'Загружаю…' : 'Загрузить'}
        </button>
      </form>
    </>
  )
}
