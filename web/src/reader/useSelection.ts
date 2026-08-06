/**
 * Состояние выделения: что сейчас выбрано и чем это станет для карточки.
 *
 * Правила взаимодействия — segmentation.md §4: тап выделяет токен целиком,
 * протяжка расширяет выделение по границам токенов, долгий тап сужает до
 * одного символа. Расширение **не пересекает границу предложения**: иначе
 * легко собрать бессвязный кусок из хвоста одной фразы и начала другой.
 */

import { useCallback, useMemo, useState } from 'react'
import type { CharSplit, TokenRange } from './ChapterText'
import type { ChapterIndex } from './tokens'
import { clampToSentence, isSelectable } from './tokens'

/** То, что выделено, — в терминах текста, а не индексов. Контракт для карточки. */
export interface Selected {
  /** Офсеты в единицах JS-строки: `content.slice(start, end)` вернёт `text`. */
  start: number
  end: number
  text: string
  /** Индекс предложения, в котором это выделено; -1 — вне предложений. */
  sentence: number
  granularity: 'token' | 'phrase' | 'char'
}

export interface SelectionState {
  selection: TokenRange | null
  charSplit: CharSplit | null
  selected: Selected | null
  onTap(tokenIndex: number): void
  onExtend(anchorIndex: number, focusIndex: number): void
  onCharacter(tokenIndex: number, charOffset: number): void
  onStep(direction: -1 | 1): void
  clear(): void
}

export function useSelection(index: ChapterIndex, content: string): SelectionState {
  const [selection, setSelection] = useState<TokenRange | null>(null)
  const [charSplit, setCharSplit] = useState<CharSplit | null>(null)

  const onTap = useCallback(
    (tokenIndex: number) => {
      const token = index.tokens[tokenIndex]
      if (!token || !isSelectable(token.kind)) return
      setCharSplit(null)
      setSelection({ from: tokenIndex, to: tokenIndex })
    },
    [index],
  )

  const onExtend = useCallback(
    (anchorIndex: number, focusIndex: number) => {
      const [from, to] = clampToSentence(index, anchorIndex, focusIndex)
      setCharSplit(null)
      setSelection({ from, to })
    },
    [index],
  )

  const onCharacter = useCallback(
    (tokenIndex: number, charOffset: number) => {
      const token = index.tokens[tokenIndex]
      if (!token) return
      setSelection({ from: tokenIndex, to: tokenIndex })
      setCharSplit({ tokenIndex, charOffset })
    },
    [index],
  )

  /** Клавиатурный шаг: следующий выделяемый токен, не выходя за предложение. */
  const onStep = useCallback(
    (direction: -1 | 1) => {
      setCharSplit(null)
      setSelection((current) => {
        const start = current ? (direction > 0 ? current.to : current.from) : -1
        for (let i = start + direction; i >= 0 && i < index.tokens.length; i += direction) {
          if (isSelectable(index.tokens[i].kind)) return { from: i, to: i }
        }
        return current
      })
    },
    [index],
  )

  const clear = useCallback(() => {
    setSelection(null)
    setCharSplit(null)
  }, [])

  const selected = useMemo<Selected | null>(() => {
    if (charSplit) {
      const piece = content.slice(charSplit.charOffset, charSplit.charOffset + 1)
      // Суррогатная пара: символ занимает две единицы JS-строки.
      const size = piece.codePointAt(0)! > 0xffff ? 2 : 1
      return {
        start: charSplit.charOffset,
        end: charSplit.charOffset + size,
        text: content.slice(charSplit.charOffset, charSplit.charOffset + size),
        sentence: index.sentenceOf[charSplit.tokenIndex] ?? -1,
        granularity: 'char',
      }
    }

    if (!selection) return null
    const first = index.tokens[selection.from]
    const last = index.tokens[selection.to]
    if (!first || !last) return null

    return {
      start: first.start,
      end: last.end,
      text: content.slice(first.start, last.end),
      sentence: index.sentenceOf[selection.from] ?? -1,
      granularity: selection.from === selection.to ? 'token' : 'phrase',
    }
  }, [selection, charSplit, index, content])

  return { selection, charSplit, selected, onTap, onExtend, onCharacter, onStep, clear }
}
