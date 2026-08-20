/**
 * Жесты по тексту главы: тап — подсветка, удержание — подсказка с переводом,
 * двойной тап — подробная панель, протяжка — фраза.
 *
 * Два уровня ответа вместо одного — осознанно. Раньше любое касание распахивало
 * панель на треть экрана, и чтение прерывалось на каждом незнакомом слове.
 * Теперь дешёвый жест даёт дешёвый ответ: подсказка живёт, пока палец на
 * экране, и не двигает текст. Всё остальное — перевод предложения, статьи из
 * обоих словарей, кнопка в словарь — стоит второго касания.
 *
 * Слушатели вешаются нативно и один раз на весь контейнер (делегирование):
 * спанов около 2500, и обработчик на каждом — это и память, и лишняя работа
 * при перерисовке.
 *
 * Четыре вещи здесь неочевидны, и каждая ломает жесты по-своему:
 *
 * 1. **`e.target` во время протяжки пальцем залипает на стартовом спане** —
 *    браузер неявно захватывает указатель. Поэтому «на каком токене палец
 *    сейчас» спрашивается через `elementFromPoint`, а не у события.
 * 2. **`preventDefault()` в JSX-пропах не работает**: React вешает
 *    touch-слушатели как passive. Отсюда `addEventListener` вручную с
 *    `{ passive: false }`.
 * 3. **Признак «жест превратился в прокрутку» — `pointercancel`**, а не своя
 *    эвристика по смещению: браузер решает это раньше и точнее нас.
 * 4. **Двойной тап не ждёт своего окна.** Обычно распознавание двойного
 *    касания стоит 300 мс задержки на одиночном. Здесь не стоит: первый тап
 *    только подсвечивает, подсветка обратима — значит её применяем сразу, а
 *    второе касание уже поверх неё открывает панель.
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
/** Долгое нажатие. 500 мс — системная величина и на iOS, и на Android. */
const LONG_PRESS_MS = 500
/** Окно двойного тапа. 300 мс — столько же держат браузеры перед `dblclick`. */
const DOUBLE_TAP_MS = 300

export interface GestureCallbacks {
  /** Тап: выделить токен и ничего не открывать. */
  onTap(tokenIndex: number): void
  /** Двойной тап по тому же токену: раскрыть подробную панель. */
  onOpen(tokenIndex: number): void
  /** Протяжка: расширить выделение до токена под пальцем. */
  onExtend(anchorIndex: number, focusIndex: number): void
  /** Протяжка окончена: показать панель по собранной фразе. */
  onExtendEnd(): void
  /** Удержание: подсказка с переводом токена, у точки касания. */
  onPeek(tokenIndex: number, x: number, y: number): void
  /** Палец отпущен — подсказку убрать. */
  onPeekEnd(): void
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
  peeking: boolean
  timer: number
}

function tokenAtPoint(x: number, y: number): number | null {
  const element = document.elementFromPoint(x, y)
  const holder = element instanceof Element ? element.closest<HTMLElement>('[data-i]') : null
  if (!holder) return null
  const index = Number(holder.dataset.i)
  return Number.isInteger(index) ? index : null
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

  // index в зависимостях, хотя в теле не используется: смена главы обязана
  // обнулить и незакрытый жест, и память о первом касании двойного тапа.
  useEffect(() => {
    const node = ref.current
    if (!node) return

    let gesture: Gesture | null = null
    let lastTap: { tokenIndex: number; at: number } | null = null

    const clear = () => {
      if (gesture) clearTimeout(gesture.timer)
      gesture = null
    }

    /** Убрать подсказку, если она была показана этим жестом. */
    const endPeek = () => {
      if (gesture?.peeking) latest.current.onPeekEnd()
    }

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0 && e.pointerType === 'mouse') return
      const tokenIndex = tokenAtPoint(e.clientX, e.clientY)
      if (tokenIndex === null) return

      const { clientX: x, clientY: y } = e

      gesture = {
        pointerId: e.pointerId,
        tokenIndex,
        x,
        y,
        slop: e.pointerType === 'mouse' ? SLOP_MOUSE : SLOP_TOUCH,
        dragging: false,
        peeking: false,
        timer: window.setTimeout(() => {
          if (!gesture || gesture.dragging) return
          gesture.peeking = true
          // Удержание — самостоятельный жест, а не затянувшийся тап. Без этой
          // строки отпускание пальца засчиталось бы первым касанием двойного,
          // и следующий обычный тап неожиданно распахнул бы панель.
          lastTap = null
          latest.current.onPeek(tokenIndex, x, y)
        }, LONG_PRESS_MS),
      }
    }

    const onPointerMove = (e: PointerEvent) => {
      if (!gesture || e.pointerId !== gesture.pointerId) return

      const dx = Math.abs(e.clientX - gesture.x)
      const dy = Math.abs(e.clientY - gesture.y)
      if (!gesture.dragging && Math.hypot(dx, dy) < gesture.slop) return

      // Палец поехал — это уже не удержание.
      clearTimeout(gesture.timer)
      if (gesture.peeking) return

      gesture.dragging = true
      lastTap = null
      // Иначе браузер начнёт своё выделение текста поверх нашего.
      if (e.cancelable) e.preventDefault()

      const focus = tokenAtPoint(e.clientX, e.clientY)
      if (focus !== null) latest.current.onExtend(gesture.tokenIndex, focus)
    }

    const onPointerUp = (e: PointerEvent) => {
      if (!gesture || e.pointerId !== gesture.pointerId) return
      const { tokenIndex, dragging, peeking } = gesture
      endPeek()
      clear()

      if (peeking) return
      if (dragging) {
        latest.current.onExtendEnd()
        return
      }

      // Двойной тап требует того же токена, а не просто соседней точки:
      // «дважды по тому слову, что подсветилось» — правило, которое читатель
      // может держать в голове, а «дважды в пределах 20 пикселей» — нет.
      const now = performance.now()
      if (lastTap && lastTap.tokenIndex === tokenIndex && now - lastTap.at <= DOUBLE_TAP_MS) {
        lastTap = null
        latest.current.onOpen(tokenIndex)
        return
      }

      lastTap = { tokenIndex, at: now }
      latest.current.onTap(tokenIndex)
    }

    // Прокрутка началась — браузер забрал жест себе, и это нормально.
    // Панель при этом не открываем: незавершённая протяжка ничего не выбрала.
    const onPointerCancel = () => {
      endPeek()
      clear()
    }

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
      endPeek()
      clear()
      node.removeEventListener('pointerdown', onPointerDown)
      node.removeEventListener('pointermove', onPointerMove)
      node.removeEventListener('pointerup', onPointerUp)
      node.removeEventListener('pointercancel', onPointerCancel)
      node.removeEventListener('keydown', onKeyDown)
    }
  }, [ref, index])
}
