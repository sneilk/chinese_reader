/**
 * Жесты по тексту главы: тап — слово, протяжка — фраза, долгий тап — символ.
 *
 * Слушатели вешаются нативно и один раз на весь контейнер (делегирование):
 * спанов около 2500, и обработчик на каждом — это и память, и лишняя работа
 * при перерисовке.
 *
 * Три вещи здесь неочевидны, и каждая ломает жесты по-своему:
 *
 * 1. **`e.target` во время протяжки пальцем залипает на стартовом спане** —
 *    браузер неявно захватывает указатель. Поэтому «на каком токене палец
 *    сейчас» спрашивается через `elementFromPoint`, а не у события.
 * 2. **`preventDefault()` в JSX-пропах не работает**: React вешает
 *    touch-слушатели как passive. Отсюда `addEventListener` вручную с
 *    `{ passive: false }`.
 * 3. **Признак «жест превратился в прокрутку» — `pointercancel`**, а не своя
 *    эвристика по смещению: браузер решает это раньше и точнее нас.
 *
 * Вертикальную прокрутку не трогаем вовсе — она отдана браузеру через
 * `touch-action: pan-y` (см. index.css). Протяжка расширяет выделение, пока
 * она горизонтальная; как только палец поехал вверх или вниз, приходит
 * `pointercancel` и жест отменяется.
 */

import { useEffect, useRef } from 'react'
import type { ChapterIndex } from './tokens'

/** Порог «палец дрогнул» против «пользователь тянет». Для мыши он мельче. */
const SLOP_TOUCH = 10
const SLOP_MOUSE = 3
/** Долгий тап. 500 мс — системная величина и на iOS, и на Android. */
const LONG_PRESS_MS = 500

export interface GestureCallbacks {
  /** Тап: выделить токен целиком. */
  onTap(tokenIndex: number): void
  /** Протяжка: расширить выделение до токена под пальцем. */
  onExtend(anchorIndex: number, focusIndex: number): void
  /** Долгий тап: сузить до одного символа. `charOffset` — в единицах JS-строки. */
  onCharacter(tokenIndex: number, charOffset: number): void
  /** Клавиатура: сдвинуть выделение на соседний токен. */
  onStep(direction: -1 | 1): void
}

interface Gesture {
  pointerId: number
  tokenIndex: number
  x: number
  y: number
  slop: number
  dragging: boolean
  longPressed: boolean
  timer: number
}

function tokenAtPoint(x: number, y: number): number | null {
  const element = document.elementFromPoint(x, y)
  const holder = element instanceof Element ? element.closest<HTMLElement>('[data-i]') : null
  if (!holder) return null
  const index = Number(holder.dataset.i)
  return Number.isInteger(index) ? index : null
}

/**
 * Смещение символа под точкой — внутри текста главы.
 *
 * Спрашиваем у браузера позицию каретки: она попадает в символ точнее, чем
 * деление ширины спана на число знаков, и не ломается на переносе строки.
 */
function charOffsetAtPoint(x: number, y: number, start: number, end: number): number {
  const doc = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
    caretRangeFromPoint?: (x: number, y: number) => Range | null
  }

  let offsetInNode: number | null = null
  if (typeof doc.caretPositionFromPoint === 'function') {
    offsetInNode = doc.caretPositionFromPoint(x, y)?.offset ?? null
  } else if (typeof doc.caretRangeFromPoint === 'function') {
    offsetInNode = doc.caretRangeFromPoint(x, y)?.startOffset ?? null
  }
  if (offsetInNode === null) return start

  // Каретка стоит между символами; берём тот, что слева от неё, но не выходя
  // за границы самого токена.
  const absolute = start + offsetInNode
  return Math.min(Math.max(absolute, start), end - 1)
}

export function useTokenGestures(
  ref: React.RefObject<HTMLElement | null>,
  index: ChapterIndex,
  callbacks: GestureCallbacks,
): void {
  // Колбэки приезжают новым объектом на каждый рендер, а выделение меняет
  // состояние на каждом движении пальца. Если держать их в зависимостях
  // эффекта, слушатели переподпишутся прямо посреди жеста — и протяжка
  // умрёт после первого же шага, потому что вместе с ними обнулится и
  // накопленное состояние жеста.
  const latest = useRef(callbacks)
  latest.current = callbacks

  useEffect(() => {
    const node = ref.current
    if (!node) return

    let gesture: Gesture | null = null

    const clear = () => {
      if (gesture) clearTimeout(gesture.timer)
      gesture = null
    }

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0 && e.pointerType === 'mouse') return
      const tokenIndex = tokenAtPoint(e.clientX, e.clientY)
      if (tokenIndex === null) return

      const { clientX: x, clientY: y } = e
      const token = index.tokens[tokenIndex]

      gesture = {
        pointerId: e.pointerId,
        tokenIndex,
        x,
        y,
        slop: e.pointerType === 'mouse' ? SLOP_MOUSE : SLOP_TOUCH,
        dragging: false,
        longPressed: false,
        timer: window.setTimeout(() => {
          if (!gesture || gesture.dragging) return
          gesture.longPressed = true
          latest.current.onCharacter(tokenIndex, charOffsetAtPoint(x, y, token.start, token.end))
        }, LONG_PRESS_MS),
      }
    }

    const onPointerMove = (e: PointerEvent) => {
      if (!gesture || e.pointerId !== gesture.pointerId) return

      const dx = Math.abs(e.clientX - gesture.x)
      const dy = Math.abs(e.clientY - gesture.y)
      if (!gesture.dragging && Math.hypot(dx, dy) < gesture.slop) return

      // Палец поехал — это уже не долгий тап.
      clearTimeout(gesture.timer)
      if (gesture.longPressed) return

      gesture.dragging = true
      // Иначе браузер начнёт своё выделение текста поверх нашего.
      if (e.cancelable) e.preventDefault()

      const focus = tokenAtPoint(e.clientX, e.clientY)
      if (focus !== null) latest.current.onExtend(gesture.tokenIndex, focus)
    }

    const onPointerUp = (e: PointerEvent) => {
      if (!gesture || e.pointerId !== gesture.pointerId) return
      const { tokenIndex, dragging, longPressed } = gesture
      clear()
      if (!dragging && !longPressed) latest.current.onTap(tokenIndex)
    }

    // Прокрутка началась — браузер забрал жест себе, и это нормально.
    const onPointerCancel = () => clear()

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') {
        e.preventDefault()
        latest.current.onStep(1)
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        latest.current.onStep(-1)
      }
    }

    // passive: false — иначе preventDefault в pointermove молча не сработает.
    node.addEventListener('pointerdown', onPointerDown)
    node.addEventListener('pointermove', onPointerMove, { passive: false })
    node.addEventListener('pointerup', onPointerUp)
    node.addEventListener('pointercancel', onPointerCancel)
    node.addEventListener('keydown', onKeyDown)

    return () => {
      clear()
      node.removeEventListener('pointerdown', onPointerDown)
      node.removeEventListener('pointermove', onPointerMove)
      node.removeEventListener('pointerup', onPointerUp)
      node.removeEventListener('pointercancel', onPointerCancel)
      node.removeEventListener('keydown', onKeyDown)
    }
  }, [ref, index])
}
