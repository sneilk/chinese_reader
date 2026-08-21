/**
 * Значения выделенного слова из локальных словарей.
 *
 * Кэш на время сессии: по одному слову тапают по нескольку раз за главу —
 * имя героя встречается сотнями, — и ходить за ним в базу каждый раз незачем.
 *
 * Ответ приходит быстро (словари в той же базе, точный поиск по индексу),
 * поэтому спиннер здесь не нужен: до ответа панель просто показывает перевод
 * предложения, который уже лежит рядом.
 */

import { useEffect, useState } from 'react'
import { ApiError, api, type Language, type Lookup } from '../api'

// Ключ кэша включает язык: `run` в китайской главе и в английской — разные
// вопросы к разным словарям, и один ответ на оба был бы неверным для обоих.
const cache = new Map<string, Lookup>()

export function useLookup(word: string | null, lang: Language = 'zh'): Lookup | null {
  const [result, setResult] = useState<Lookup | null>(
    word ? (cache.get(`${lang}:${word}`) ?? null) : null,
  )

  useEffect(() => {
    if (!word) {
      setResult(null)
      return
    }

    const key = `${lang}:${word}`
    const cached = cache.get(key)
    if (cached) {
      setResult(cached)
      return
    }

    // Пока ответа нет, старое значение показывать нельзя: это было бы
    // значение прошлого слова под новым заголовком.
    setResult(null)

    let cancelled = false
    api
      .lookup(word, lang)
      .then((found) => {
        cache.set(key, found)
        if (!cancelled) setResult(found)
      })
      .catch((e) => {
        // Молчим: словарь — дополнение к переводу предложения, и его отказ
        // не должен закрывать собой то, что уже показано.
        if (!(e instanceof ApiError)) throw e
      })

    return () => {
      cancelled = true
    }
  }, [word, lang])

  return result
}
