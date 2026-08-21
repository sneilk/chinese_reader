/**
 * Текст главы, разложенный по токенам.
 *
 * Два решения определяют, будет ли это тормозить на телефоне.
 *
 * **Спаны рендерятся один раз.** Их около 2500, и если подсветка выделения
 * поедет через состояние React, каждый тап будет пересобирать всю главу.
 * Поэтому выделение проставляется классами напрямую в DOM: меняются только
 * те спаны, что вошли в диапазон или вышли из него.
 *
 * **Абзацы мемоизированы поодиночке.** Единственное, что меняет разметку, —
 * режим «долгий тап», где активный токен разбивается на символы. Перерисуется
 * при этом один абзац из семидесяти, а не глава целиком.
 *
 * По той же причине подсветка озвучиваемого предложения красит спаны напрямую,
 * а не через состояние: во время чтения вслух она переезжает каждые несколько
 * секунд, и пересборка главы на каждый переезд свела бы всю эту экономию на нет.
 */

import { memo, useEffect, useRef } from 'react'
import type { Language } from '../api'
import type { ChapterIndex, TokenRef } from './tokens'
import { splitChars } from './tokens'

/** Диапазон выделения в индексах токенов. */
export interface TokenRange {
  from: number
  to: number
}

export interface CharSplit {
  tokenIndex: number
  /** Выделенный символ, в единицах JS-строки. */
  charOffset: number
}

const SELECTED = 'is-selected'
const SPEAKING = 'is-speaking'

/** Снять класс со всех спанов, покрашенных в прошлый раз. */
function repaint(painted: React.RefObject<Element[]>, next: Element[], className: string): void {
  for (const element of painted.current) element.classList.remove(className)
  for (const element of next) element.classList.add(className)
  painted.current = next
}

function spansOf(node: HTMLElement, range: TokenRange | null): Element[] {
  if (!range) return []
  const found: Element[] = []
  for (let i = range.from; i <= range.to; i++) {
    const span = node.querySelector(`[data-i="${i}"]`)
    if (span) found.push(span)
  }
  return found
}

const TokenSpan = memo(function TokenSpan({
  token,
  split,
}: {
  token: TokenRef
  split: number | null
}) {
  if (split === null) {
    return (
      <span className="tk" data-i={token.i} data-s={token.start} data-e={token.end}>
        {token.text}
      </span>
    )
  }

  return (
    <span className="tk tk--split" data-i={token.i} data-s={token.start} data-e={token.end}>
      {splitChars(token.text, token.start).map((piece) => (
        <span className="ch" data-c={piece.start} key={piece.start}>
          {piece.text}
        </span>
      ))}
    </span>
  )
})

const ParagraphView = memo(function ParagraphView({
  tokens,
  splitToken,
}: {
  tokens: TokenRef[]
  splitToken: number
}) {
  return (
    <p className="reader__p">
      {tokens.map((token) => (
        <TokenSpan key={token.i} token={token} split={token.i === splitToken ? token.i : null} />
      ))}
    </p>
  )
})

export function ChapterText({
  index,
  selection,
  charSplit,
  speaking,
  lang,
  containerRef,
}: {
  index: ChapterIndex
  selection: TokenRange | null
  charSplit: CharSplit | null
  /** Токены озвучиваемого предложения; `null` — тишина. */
  speaking: TokenRange | null
  lang: Language
  containerRef: React.RefObject<HTMLDivElement | null>
}) {
  const painted = useRef<Element[]>([])
  const spoken = useRef<Element[]>([])

  useEffect(() => {
    const node = containerRef.current
    if (!node) return

    if (charSplit) {
      const ch = node.querySelector(`[data-c="${charSplit.charOffset}"]`)
      repaint(painted, ch ? [ch] : [], SELECTED)
      return
    }
    repaint(painted, spansOf(node, selection), SELECTED)
  }, [selection, charSplit, containerRef, index])

  useEffect(() => {
    const node = containerRef.current
    if (!node) return
    repaint(spoken, spansOf(node, speaking), SPEAKING)
  }, [speaking, containerRef, index])

  return (
    <div
      className="reader"
      lang={lang === 'zh' ? 'zh-Hans' : 'en'}
      ref={containerRef}
      tabIndex={0}
      role="article"
      aria-label="Текст главы"
    >
      {index.paragraphs.map((paragraph) => (
        <ParagraphView
          key={paragraph.key}
          tokens={paragraph.tokens}
          splitToken={charSplit ? charSplit.tokenIndex : -1}
        />
      ))}
    </div>
  )
}
