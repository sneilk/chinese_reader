/**
 * Подсказка у пальца: слово, чтение и первое значение — пока палец не отпущен.
 *
 * Отвечает ровно на один вопрос — «что это за слово» — и исчезает сама. Всё
 * остальное (перевод предложения, статьи обоих словарей, разбор по знакам,
 * кнопка в словарь) стоит двойного тапа и живёт в нижней панели: беглое чтение
 * не должно каждый раз платить за подробности, которых не просили.
 *
 * Значения приезжают тем же `useLookup`, что и у панели, а он кэширует ответы
 * на сессию. Поэтому подсказка по слову, которое уже смотрели, появляется без
 * сети — а имя героя в новелле встречается сотнями.
 *
 * Позиция считается после отрисовки: ширина зависит от текста, а прижимать
 * подсказку к краю экрана приходится по фактическому размеру. Пока размер не
 * измерен, она стоит за экраном — `useLayoutEffect` успевает отработать до
 * кадра, поэтому прыжка не видно.
 */

import { useLayoutEffect, useRef, useState } from 'react'
import type { Language, Lookup } from '../api'

/** Зазор до пальца и до краёв экрана. */
const GAP = 12

/** Сколько значений влезает в одну строку, не превращая подсказку в карточку. */
const MAX_SENSES = 2

function summary(lookup: Lookup | null): string {
  if (!lookup) return '…'
  if (lookup.found) {
    const senses = lookup.entries[0]?.senses ?? []
    const text = senses.slice(0, MAX_SENSES).join('; ') || 'значения нет'
    // Слово найдено не в той форме, в какой стоит в тексте: `running` → `run`.
    // Без пометки подсказка выдавала бы значение другого слова за это.
    return lookup.matched ? `${lookup.matched} — ${text}` : text
  }
  // Статьи на слово нет — обычный случай для имён героев. Собираем строку из
  // знаков, но по одному значению на знак: подсказка не место для разбора.
  if (lookup.approximate) {
    return lookup.chars.map((gloss) => `${gloss.char} ${gloss.senses[0] ?? '—'}`).join(' · ')
  }
  return 'в словарях нет'
}

export function WordPeek({
  term,
  lookup,
  lang,
  x,
  y,
}: {
  term: string
  lookup: Lookup | null
  lang: Language
  x: number
  y: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ left: number; top: number } | null>(null)
  const reading = lookup?.entries[0]?.reading ?? null

  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return

    const { width, height } = node.getBoundingClientRect()
    const left = Math.min(Math.max(x - width / 2, GAP), window.innerWidth - width - GAP)
    // Над пальцем, потому что палец закрывает то, что под ним. У верхнего края
    // места нет — тогда снизу, отступив на палец.
    const above = y - GAP - height
    setBox({ left, top: above >= GAP ? above : y + GAP * 3 })
  }, [x, y, term, reading, lookup])

  return (
    <div
      className="peek"
      ref={ref}
      role="status"
      aria-live="polite"
      style={box ? { left: box.left, top: box.top } : { left: 0, top: -9999 }}
    >
      <span className="peek__term" lang={lang === 'zh' ? 'zh-Hans' : 'en'}>
        {term}
      </span>
      {reading && <span className="peek__reading">{reading}</span>}
      <span className="peek__sense">{summary(lookup)}</span>
    </div>
  )
}
