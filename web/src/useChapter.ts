/**
 * Загрузка главы с опросом статуса.
 *
 * Опрос нужен потому, что загрузка асинхронная: `POST` отвечает раньше, чем
 * текст появляется в базе (RFC §4). Как только конвейер дошёл до конечного
 * состояния, опрос прекращается сам — таймер не должен жить дольше работы.
 *
 * Хук общий для экрана ввода и экрана чтения: на первом он показывает
 * прогресс, на втором — держит статус свежим, пока идёт перевод.
 *
 * ## Отказ опроса — не то же самое, что отказ загрузки
 *
 * Разница видна только на экране чтения, зато там она решает всё. Первая
 * загрузка упала — показывать нечего, и сообщение об отказе занимает весь
 * экран законно. Упал **фоновый опрос** у главы, которая уже открыта и
 * читается, — терять при этом нечего: текст, разметка и прокрутка на месте.
 * Поэтому `requestError` живёт отдельно от `chapter`, и решает экран, а не
 * хук: `chapter === null` — беда, иначе просто строка сверху.
 *
 * ## Цепочка опроса не должна рваться о первый же сбой
 *
 * Опрос держится на том, что смена `chapter` перезапускает эффект. Неудачный
 * запрос главу не меняет — и цепочка обрывалась навсегда: телефон ушёл в сон,
 * вайфай моргнул, и глава больше никогда не узнает, что перевод готов.
 * Отсюда счётчик: он двигается после **любого** ответа, удачного и нет.
 *
 * Пауза при этом растёт, пока не получается. Долбить мёртвый сервер раз в
 * полторы секунды с телефона — это разряженная батарея и ничего больше.
 *
 * ## Ответ на прошлую главу не должен лечь на текущую
 *
 * Между «нажал следующую главу» и ответом на предыдущий опрос — обычные
 * полсекунды. Без пометки поколения опоздавший ответ перезаписывал состояние,
 * и в адресе была тринадцатая глава, а на экране двенадцатая.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, asApiError, api, isPending, type Chapter } from './api'

const POLL_MS = 1500
/** Во сколько раз пауза может вырасти, пока опрос не удаётся. */
const MAX_BACKOFF = 8

/**
 * Пауза до следующего опроса. Растёт, пока запросы не удаются.
 *
 * Вынесена из хука затем, что это единственная его часть, которую можно
 * проверить без браузера. Ошибка здесь тихая в обе стороны: не вырастет —
 * телефон будет долбить мёртвый сервер раз в полторы секунды, вырастет без
 * потолка — читатель не дождётся готового перевода.
 */
export function pollDelay(failures: number): number {
  return POLL_MS * Math.min(2 ** Math.max(0, failures), MAX_BACKOFF)
}

export interface ChapterState {
  chapter: Chapter | null
  /**
   * Отказ самого запроса. Смотреть его надо вместе с `chapter`: при пустой
   * главе это «показать нечего», при заполненной — «свежесть не подтвердилась».
   */
  requestError: ApiError | null
  loading: boolean
  reload: () => Promise<void>
}

export function useChapter(id: number | null): ChapterState {
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [requestError, setRequestError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(id !== null)
  // Двигается после каждого ответа и только затем, чтобы перезапустить эффект
  // опроса: сам по себе он ничего не значит.
  const [tick, setTick] = useState(0)
  const failures = useRef(0)
  // Поколение запроса. Растёт при смене главы, и ответ из прошлого поколения
  // выбрасывается: он про другую главу.
  const generation = useRef(0)

  const reload = useCallback(async () => {
    if (id === null) return
    const mine = generation.current
    try {
      const got = await api.getChapter(id)
      if (mine !== generation.current) return
      setChapter(got)
      setRequestError(null)
      failures.current = 0
    } catch (e) {
      if (mine !== generation.current) return
      setRequestError(asApiError(e))
      failures.current += 1
    } finally {
      if (mine === generation.current) setLoading(false)
    }
  }, [id])

  useEffect(() => {
    generation.current += 1
    failures.current = 0
    setChapter(null)
    setRequestError(null)
    setLoading(id !== null)
    void reload()
  }, [id, reload])

  useEffect(() => {
    if (!chapter || !isPending(chapter.status)) return

    const timer = setTimeout(() => {
      // Счётчик двигается после ответа, а не до: иначе эффект перезапустится
      // раньше, чем приедет состояние, и опросы пойдут чаще срока.
      void reload().finally(() => setTick((n) => n + 1))
    }, pollDelay(failures.current))
    return () => clearTimeout(timer)
  }, [chapter, tick, reload])

  return { chapter, requestError, loading, reload }
}
