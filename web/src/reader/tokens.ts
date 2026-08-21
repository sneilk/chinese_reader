/**
 * Подготовка главы к рендеру: абзацы, индексы, границы предложений.
 *
 * Всё здесь — чистые функции без React и без DOM: это единственная часть
 * рендера, которую можно проверить, ничего не рисуя.
 *
 * ## Офсеты приезжают в кодовых точках, а не в UTF-16
 *
 * Python индексирует строки по кодовым точкам, JavaScript — по UTF-16.
 * На иероглифах вне BMP (CJK Ext. B и дальше, `𠮷`) одна кодовая точка
 * занимает две единицы JS, и разметка всей главы съезжает — молча, начиная
 * с первого такого знака. В нынешней главе-фикстуре их ноль, поэтому ошибку
 * было бы не видно до первого редкого иероглифа в другой книге.
 *
 * Поэтому офсеты переводятся один раз при подготовке главы. Когда астральных
 * символов нет — а это обычный случай — карта не строится вовсе.
 */

import type { Sentence, Token, TokenKind } from '../api'

export interface TokenRef {
  /** Индекс в исходном массиве токенов: по нему ищется спан в DOM. */
  i: number
  /** Границы в единицах JS-строки, уже пригодные для `content.slice`. */
  start: number
  end: number
  kind: TokenKind
  text: string
}

export interface Paragraph {
  key: number
  tokens: TokenRef[]
}

export interface ChapterIndex {
  paragraphs: Paragraph[]
  tokens: TokenRef[]
  /** Предложение каждого токена; -1 — вне предложений (перевод строки). */
  sentenceOf: Int32Array
  /** Первый и последний токен каждого предложения — границы для расширения. */
  firstToken: Int32Array
  lastToken: Int32Array
  /** Границы предложений в единицах JS-строки. */
  sentenceStart: Int32Array
  sentenceEnd: Int32Array
  /**
   * Номера предложений в каждом абзаце — по ним строится вид «перевод».
   *
   * Считается по токенам, а не разбором текста заново: абзацы и предложения
   * уже разложены здесь, и второй способ узнать то же самое рано или поздно
   * разошёлся бы с первым.
   */
  paragraphSentences: number[][]
}

/**
 * Карта «кодовая точка → индекс UTF-16». `null`, если они совпадают.
 */
export function buildOffsetMap(text: string): Int32Array | null {
  let astral = false
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i)
    if (code >= 0xd800 && code <= 0xdbff) {
      astral = true
      break
    }
  }
  if (!astral) return null

  // +1: нужен и офсет конца строки.
  const map = new Int32Array([...text].length + 1)
  let cp = 0
  for (let i = 0; i < text.length; ) {
    map[cp++] = i
    i += text.codePointAt(i)! > 0xffff ? 2 : 1
  }
  map[cp] = text.length
  return map
}

function mapper(map: Int32Array | null): (offset: number) => number {
  if (map === null) return (offset) => offset
  return (offset) => (offset < map.length ? map[offset] : map[map.length - 1])
}

/** Двоичный поиск предложения, покрывающего офсет. -1, если такого нет. */
function findSentence(starts: Int32Array, ends: Int32Array, offset: number): number {
  let lo = 0
  let hi = starts.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (offset < starts[mid]) hi = mid - 1
    else if (offset >= ends[mid]) lo = mid + 1
    else return mid
  }
  return -1
}

/**
 * Разложить главу на абзацы и построить индексы.
 *
 * Абзац равен строке канона (`normalize.py`), а перевод строки приезжает
 * отдельным токеном — проверено на живой главе: 71 такой токен и ни одного,
 * который пересекал бы границу абзаца или предложения. Поэтому резать можно
 * прямо по токенам, не разбирая текст заново.
 */
