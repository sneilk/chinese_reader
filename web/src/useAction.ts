/**
 * Действие к API: что сейчас идёт и чем кончилось прошлое.
 *
 * Экран чтения дорос до четырёх действий — дозалить перевод, открыть
 * следующую главу, загрузить пачку вперёд, перезапросить ссылку, — и каждое
 * тащило за собой один и тот же кусок: поднять флаг занятости, обнулить
 * отказ, `try`, привести пойманное к `ApiError`, `finally` опустить флаг.
 * Приведение отказа было переписано двенадцать раз в шести файлах.
 *
 * ## Одна машина на экран, а не по одной на действие
 *
 * Соблазн завести отдельное состояние каждому действию есть, но он ломает то,
 * как это работает сейчас: отказ на экране один, и запуск **любого** действия
 * его гасит. С отдельными состояниями отказ неудавшейся перессылки продолжал
 * бы висеть над экраном, пока рядом успешно грузятся главы, — и читатель
 * чинил бы то, что уже починилось.
 *
 * Поэтому занятость хранится не флагом, а ключом действия: кнопкам нужно
 * различать «перевожу» и «загружаю», а состояние при этом остаётся одно.
 */

import { useCallback, useRef, useState } from 'react'
import { ApiError, asApiError } from './api'

export interface Action<K extends string> {
  /** Ключ идущего действия. `null` — ничего не идёт. */
  busy: K | null
  /** Идёт ли хоть что-нибудь: этим гасятся кнопки, которым нельзя мешать. */
  working: boolean
  /** Чем кончилось последнее действие. Гаснет при запуске следующего. */
  error: ApiError | null
  run: (key: K, task: () => Promise<void>) => Promise<void>
  clear: () => void
}

export function useAction<K extends string = string>(): Action<K> {
  const [busy, setBusy] = useState<K | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  // Занятость держится ещё и здесь: `busy` из замыкания на момент нажатия
  // устарел, а проверять надо то, что происходит сейчас. Кнопки на это время
  // погашены, но нажать их можно и с клавиатуры, и двойным тапом.
  const inFlight = useRef<K | null>(null)

  const clear = useCallback(() => setError(null), [])

  const run = useCallback(async (key: K, task: () => Promise<void>) => {
    if (inFlight.current !== null) return
    inFlight.current = key
    setBusy(key)
    setError(null)
    try {
      await task()
    } catch (cause) {
      setError(asApiError(cause))
    } finally {
      inFlight.current = null
      setBusy(null)
    }
  }, [])

  return { busy, working: busy !== null, error, run, clear }
}
