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

import type { ErrorKind } from './api'

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

export function describeError(kind: ErrorKind | string): ErrorInfo {
  return MESSAGES[kind] ?? UNKNOWN
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