export function buildIndex(content: string, tokens: Token[], sentences: Sentence[]): ChapterIndex {
  const map = buildOffsetMap(content)
  const at = mapper(map)

  const sentenceStart = new Int32Array(sentences.length)
  const sentenceEnd = new Int32Array(sentences.length)
  sentences.forEach((s, i) => {
    sentenceStart[i] = at(s.start)
    sentenceEnd[i] = at(s.end)
  })

  const refs: TokenRef[] = []
  const paragraphs: Paragraph[] = []
  let current: TokenRef[] = []

  tokens.forEach(([rawStart, rawEnd, kind], i) => {
    const start = at(rawStart)
    const end = at(rawEnd)
    const text = content.slice(start, end)
    const ref: TokenRef = { i, start, end, kind, text }
    refs.push(ref)

    if (text === '\n') {
      if (current.length) paragraphs.push({ key: paragraphs.length, tokens: current })
      current = []
      return
    }
    current.push(ref)
  })
  if (current.length) paragraphs.push({ key: paragraphs.length, tokens: current })

  const sentenceOf = new Int32Array(refs.length).fill(-1)
  const firstToken = new Int32Array(sentences.length).fill(-1)
  const lastToken = new Int32Array(sentences.length).fill(-1)

  refs.forEach((ref, i) => {
    if (ref.text === '\n') return
    const s = findSentence(sentenceStart, sentenceEnd, ref.start)
    sentenceOf[i] = s
    if (s >= 0) {
      if (firstToken[s] < 0) firstToken[s] = i
      lastToken[s] = i
    }
  })

  const paragraphSentences = paragraphs.map((paragraph) => {
    const found: number[] = []
    for (const token of paragraph.tokens) {
      const s = sentenceOf[token.i]
      // Подряд идущие токены одного предложения дают один номер — берём
      // только смену, поэтому порядок сохраняется без сортировки и Set.
      if (s >= 0 && s !== found[found.length - 1]) found.push(s)
    }
    return found
  })

  return {
    paragraphs,
    tokens: refs,
    sentenceOf,
    firstToken,
    lastToken,
    sentenceStart,
    sentenceEnd,
    paragraphSentences,
  }
}

/**
 * Ограничить протяжку предложением, в котором она началась.
 *
 * Требование segmentation.md §4: без него легко собрать бессвязный кусок из
 * хвоста одной фразы и начала другой.
 */
export function clampToSentence(index: ChapterIndex, anchor: number, focus: number): [number, number] {
  const sentence = index.sentenceOf[anchor]
  if (sentence < 0) return [anchor, anchor]

  let bounded = focus
  if (index.sentenceOf[focus] !== sentence) {
    bounded = focus < anchor ? index.firstToken[sentence] : index.lastToken[sentence]
  }
  return bounded < anchor ? [bounded, anchor] : [anchor, bounded]
}

/** Кликабелен ли токен: запятые и пробелы карточке не нужны. */
export function isSelectable(kind: TokenKind): boolean {
  return kind === 'word' || kind === 'latin' || kind === 'digit'
}

/**
 * Длина в кодовых точках — в тех же единицах, в каких офсеты хранит бэкенд.
 *
 * Наружу (в контекст сохранённого слова) офсеты обязаны уезжать по кодовым
 * точкам, иначе на редком иероглифе они разойдутся с python-стороной ровно
 * так же, как разошлись бы при рендере.
 */
export function codePointLength(text: string): number {
  let count = 0
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i)
    // Ведущий суррогат: пара считается одним знаком.
    if (code >= 0xd800 && code <= 0xdbff) i++
    count++
  }
  return count
}

export interface CharPiece {
  start: number
  end: number
  text: string
}

/**
 * Разрезать токен на символы для режима «долгий тап».
 *
 * По кодовым точкам, а не по единицам UTF-16: половинка суррогатной пары —
 * это не символ, и выделять её нельзя.
 */
export function splitChars(text: string, offset: number): CharPiece[] {
  const pieces: CharPiece[] = []
  let at = offset
  for (const ch of text) {
    pieces.push({ start: at, end: at + ch.length, text: ch })
    at += ch.length
  }
  return pieces
}
