/**
 * Последовательная озвучка русского перевода главы.
 *
 * Единица — предложение, как и у перевода: у него уже есть устойчивый адрес
 * (`/api/chapters/{id}/audio/{idx}`), готовый текст и место на экране, которое
 * можно подсветить. Один файл на всю главу нельзя было бы ни перемотать к
 * нужной фразе, ни подсветить.
 *
 * ## Один `<audio>` на всё, и это не экономия
 *
 * iOS разрешает проигрывать звук только с жеста пользователя — и разрешение
 * выдаётся **элементу**, а не странице. Первый `play()` приходит по нажатию
 * кнопки и разблокирует элемент; дальше ему можно менять `src` и запускать
 * снова уже из обработчика `ended`. Создавать новый `Audio` на каждое
 * предложение значило бы каждый раз упираться в запрет заново.
 *
 * ## Следующее предложение подгружается заранее
 *
 * Первый синтез фразы занимает около секунды: SpeechKit её ещё не озвучивал.
 * Без упреждающей загрузки эта секунда становится паузой **между каждой парой
 * фраз**, и слушать это невозможно. Обычный `fetch` наперёд и греет кэш
 * сервера, и кладёт файл в кэш браузера — ответ отдаётся с `cache-control`,
 * поэтому `<audio>` возьмёт его оттуда, а не сходит второй раз.
 *
 * Предложения без перевода пропускаются: озвучивать там нечего, а спотыкаться
 * об них посреди главы — значит останавливать чтение на пустом месте.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { audioUrl, type Chapter } from '../api'

export interface SpeechState {
  /** Идёт ли воспроизведение прямо сейчас. */
  playing: boolean
  /** Номер озвучиваемого предложения; -1 — тишина. */
  current: number
  /** Есть ли вообще что озвучивать: без переводов — нечего. */
  available: boolean
  /** Причина отказа в терминах `error_kind`; `null` — всё в порядке. */
  failure: string | null
  /** Пуск с конкретного предложения — тап по фразе в переводе. */
  playFrom(sentenceIdx: number): void
  /** Пуск с начала или пауза — одна кнопка. */
  toggle(): void
  stop(): void
}

function prefetch(chapterId: number, sentenceIdx: number | undefined): void {
  if (sentenceIdx === undefined) return
  // Ошибку глушим намеренно: это подготовка, а не работа. Если она не
  // удалась, следующая фраза просто прозвучит с задержкой.
  void fetch(audioUrl(chapterId, sentenceIdx)).catch(() => undefined)
}

export function useSpeech(chapter: Chapter | null): SpeechState {
  const [current, setCurrent] = useState(-1)
  const [playing, setPlaying] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const element = useRef<HTMLAudioElement | null>(null)
  const cursor = useRef(-1)

  // Порядок озвучки: только предложения с переводом, в порядке чтения.
  const order = useMemo(
    () => (chapter?.sentences ?? []).filter((s) => s.translation).map((s) => s.idx),
    [chapter?.sentences],
  )

  // Обработчик `ended` подписан на элемент один раз и переживает перерисовки,
  // поэтому очередь он обязан читать из ref, а не из замыкания над состоянием.
  const queue = useRef<{ id: number; order: number[] }>({ id: -1, order: [] })
  useEffect(() => {
    queue.current = { id: chapter?.id ?? -1, order }
  }, [chapter?.id, order])

  const stop = useCallback(() => {
    element.current?.pause()
    cursor.current = -1
    setPlaying(false)
    setCurrent(-1)
  }, [])

  const playAt = useCallback(
    function run(position: number): void {
      const { id, order: queued } = queue.current
      if (id < 0 || position < 0 || position >= queued.length) {
        stop()
        return
      }

      let node = element.current
      if (node === null) {
        node = new Audio()
        element.current = node
      }

      cursor.current = position
      const sentenceIdx = queued[position]
      setCurrent(sentenceIdx)
      setFailure(null)

      node.onended = () => run(cursor.current + 1)
      node.onerror = () => {
        // Отказ синтеза — не повод молча остановиться: читатель нажал кнопку
        // и должен увидеть, почему тишина. Сам `<audio>` кода ответа не
        // показывает, поэтому причину спрашиваем отдельным запросом — один
        // и только на отказе, которого в обычной жизни не бывает.
        setPlaying(false)
        setCurrent(-1)
        cursor.current = -1
        void fetch(audioUrl(id, sentenceIdx))
          .then((r) => r.json())
          .then((body) => setFailure(body?.error?.kind ?? 'speech_failed'))
          .catch(() => setFailure('speech_failed'))
      }

      node.src = audioUrl(id, sentenceIdx)
      node
        .play()
        .then(() => {
          setPlaying(true)
          prefetch(id, queued[position + 1])
        })
        .catch(() => {
          // Браузер отказался играть: обычно это запрет автозапуска, и
          // лечится он повторным нажатием — то есть жестом пользователя.
          setPlaying(false)
        })
    },
    [stop],
  )

  const playFrom = useCallback(
    (sentenceIdx: number) => {
      const position = queue.current.order.indexOf(sentenceIdx)
      // Предложения без перевода в очереди нет — начинаем со следующего за ним.
      playAt(position >= 0 ? position : queue.current.order.findIndex((i) => i > sentenceIdx))
    },
    [playAt],
  )

  const toggle = useCallback(() => {
    const node = element.current
    if (playing && node) {
      node.pause()
      setPlaying(false)
      return
    }
    // Пауза посреди фразы возобновляется с того же места, а не с начала главы.
    if (node && cursor.current >= 0 && !node.ended) {
      void node.play().then(() => setPlaying(true))
      return
    }
    playAt(0)
  }, [playing, playAt])

  // Смена главы обязана остановить звук: иначе предыдущая глава продолжает
  // читаться вслух поверх новой, и подсветка ездит по чужому тексту.
  useEffect(() => {
    return () => {
      element.current?.pause()
      cursor.current = -1
    }
  }, [chapter?.id])

  useEffect(() => {
    setPlaying(false)
    setCurrent(-1)
    setFailure(null)
  }, [chapter?.id])

  return {
    playing,
    current,
    available: order.length > 0,
    failure,
    playFrom,
    toggle,
    stop,
  }
}
