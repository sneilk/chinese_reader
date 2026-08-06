/**
 * Сообщение об отказе: заголовок, совет, техническая подробность и действие.
 *
 * Один компонент на оба экрана — иначе «челлендж» на вводе и «челлендж» при
 * чтении со временем разъедутся по формулировкам.
 */

import { describeError } from '../errors'

export function ErrorNote({
  kind,
  detail,
  onRetry,
  retryLabel = 'Попробовать снова',
  busy = false,
}: {
  kind: string
  detail?: string
  onRetry?: () => void
  retryLabel?: string
  busy?: boolean
}) {
  const info = describeError(kind)
  const tone = info.readable ? 'note--warning' : 'note--error'

  return (
    <div className={`note ${tone}`} role="alert">
      <div className="note__title">{info.title}</div>
      <p className="note__advice">{info.advice}</p>

      {/* Технический код рядом: он нужен, когда сообщение не помогло. */}
      <p className="note__detail">
        <code>{kind}</code>
        {detail ? ` · ${detail}` : ''}
      </p>

      {onRetry && info.retryable && (
        <button className="button button--quiet" type="button" onClick={onRetry} disabled={busy}>
          {busy ? 'Пробую…' : retryLabel}
        </button>
      )}
    </div>
  )
}
