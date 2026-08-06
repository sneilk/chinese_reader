/**
 * Загрузка главы с опросом статуса.
 *
 * Опрос нужен потому, что загрузка асинхронная: `POST` отвечает раньше, чем
 * текст появляется в базе (RFC §4). Как только конвейер дошёл до конечного
 * состояния, опрос прекращается сам — таймер не должен жить дольше работы.
 *
 * Хук общий для экрана ввода и экрана чтения: на первом он показывает
 * прогресс, на втором — держит статус свежим, пока идёт перевод.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, isPending, type Chapter } from './api'

const POLL_MS = 1500

export interface ChapterState {
  chapter: Chapter | null
  /** Ошибка самого запроса: сервер лёг, главы нет в базе. */
  requestError: ApiError | null
  loading: boolean
  reload: () => Promise<void>
}

export function useChapter(id: number | null): ChapterState {
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [requestError, setRequestError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(id !== null)

  const reload = useCallback(async () => {
    if (id === null) return
    try {
      setChapter(await api.getChapter(id))
      setRequestError(null)
    } catch (e) {
      setRequestError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    setChapter(null)
    setRequestError(null)
    setLoading(id !== null)
    void reload()
  }, [id, reload])

  useEffect(() => {
    if (!chapter || !isPending(chapter.status)) return
    const timer = setTimeout(() => void reload(), POLL_MS)
    return () => clearTimeout(timer)
  }, [chapter, reload])

  return { chapter, requestError, loading, reload }
}
