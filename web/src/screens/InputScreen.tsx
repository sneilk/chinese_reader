/**
 * Экран ввода URL главы (T1.14).
 *
 * Отправили — остаёмся здесь и опрашиваем статус, пока глава не станет
 * читаемой. Уводить на экран чтения сразу нельзя: `POST` отвечает раньше, чем
 * появляется текст, и читатель увидел бы пустоту вместо прогресса.
 *
 * Отказ показывается здесь же, с человеческим объяснением и кнопкой повтора.
 * Повтор — это тот же `POST`: глава в `failed` перезапускается (RFC §4), так
 * что фронту не нужно помнить, чем повтор отличается от первого раза.
 *
 * Поле «и ещё N глав» — вход в книгу целиком. Оглавления у английского
 * источника из разметки не достать (sources.md §2), поэтому книга обходится по
 * ссылкам «следующая глава», и просить их надо счётом, а не адресом каждой.
 * Ноль по умолчанию: обход — решение читателя, а не побочное действие ссылки.
 *
 * Уходим читать по первой готовой главе, не дожидаясь остальных: обход идёт
 * фоном и по две секунды на страницу, а первая глава к этому моменту уже на
 * экране. Ждать её соседей — значит смотреть на спиннер вместо чтения.
 */

import { useEffect, useState } from 'react'
import { ApiError, api, isReadable } from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { describeStatus } from '../errors'
import { navigate } from '../router'
import { useChapter } from '../useChapter'

/**
 * Потолок обхода до ответа сервера. Не вторая копия настройки, а значение на
 * те полсекунды, пока не приехало настоящее: держать его константой значило бы
 * однажды предложить поле шире, чем примут в ответ.
 */
const FALLBACK_FOLLOW = 20

export function InputScreen() {
  const [url, setUrl] = useState('')
  const [follow, setFollow] = useState(0)
  const [maxFollow, setMaxFollow] = useState(FALLBACK_FOLLOW)
  const [chapterId, setChapterId] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<ApiError | null>(null)

  // Предел спрашиваем у сервера: он же его и применяет. Ручка живости дешёвая —
  // в отличие от диагностики, которая считает статьи в словаре на три миллиона
  // строк. Отказ глушим: без предела экран работает, просто с догадкой.
  useEffect(() => {
    api
      .health()
      .then((health) => setMaxFollow(health.limits.max_chapters_per_run))
      .catch(() => undefined)
  }, [])

  const { chapter, requestError, reload } = useChapter(chapterId)

  // Как только текст есть — уходим читать. Перевод, если он ещё идёт,
  // догрузится на экране чтения: ждать его здесь незачем.
  useEffect(() => {
    if (chapter && isReadable(chapter.status)) {
      navigate({ name: 'chapter', id: chapter.id })
    }
  }, [chapter])

  async function send(target: string) {
    if (!target || sending) return
    setSending(true)
    setSendError(null)
    try {
      const accepted = await api.createChapter(target, follow)
      setChapterId(accepted.id)
      if (accepted.id === chapterId) await reload()
    } catch (e) {
      setSendError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setSending(false)
    }
  }

  const failure = chapter?.status === 'failed' ? chapter.error : null
  const working = Boolean(chapterId) && !failure && !requestError
  const busy = sending || working

  return (
    <>
      <h1>Читать главу</h1>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          void send(url.trim())
        }}
      >
        <div className="field">
          <label className="label" htmlFor="url">
            Адрес главы
          </label>
          <input
            id="url"
            className="input"
            type="url"
            inputMode="url"
            autoComplete="url"
            placeholder="https://51shucheng.net/… или https://novelarrow.com/…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={busy}
          />
        </div>

        <div className="field">
          <label className="label" htmlFor="follow">
            И ещё глав подряд
          </label>
          <input
            id="follow"
            className="input input--narrow"
            type="number"
            inputMode="numeric"
            min={0}
            max={maxFollow}
            value={follow}
            onChange={(e) => setFollow(Math.min(maxFollow, Math.max(0, Number(e.target.value))))}
            disabled={busy}
          />
          <span className="muted label">
            Сервис пойдёт вперёд по ссылкам «следующая глава». Уже загруженные он
            перешагивает, на сайт за ними не ходит.
          </span>
        </div>

        <button className="button" type="submit" disabled={busy || !url.trim()}>
          {busy ? 'Загружаю…' : 'Загрузить'}
        </button>
      </form>

      {working && (
        <p className="muted progress" role="status" aria-live="polite">
          {describeStatus(chapter?.status ?? 'fetching')}
        </p>
      )}

      {sendError && <ErrorNote kind={sendError.kind} detail={sendError.message} />}

      {requestError && (
        <ErrorNote
          kind={requestError.kind}
          detail={requestError.message}
          onRetry={() => void reload()}
        />
      )}

      {failure && (
        <ErrorNote
          kind={failure.kind}
          detail={failure.message}
          busy={sending}
          onRetry={() => void send(chapter?.url ?? url.trim())}
        />
      )}
    </>
  )
}
