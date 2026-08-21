/**
 * Глава в переводе: тот же текст, но по-русски.
 *
 * Абзацы и предложения берутся из того же индекса, что и оригинал, поэтому
 * два вида — это буквально одна разметка в двух проекциях. Абзац в переводе
 * стоит там же, где в оригинале, и переключение режима не сдвигает текст под
 * пальцем.
 *
 * **Предложение — кликабельная единица.** Тап по фразе запускает озвучку с
 * неё: это тот же жест, каким в оригинале открывают карточку слова, и
 * означает он то же самое — «вот про это место».
 *
 * Кнопкой предложение при этом **не объявляется**. В главе их полторы сотни, и
 * `role="button"` с `tabindex` превратил бы Tab в путешествие через весь текст
 * до кнопки «следующая глава», а чтение с экрана — в перечисление «кнопка,
 * кнопка, кнопка» перед каждой фразой. Клавиатурный путь к озвучке остаётся в
 * полосе управления, где ему и место: там одна кнопка на всю главу.
 *
 * **Дырок не бывает.** Если перевода предложения нет (частичный отказ
 * переводчика — штатное состояние, RFC §4), на его месте стоит оригинал с
 * пометкой. Пропустить фразу значило бы молча укоротить главу, а показать
 * пустоту — выдать сбой за конец абзаца.
 *
 * ## Место в массиве и номер предложения — разные числа
 *
 * Индексы главы (`sentenceStart`, `paragraphSentences`) считаны по **месту** в
 * массиве предложений, а озвучка адресуется по `sentence.idx` — номеру,
 * который знает бэкенд. Обычно они совпадают, и именно поэтому их легко
 * перепутать: разъехавшись однажды, они дали бы озвучку соседней фразы под
 * подсветкой этой. Здесь они разведены явно и сходятся только на границе.
 *
 * Как и в оригинале, абзацы мемоизированы поодиночке: во время озвучки
 * подсветка переезжает по предложениям, и перерисовывать из-за этого всю
 * главу незачем — меняются два абзаца из семидесяти.
 */

import { memo } from 'react'
import type { Language, Sentence } from '../api'
import type { ChapterIndex } from './tokens'

interface Piece {
  /** Место в массиве предложений — по нему берутся офсеты из индекса. */
  position: number
  /** Номер предложения на бэкенде — по нему запрашивается озвучка. */
  sentenceIdx: number
  text: string
  /** Перевода нет — показан оригинал. */
  raw: boolean
}

const ParagraphView = memo(function ParagraphView({
  pieces,
  speaking,
  lang,
  onPlayFrom,
}: {
  pieces: Piece[]
  /** Озвучиваемое предложение, если оно в этом абзаце; иначе -1. */
  speaking: number
  lang: Language
  onPlayFrom: (sentenceIdx: number) => void
}) {
  return (
    <p className="reader__p">
      {pieces.map((piece) => (
        <span
          className={`sn${piece.raw ? ' sn--raw' : ''}${
            piece.sentenceIdx === speaking ? ' is-speaking' : ''
          }`}
          key={piece.position}
          lang={piece.raw ? lang : 'ru'}
          title="Читать вслух с этой фразы"
          aria-current={piece.sentenceIdx === speaking ? 'true' : undefined}
          onClick={() => onPlayFrom(piece.sentenceIdx)}
        >
          {piece.text}{' '}
        </span>
      ))}
    </p>
  )
})

export function TranslationText({
  index,
  content,
  sentences,
  lang,
  speaking,
  onPlayFrom,
}: {
  index: ChapterIndex
  content: string
  sentences: Sentence[]
  lang: Language
  /** Номер (`idx`) озвучиваемого предложения; -1 — тишина. */
  speaking: number
  onPlayFrom: (sentenceIdx: number) => void
}) {
  return (
    <div className="reader reader--translation" lang="ru" role="article" aria-label="Перевод главы">
      {index.paragraphs.map((paragraph, i) => {
        const pieces: Piece[] = []
        for (const position of index.paragraphSentences[i] ?? []) {
          const sentence = sentences[position]
          if (!sentence) continue
          pieces.push({
            position,
            sentenceIdx: sentence.idx,
            text:
              sentence.translation ??
              content.slice(index.sentenceStart[position], index.sentenceEnd[position]),
            raw: sentence.translation === null,
          })
        }
        if (pieces.length === 0) return null

        return (
          <ParagraphView
            key={paragraph.key}
            pieces={pieces}
            speaking={pieces.some((p) => p.sentenceIdx === speaking) ? speaking : -1}
            lang={lang}
            onPlayFrom={onPlayFrom}
          />
        )
      })}
    </div>
  )
}
