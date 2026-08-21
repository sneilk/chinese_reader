/**
 * Что показывает ридер: оригинал или перевод.
 *
 * Выбор запоминается между главами и переживает перезагрузку. Это не удобство,
 * а следствие того, зачем режим нужен: читатель переключается на перевод, когда
 * устал разбирать, — и на следующей главе он устал ровно так же. Сбрасываться в
 * оригинал на каждой главе значило бы заставлять его нажимать это по десять раз
 * за вечер.
 *
 * Ключ один на всё приложение, а не на главу: это состояние читателя, а не
 * свойство текста.
 */

import { useCallback, useEffect, useState } from 'react'
import type { ReadingMode } from '../api'

const KEY = 'reader-mode'

function stored(): ReadingMode {
  try {
    return localStorage.getItem(KEY) === 'translation' ? 'translation' : 'source'
  } catch {
    // Приватный режим Safari запрещает localStorage целиком. Режим тогда
    // просто не запоминается — это мелочь, а падение ридера мелочью не было бы.
    return 'source'
  }
}

export function useReadingMode(): [ReadingMode, (next: ReadingMode) => void] {
  const [mode, setMode] = useState<ReadingMode>(stored)

  useEffect(() => {
    try {
      localStorage.setItem(KEY, mode)
    } catch {
      // см. выше
    }
  }, [mode])

  const change = useCallback((next: ReadingMode) => setMode(next), [])
  return [mode, change]
}
