/**
 * Человеческие сообщения по `error_kind` (RFC §4, задача T1.14).
 *
 * Правило одно: пользователь должен понимать, **что делать**, а не читать
 * перевод машинного кода. «Челлендж» и «404» обязаны выглядеть по-разному —
 * это разные ситуации: первая лечится ожиданием, вторая правкой адреса.
 *
 * Технический `kind` и подробность от бэкенда всё равно показываются рядом,
 * мелким шрифтом: когда что-то ломается, детали экономят полчаса.
 */

import type { BrowserCheck, ErrorKind } from './api'

export interface ErrorInfo {
  /** Короткий заголовок: что случилось. */
  title: string
  /** Что с этим делать. */
  advice: string
  /** Имеет ли смысл повторить загрузку тем же адресом. */
  retryable: boolean
  /** Текст главы при этом есть, читать можно. */
  readable: boolean
}

const MESSAGES: Record<string, ErrorInfo> = {
  challenge: {
    title: 'Сайт просит пройти проверку',
    advice:
      'Он решил, что мы робот. Обычно проверка проходит сама — попробуйте ещё раз через минуту.',
    retryable: true,
    readable: false,
  },
  not_found: {
    title: 'Главы по этому адресу нет',
    advice: 'Проверьте номер главы в ссылке: возможно, книга закончилась раньше.',
    retryable: true,
    readable: false,
  },
  empty_extract: {
    title: 'Текста главы на странице нет',
    advice:
      'Похоже, это оглавление книги, а не глава. Откройте нужную главу на сайте и скопируйте её адрес.',
    retryable: false,
    readable: false,
  },
  fetch_timeout: {
    title: 'Сайт не ответил вовремя',
    advice: 'Он может быть перегружен. Попробуйте ещё раз.',
    retryable: true,
    readable: false,
  },
  adapter_error: {
    title: 'Не удалось разобрать страницу',
    advice: 'Что-то пошло не так на нашей стороне. Подробность ниже стоит показать разработчику.',
    retryable: true,
    readable: false,
  },
  translate_failed: {
    title: 'Перевод не получен',
    advice: 'Текст главы на месте, читать можно. Перевод получится дозалить кнопкой.',
    retryable: false,
    readable: true,
  },
  speech_failed: {
    title: 'Озвучка не получилась',
    advice:
      'Текст и перевод на месте — не вышло только прочитать вслух. Чаще всего не хватает роли ai.speechkit-tts.user у сервисного аккаунта; проверьте это на экране состояния.',
    retryable: true,
    readable: true,
  },
  interrupted: {
    title: 'Загрузку оборвал перезапуск',
    advice:
      'Сервис обновился, пока глава грузилась. Ни сайт, ни переводчик тут ни при чём — просто попробуйте ещё раз.',
    retryable: true,
    readable: false,
  },
  budget_exceeded: {
    title: 'Достигнут лимит расходов на перевод',
    advice:
      'Это наш собственный потолок, а не сбой: повтор не поможет, лимит поднимается в настройках сервера.',
    retryable: false,
    readable: true,
  },
  bad_request: {
    title: 'Это не похоже на ссылку',
    advice: 'Нужен полный адрес главы, вместе с https://.',
    retryable: false,
    readable: false,
  },
  network: {
    title: 'Сервер не отвечает',
    advice: 'Проверьте соединение — и что backend запущен.',
    retryable: true,
    readable: false,
  },
}

const UNKNOWN: ErrorInfo = {
  title: 'Непонятная ошибка',
  advice: 'Подробность ниже стоит показать разработчику.',
  retryable: true,
  readable: false,
}

/**
 * Ответ без нашего тела: до бэкенда не дошли или он ответил не по формату.
 *
 * `kind` вида `http_502` заводит сам клиент, когда в ответе нет `{error:
 * {kind}}` — то есть отвечал не сервис, а прокси перед ним. Случай не
 * экзотический: бэкенд перезапускается на **каждой выкладке**, и всё это
 * время открытая вкладка получает 502. До сих пор она показывала «Непонятная
 * ошибка. Подробность стоит показать разработчику» — то есть пугала и
 * советовала не то в самой обычной ситуации.
 */
const GATEWAY: ErrorInfo = {
  title: 'Сервис сейчас недоступен',
  advice:
    'Чаще всего это выкладка: бэкенд перезапускается несколько секунд. ' +
    'Подождите и повторите — данные никуда не делись.',
  retryable: true,
  readable: false,
}

/** Прокси ответил, а сервиса за ним не оказалось. */
const GATEWAY_STATUSES = new Set(['http_404', 'http_500', 'http_502', 'http_503', 'http_504'])

export function describeError(kind: ErrorKind | string): ErrorInfo {
  const known = MESSAGES[kind]
  if (known) return known
  return GATEWAY_STATUSES.has(kind) ? GATEWAY : UNKNOWN
}

/**
 * Итог живой проверки сайта словами: что увидел браузер и что с этим делать.
 *
 * Три исхода, и советы у них разные. Страница открылась — кука проверки легла
 * в профиль, и загрузка глав теперь пойдёт. Не открылась, а окна не видно —
 * чинить надо настройку, руками тут ничего не пройти. Не открылась, но окно
 * есть — вот тогда и смотрят на снимок, и жмут капчу.
 */
export function describeBrowserCheck(result: BrowserCheck): string {
  const waited = `за ${result.waited_seconds} с`

  if (result.ok) {
    return (
      `Страница открылась ${waited}: HTTP ${result.status}, «${result.title}». ` +
      'Кука проверки легла в профиль — загрузка глав пойдёт.'
    )
  }

  const reason = result.kind ? describeError(result.kind).title : `HTTP ${result.status}`

  if (!result.visible) {
    return (
      `${reason} ${waited}, но окна не видно: браузер запущен headless, ` +
      'и пройти проверку руками негде. Выключите BROWSER_HEADLESS и перезапустите сервис.'
    )
  }

  return (
    `${reason} ${waited}. Заголовок страницы: «${result.title}». ` +
    'Если на снимке капча — пройдите её в окне браузера и запустите проверку ещё раз.'
  )
}

/** Что показывать, пока конвейер работает: статус словами, а не термином. */
export function describeStatus(status: string): string {
  switch (status) {
    case 'fetching':
      return 'Открываю страницу на сайте…'
    case 'segmented':
      return 'Текст разобран, жду перевод…'
    case 'translating':
      return 'Перевожу предложения…'
    case 'ready':
      return 'Готово'
    default:
      return status
  }
}
