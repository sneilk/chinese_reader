/**
 * Экран чтения главы.
 *
 * Текст рендерится по токенам (T1.15): тап — слово, протяжка — фраза в
 * пределах предложения, долгий тап — символ. Панель перевода предложения —
 * T1.16, и контракт под неё уже есть: `selected` знает свои офсеты и номер
 * предложения.
 *
 * Отказ перевода не прячет текст: глава читаема начиная с `segmented`, и
 * предупреждение висит над ней, а не вместо неё.
 */

import { useMemo, useRef, useState } from 'react'
import { ApiError, api, isPending, isReadable } from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { describeStatus } from '../errors'
import { ChapterText } from '../reader/ChapterText'
import { SentencePanel } from '../reader/SentencePanel'
import { buildIndex } from '../reader/tokens'
import { useSelection } from '../reader/useSelection'
import { useTokenGestures } from '../reader/useTokenGestures'
import { useChapter } from '../useChapter'

export function ReaderScreen({ id }: { id: number }) {
  const { chapter, requestError, loading, reload } = useChapter(id)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<ApiError | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const content = chapter?.content ?? ''
  // Индекс перестраивается только при смене главы: опрос статуса приносит
  // тот же текст, и пересобирать 2500 токенов на каждый ответ незачем.
  const index = useMemo(
    () => buildIndex(content, chapter?.tokens ?? [], chapter?.sentences ?? []),
    [content, chapter?.tokens, chapter?.sentences],
  )

  const selection = useSelection(index, content)
  useTokenGestures(containerRef, index, selection)

  // Обычно idx совпадает с местом в массиве, но полагаться на это не стоит:
  // разъехавшаяся нумерация показала бы перевод соседнего предложения — и
  // выглядело бы это как плохой перевод, а не как ошибка.
  const activeSentence = useMemo(() => {
    const idx = selection.selected?.sentence ?? -1
    if (idx < 0 || !chapter) return null
    const direct = chapter.sentences[idx]
    return direct?.idx === idx ? direct : (chapter.sentences.find((s) => s.idx === idx) ?? null)
  }, [chapter, selection.selected?.sentence])

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
          <button
            className="button button--quiet"
            onClick={() => void retranslate()}
            disabled={retrying}
          >
            {retrying ? 'Перевожу…' : 'Дозалить перевод'}
          </button>
        </div>
      )}

      {retryError && <ErrorNote kind={retryError.kind} detail={retryError.message} />}

      {isReadable(chapter.status) && chapter.content ? (
        <ChapterText
          index={index}
          selection={selection.selection}
          charSplit={selection.charSplit}
          containerRef={containerRef}
        />
      ) : (
        !isPending(chapter.status) && !chapter.error && <p className="muted">Текста пока нет.</p>
      )}

      {selection.selected && (
        <SentencePanel
          selected={selection.selected}
          sentence={activeSentence}
          onClose={selection.clear}
        />
      )}
    </>
  )
}
