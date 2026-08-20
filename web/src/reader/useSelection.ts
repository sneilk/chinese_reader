/**
 * Состояние выделения: что сейчас выбрано и насколько подробно это показано.
 *
 * Правила взаимодействия — segmentation.md §4: тап выделяет токен целиком,
 * протяжка расширяет выделение по границам токенов, сужение до одного символа
 * доступно в панели. Расширение **не пересекает границу предложения**: иначе
 * легко собрать бессвязный кусок из хвоста одной фразы и начала другой.
 *
 * Выделение и показ разведены намеренно. Выделить — дёшево и обратимо, поэтому
 * это делает любой жест. Показать — либо подсказка у пальца (`peek`), либо
 * панель на треть экрана (`panel`), и второе стоит отдельного двойного тапа.
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import type { CharSplit, TokenRange } from './ChapterText'
import type { ChapterIndex } from './tokens'
import { clampToSentence, codePointLength, isSelectable } from './tokens'

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

/** Что показано поверх текста. Координаты подсказки — в единицах вьюпорта. */
export type ReaderView =
  | { kind: 'none' }
  | { kind: 'peek'; x: number; y: number }
  | { kind: 'panel' }

const HIDDEN: ReaderView = { kind: 'none' }
const PANEL: ReaderView = { kind: 'panel' }

/**
 * Контекст выделения для сохранения в словарь: предложение целиком и офсеты
 * слова внутри него — в кодовых точках, как их хранит бэкенд.
 */
export function contextOf(
  index: ChapterIndex,
  content: string,
  selected: Selected,
): { sentence: string; offset_start: number; offset_end: number } | null {
  const i = selected.sentence
  if (i < 0 || i >= index.sentenceStart.length) return null

  const from = index.sentenceStart[i]
  const sentence = content.slice(from, index.sentenceEnd[i])
  const start = codePointLength(content.slice(from, selected.start))
  return {
    sentence,
    offset_start: start,
    offset_end: start + codePointLength(selected.text),
  }
}

export interface SelectionState {
  selection: TokenRange | null
  charSplit: CharSplit | null
  selected: Selected | null
  view: ReaderView
  onTap(tokenIndex: number): void
  onOpen(tokenIndex: number): void
  onExtend(anchorIndex: number, focusIndex: number): void
  onExtendEnd(): void
  onPeek(tokenIndex: number, x: number, y: number): void
  onPeekEnd(): void
  /** Сузить до символа внутри выделенного слова; `null` — вернуться к слову. */
  onNarrow(charOffset: number | null): void
  onStep(direction: -1 | 1): void
  clear(): void
}

export function useSelection(index: ChapterIndex, content: string): SelectionState {
  const [selection, setSelection] = useState<TokenRange | null>(null)
  const [charSplit, setCharSplit] = useState<CharSplit | null>(null)
  const [view, setView] = useState<ReaderView>(HIDDEN)

  // Зеркало выделения для обработчиков. Читать его из состояния нельзя:
  // `onExtendEnd` приходит из нативного слушателя, который подписан один раз
  // на главу и видел бы `selection` таким, каким тот был при подписке.
  const range = useRef<TokenRange | null>(null)
  const put = useCallback((next: TokenRange | null) => {
    range.current = next
    setSelection(next)
  }, [])

  const select = useCallback(
    (tokenIndex: number, next: ReaderView) => {
      const token = index.tokens[tokenIndex]
      if (!token || !isSelectable(token.kind)) return
      setCharSplit(null)
      put({ from: tokenIndex, to: tokenIndex })
      setView(next)
    },
    [index, put],
  )

  const onTap = useCallback((tokenIndex: number) => select(tokenIndex, HIDDEN), [select])
  const onOpen = useCallback((tokenIndex: number) => select(tokenIndex, PANEL), [select])

  const onPeek = useCallback(
    (tokenIndex: number, x: number, y: number) => select(tokenIndex, { kind: 'peek', x, y }),
    [select],
  )

  const onPeekEnd = useCallback(() => {
    // Подсветка остаётся: по ней видно, что именно только что смотрели, и
    // двойной тап по тому же слову откроет панель уже прицельно.
    setView((current) => (current.kind === 'peek' ? HIDDEN : current))
  }, [])

  const onExtend = useCallback(
    (anchorIndex: number, focusIndex: number) => {
      const [from, to] = clampToSentence(index, anchorIndex, focusIndex)
      setCharSplit(null)
      put({ from, to })
      setView(HIDDEN)
    },
    [index, put],
  )

  const onExtendEnd = useCallback(() => {
    // Панель открывает только настоящая фраза. Дрогнувший на одном токене
    // палец — это тап, а тап теперь ничего не открывает: иначе порог в 10
    // пикселей стал бы границей между «тихо» и «панель на треть экрана».
    const current = range.current
    if (current && current.from !== current.to) setView(PANEL)
  }, [])

  const onNarrow = useCallback((charOffset: number | null) => {
    const current = range.current
    if (!current) return
    setCharSplit(charOffset === null ? null : { tokenIndex: current.from, charOffset })
  }, [])

  /** Клавиатурный шаг: следующий выделяемый токен, не выходя за предложение. */
  const onStep = useCallback(
    (direction: -1 | 1) => {
      const current = range.current
      const from = current ? (direction > 0 ? current.to : current.from) : -1
      for (let i = from + direction; i >= 0 && i < index.tokens.length; i += direction) {
        if (!isSelectable(index.tokens[i].kind)) continue
        setCharSplit(null)
        put({ from: i, to: i })
        // С клавиатуры подсказку у пальца не показать, да и незачем: шаг по
        // словам — это разбор текста, а не беглое чтение.
        setView(PANEL)
        return
      }
    },
    [index, put],
  )

  const clear = useCallback(() => {
    put(null)
    setCharSplit(null)
    setView(HIDDEN)
  }, [put])

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

  return {
    selection,
    charSplit,
    selected,
    view,
    onTap,
    onOpen,
    onExtend,
    onExtendEnd,
    onPeek,
    onPeekEnd,
    onNarrow,
    onStep,
    clear,
  }
}
