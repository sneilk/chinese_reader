/**
 * Экран чтения главы.
 *
 * Каркас (T1.13) плюс сообщения и дозалив перевода (T1.14). Рендер по токенам
 * со спанами и жестами — T1.15, панель перевода предложения — T1.16.
 *
 * Отказ перевода здесь не прячет текст: глава читаема начиная с `segmented`,
 * и предупреждение висит над ней, а не вместо неё.
 */

import { useState } from 'react'
import { ApiError, api, isPending, isReadable } from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { describeStatus } from '../errors'
import { useChapter } from '../useChapter'

export function ReaderScreen({ id }: { id: number }) {
  const { chapter, requestError, loading, reload } = useChapter(id)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<ApiError | null>(null)

  async function retranslate() {
    setRetrying(true)
    setRetryError(null)
    try {
      await api.translateChapter(id)
      await reload()
    } catch (e) {
      setRetryError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setRetrying(false)
    }
  }

  if (requestError) {
    return <ErrorNote kind={requestError.kind} detail={requestError.message} onRetry={reload} />
  }

  if (loading || !chapter) {
    return <p className="muted">Загружаю…</p>
  }

  const missingTranslation = chapter.sentences.some((s) => s.translation === null)

  return (
    <>
      {chapter.title && <h1 className="reader__title">{chapter.title}</h1>}

      {chapter.error && (
        <ErrorNote
          kind={chapter.error.kind}
          detail={chapter.error.message}
          busy={retrying}
          onRetry={reload}
        />
      )}

      {isPending(chapter.status) && (
        <p className="muted progress" role="status" aria-live="polite">
          {describeStatus(chapter.status)}
        </p>
      )}

      {/* Отдельная кнопка, а не общий «повтор»: перевод дозаливается, уже
          переведённые предложения не переотправляются (RFC §4). */}
      {isReadable(chapter.status) && missingTranslation && !isPending(chapter.status) && (
        <div className="row">
          <button className="button button--quiet" onClick={() => void retranslate()} disabled={retrying}>
            {retrying ? 'Перевожу…' : 'Дозалить перевод'}
          </button>
        </div>
      )}

      {retryError && <ErrorNote kind={retryError.kind} detail={retryError.message} />}

      {isReadable(chapter.status) && chapter.content ? (
        <div className="reader" lang="zh-Hans">
          {/* Абзац равен строке канона (normalize.py), поэтому делим по \n.
              В T1.15 внутри абзацев появятся спаны по токенам. */}
          {chapter.content.split('\n').map((paragraph, i) => (
            <p className="reader__p" key={i}>
              {paragraph}
            </p>
          ))}
        </div>
      ) : (
        !isPending(chapter.status) && !chapter.error && <p className="muted">Текста пока нет.</p>
      )}
    </>
  )
}
