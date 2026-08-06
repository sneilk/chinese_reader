/**
 * Экран чтения главы.
 *
 * Здесь каркас (T1.13): загрузка главы, опрос, пока идёт работа, и показ
 * текста. Рендер по токенам со спанами и жестами — T1.15, панель перевода
 * предложения — T1.16.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, isPending, isReadable, type Chapter } from '../api'

const POLL_MS = 1500

export function ReaderScreen({ id }: { id: number }) {
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setChapter(await api.getChapter(id))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [id])

  useEffect(() => {
    void load()
  }, [load])

  // Пока конвейер работает, статус опрашивается: загрузка главы асинхронная,
  // и ответ на POST приходит раньше, чем текст появляется в базе.
  useEffect(() => {
    if (!chapter || !isPending(chapter.status)) return
    const timer = setTimeout(() => void load(), POLL_MS)
    return () => clearTimeout(timer)
  }, [chapter, load])

  if (error) {
    return (
      <div className="note note--error" role="alert">
        <div className="note__title">Глава не открылась</div>
        <p className="note__detail">{error}</p>
      </div>
    )
  }

  if (!chapter) {
    return <p className="muted">Загружаю…</p>
  }

  return (
    <>
      {chapter.title && <h1 className="reader__title">{chapter.title}</h1>}

      {chapter.error && (
        <div className="note note--warning" role="status">
          <div className="note__title">{chapter.error.kind}</div>
          <p className="note__detail">{chapter.error.message}</p>
        </div>
      )}

      {isPending(chapter.status) && <p className="muted">Статус: {chapter.status}…</p>}

      {isReadable(chapter.status) && chapter.content ? (
        <div className="reader" lang="zh-Hans">
          {/* Абзац равен строке канона (normalize.py), поэтому делим по \n.
              В T1.15 внутри абзацев появятся спаны по токенам. */}
          {chapter.content.split('\n').map((paragraph, i) => (
            <p className="reader__p" key={i}>
              {paragraph}
            </p>
          ))}
        </div>
      ) : (
        !isPending(chapter.status) && <p className="muted">Текста пока нет.</p>
      )}
    </>
  )
}
