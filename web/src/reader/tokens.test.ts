/**
 * Подготовка главы к рендеру.
 *
 * Здесь проверяется единственное место фронта, где ошибка **не видна**.
 * Бэкенд считает офсеты в кодовых точках, JavaScript — в единицах UTF-16, и на
 * иероглифе вне BMP одна кодовая точка занимает две единицы. Не переведи
 * офсеты, и разметка всей главы съедет — начиная с первого такого знака и
 * молча: спаны отрисуются, жесты сработают, просто подсветится и уедет в
 * словарь соседнее слово.
 *
 * В нынешней главе-фикстуре астральных знаков ноль, поэтому в браузере такую
 * поломку было бы не увидеть до первого редкого иероглифа в другой книге.
 * Тесты — единственное место, где её можно поймать заранее, поэтому почти все
 * они здесь именно про неё.
 */

import { describe, expect, it } from 'vitest'
import type { Sentence, Token } from '../api'
import {
  buildIndex,
  buildOffsetMap,
  clampToSentence,
  codePointLength,
  isSelectable,
  splitChars,
} from './tokens'

/**
 * Глава с астральным знаком в самом начале: 𠮷 — одна кодовая точка и две
 * единицы JS, поэтому всё, что за ним, смещено ровно на единицу.
 */
const ASTRAL = '𠮷田说。\n他走了。'

/** Токены в том виде, в каком их отдаёт бэкенд: офсеты по кодовым точкам. */
const ASTRAL_TOKENS: Token[] = [
  [0, 2, 'word'], // 𠮷田
  [2, 3, 'word'], // 说
  [3, 4, 'punct'], // 。
  [4, 5, 'space'], // перевод строки
  [5, 6, 'word'], // 他
  [6, 7, 'word'], // 走
  [7, 8, 'word'], // 了
  [8, 9, 'punct'], // 。
]

const ASTRAL_SENTENCES: Sentence[] = [
  { id: 1, idx: 0, start: 0, end: 4, translation: 'Ёсида сказал.' },
  { id: 2, idx: 1, start: 5, end: 9, translation: 'Он ушёл.' },
]

/** Та же глава без астральных знаков: офсеты совпадают в обеих системах. */
const PLAIN = '天很黑。\n他走了。'
const PLAIN_TOKENS: Token[] = [
  [0, 1, 'word'],
  [1, 3, 'word'],
  [3, 4, 'punct'],
  [4, 5, 'space'],
  [5, 6, 'word'],
  [6, 8, 'word'],
  [8, 9, 'punct'],
]
const PLAIN_SENTENCES: Sentence[] = [
  { id: 1, idx: 0, start: 0, end: 4, translation: null },
  { id: 2, idx: 1, start: 5, end: 9, translation: 'Он ушёл.' },
]

describe('buildOffsetMap', () => {
  it('не строит карту, когда офсеты и так совпадают', () => {
    // Обычный случай, и он обязан быть бесплатным: карта на главу в 3600
    // знаков — это массив, который незачем ни строить, ни держать.
    expect(buildOffsetMap('天很黑，风从窗户外面吹进来。')).toBeNull()
    expect(buildOffsetMap('Plain latin text.')).toBeNull()
    expect(buildOffsetMap('')).toBeNull()
  })

  it('строит карту, когда в тексте есть астральный знак', () => {
    const map = buildOffsetMap(ASTRAL)
    expect(map).not.toBeNull()
    // Кодовая точка 0 стоит в начале, а первая же следующая уже сдвинута.
    expect(map![0]).toBe(0)
    expect(map![1]).toBe(2)
  })

  it('в карте есть офсет конца строки', () => {
    // Без него последний токен главы обрезался бы на один знак.
    const map = buildOffsetMap(ASTRAL)!
    expect(map[[...ASTRAL].length]).toBe(ASTRAL.length)
  })
})

describe('buildIndex: офсеты', () => {
  it('токены режут текст в свои же слова, несмотря на астральный знак', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)

    expect(index.tokens.map((t) => t.text)).toEqual([
      '𠮷田',
      '说',
      '。',
      '\n',
      '他',
      '走',
      '了',
      '。',
    ])
  })

  it('текст токена совпадает со срезом по его же границам', () => {
    // Инвариант, на котором держится всё остальное: подсветка, контекст
    // сохранённого слова, разбор по знакам.
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    for (const token of index.tokens) {
      expect(ASTRAL.slice(token.start, token.end)).toBe(token.text)
    }
  })

  it('без перевода офсетов глава уехала бы на знак — вот на чём это видно', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    const afterNewline = index.tokens[4]

    expect(afterNewline.text).toBe('他')
    // Ровно это получилось бы, возьми мы офсеты бэкенда как есть.
    expect(ASTRAL.slice(5, 6)).toBe('\n')
  })

  it('границы предложений тоже переведены', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)

    expect(ASTRAL.slice(index.sentenceStart[0], index.sentenceEnd[0])).toBe('𠮷田说。')
    expect(ASTRAL.slice(index.sentenceStart[1], index.sentenceEnd[1])).toBe('他走了。')
  })

  it('на обычном тексте офсеты не трогаются', () => {
    const index = buildIndex(PLAIN, PLAIN_TOKENS, PLAIN_SENTENCES)
    for (const token of index.tokens) {
      expect(PLAIN.slice(token.start, token.end)).toBe(token.text)
    }
  })
})

