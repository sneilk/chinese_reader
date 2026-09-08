/**
 * Выбор темы: «как в системе», тёмная или светлая.
 *
 * Тёмная тема здесь основная — читают вечером и подолгу. Но до сих пор выбора
 * не было вовсе: палитра переключалась одним только `prefers-color-scheme`, и
 * на ноутбуке со светлой системной настройкой добиться тёмной было нельзя
 * никак. Настройка системы и настройка читалки — разные вещи: в списке
 * приложений светлое всё, а читают всё равно в тёмном.
 *
 * ## Переключается одно свойство, а не палитра
 *
 * Токены в `index.css` объявлены через `light-dark(светлое, тёмное)`, и какое
 * из двух значений взять, решает `color-scheme`. Поэтому выбор темы — это
 * ровно одна строка на корне документа, а не вторая копия палитры. Разойтись
 * им негде: значения лежат в одном месте.
 *
 * `auto` не пишет `color-scheme` вовсе и оставляет тот, что задан в CSS
 * (`dark light`) — то есть отдаёт решение системе.
 *
 * ## Почему выбор хранится, а не выводится
 *
 * Прочитать системную настройку можно, а «пользователь хочет тёмную вопреки
 * системе» — нельзя: это знание существует только потому, что его сообщили.
 * Отсюда localStorage, и отсюда же три состояния вместо двух: «как в системе»
 * — это не то же самое, что «светлая», даже когда система светлая сейчас.
 */

export type ThemeChoice = 'auto' | 'dark' | 'light'

const KEY = 'chinese_reader.theme'

/** Порядок обхода по нажатию. Начинается с `auto`: это состояние по умолчанию. */
export const THEME_ORDER: ThemeChoice[] = ['auto', 'dark', 'light']

/** Разбор сохранённого значения. Мусор и пустота — это «как в системе». */
export function parseTheme(raw: string | null | undefined): ThemeChoice {
  return raw === 'dark' || raw === 'light' || raw === 'auto' ? raw : 'auto'
}

/** Следующий выбор по кругу: авто → тёмная → светлая → авто. */
export function nextTheme(current: ThemeChoice): ThemeChoice {
  const at = THEME_ORDER.indexOf(current)
  return THEME_ORDER[(at + 1) % THEME_ORDER.length]
}

/** Подпись выбора для человека. */
export function describeTheme(choice: ThemeChoice): string {
  switch (choice) {
    case 'dark':
      return 'Тёмная'
    case 'light':
      return 'Светлая'
    default:
      return 'Как в системе'
  }
}

/**
 * Какая тема окажется на экране при этом выборе.
 *
 * Нужна не для показа палитры, а для `theme-color`: строку состояния браузера
 * красит конкретный цвет, и «как в системе» ему передать нечем.
 */
export function resolveTheme(choice: ThemeChoice, prefersLight: boolean): 'dark' | 'light' {
  if (choice !== 'auto') return choice
  return prefersLight ? 'light' : 'dark'
}

/** Цвет строки состояния браузера. Совпадает с `--bg` соответствующей темы. */
export const THEME_COLOR: Record<'dark' | 'light', string> = {
  dark: '#16181c',
  light: '#fbfaf8',
}

/**
 * Применить выбор к документу.
 *
 * `data-theme` нужен не CSS, а самой странице: по нему видно, что выбор
 * сделан руками. Палитру переключает `color-scheme`, и только он.
 */
export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  if (choice === 'auto') {
    root.removeAttribute('data-theme')
    root.style.removeProperty('color-scheme')
  } else {
    root.setAttribute('data-theme', choice)
    root.style.setProperty('color-scheme', choice)
  }

  const prefersLight =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: light)').matches

  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[resolveTheme(choice, prefersLight)])
}

export function loadTheme(): ThemeChoice {
  try {
    return parseTheme(localStorage.getItem(KEY))
  } catch {
    // Приватный режим и заблокированные хранилища: без выбора жить можно.
    return 'auto'
  }
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    localStorage.setItem(KEY, choice)
  } catch {
    // Не сохранилось — переживём: на этот сеанс тема уже применена.
  }
}