describe('buildIndex: абзацы и предложения', () => {
  it('режет на абзацы по токену перевода строки', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)

    expect(index.paragraphs).toHaveLength(2)
    expect(index.paragraphs[0].tokens.map((t) => t.text)).toEqual(['𠮷田', '说', '。'])
    expect(index.paragraphs[1].tokens.map((t) => t.text)).toEqual(['他', '走', '了', '。'])
  })

  it('перевод строки не попадает ни в один абзац', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    const rendered = index.paragraphs.flatMap((p) => p.tokens.map((t) => t.text)).join('')
    expect(rendered).not.toContain('\n')
  })

  it('склейка абзацев обратно даёт исходный текст', () => {
    // Фронт собирает главу из токенов, а не из строки: дырка в покрытии — это
    // дырка на экране.
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(index.tokens.map((t) => t.text).join('')).toBe(ASTRAL)
  })

  it('каждому токену проставлено его предложение', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect([...index.sentenceOf]).toEqual([0, 0, 0, -1, 1, 1, 1, 1])
  })

  it('перевод строки предложению не принадлежит', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(index.sentenceOf[3]).toBe(-1)
  })

  it('знает первый и последний токен каждого предложения', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)

    expect([...index.firstToken]).toEqual([0, 4])
    expect([...index.lastToken]).toEqual([2, 7])
  })

  it('раскладывает предложения по абзацам — по ним строится вид перевода', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(index.paragraphSentences).toEqual([[0], [1]])
  })

  it('абзац из нескольких предложений сохраняет их порядок', () => {
    const content = '他来了。她走了。'
    const tokens: Token[] = [
      [0, 1, 'word'],
      [1, 3, 'word'],
      [3, 4, 'punct'],
      [4, 5, 'word'],
      [5, 7, 'word'],
      [7, 8, 'punct'],
    ]
    const sentences: Sentence[] = [
      { id: 1, idx: 0, start: 0, end: 4, translation: null },
      { id: 2, idx: 1, start: 4, end: 8, translation: null },
    ]

    const index = buildIndex(content, tokens, sentences)
    expect(index.paragraphSentences).toEqual([[0, 1]])
  })

  it('переживает пустую главу', () => {
    // Пока конвейер не дошёл до segmented, текста нет — и индекс строится
    // всё равно, потому что экран рисуется раньше.
    const index = buildIndex('', [], [])
    expect(index.paragraphs).toEqual([])
    expect(index.tokens).toEqual([])
    expect(index.paragraphSentences).toEqual([])
  })
})

describe('clampToSentence', () => {
  it('не пускает выделение за границу предложения', () => {
    // Требование segmentation.md §4: иначе легко собрать бессвязный кусок из
    // хвоста одной фразы и начала другой.
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(clampToSentence(index, 1, 6)).toEqual([1, 2])
  })

  it('упирается в начало предложения при протяжке назад', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(clampToSentence(index, 6, 1)).toEqual([4, 6])
  })

  it('внутри предложения расширяет как есть', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(clampToSentence(index, 4, 6)).toEqual([4, 6])
  })

  it('нормализует направление протяжки', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(clampToSentence(index, 6, 4)).toEqual([4, 6])
  })

  it('токен вне предложений никуда не тянется', () => {
    const index = buildIndex(ASTRAL, ASTRAL_TOKENS, ASTRAL_SENTENCES)
    expect(clampToSentence(index, 3, 6)).toEqual([3, 3])
  })
})

describe('isSelectable', () => {
  it.each([
    ['word', true],
    ['latin', true],
    ['digit', true],
    ['punct', false],
    ['space', false],
  ] as const)('%s → %s', (kind, expected) => {
    // Запятая и пробел карточке не нужны: статьи на них не бывает.
    expect(isSelectable(kind)).toBe(expected)
  })
})

describe('codePointLength', () => {
  it('считает астральный знак одним', () => {
    // Наружу офсеты уезжают по кодовым точкам, иначе контекст сохранённого
    // слова разойдётся с python-стороной ровно так же, как разъехался бы рендер.
    expect(codePointLength('𠮷')).toBe(1)
    expect('𠮷'.length).toBe(2)
  })

  it.each([
    ['', 0],
    ['abc', 3],
    ['天很黑', 3],
    ['𠮷田', 2],
  ])('%o → %i', (text, expected) => {
    expect(codePointLength(text)).toBe(expected)
  })
})

describe('splitChars', () => {
  it('режет по кодовым точкам, а не по единицам UTF-16', () => {
    // Половинка суррогатной пары — не символ, и выделять её нельзя.
    expect(splitChars('𠮷田', 0)).toEqual([
      { start: 0, end: 2, text: '𠮷' },
      { start: 2, end: 3, text: '田' },
    ])
  })

  it('считает от переданного офсета', () => {
    expect(splitChars('走了', 6)).toEqual([
      { start: 6, end: 7, text: '走' },
      { start: 7, end: 8, text: '了' },
    ])
  })

  it('куски режут исходный текст обратно в себя', () => {
    for (const piece of splitChars(ASTRAL.slice(0, 3), 0)) {
      expect(ASTRAL.slice(piece.start, piece.end)).toBe(piece.text)
    }
  })
})
