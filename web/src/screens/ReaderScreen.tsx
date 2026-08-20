/**
 * Экран чтения главы.
 *
 * Текст рендерится по токенам (T1.15): тап подсвечивает слово, удержание даёт
 * подсказку с переводом у пальца, двойной тап раскрывает панель, протяжка
 * собирает фразу в пределах предложения. Панель перевода предложения — T1.16,
 * и контракт под неё уже есть: `selected` знает свои офсеты и номер
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
import { SentencePanel, type SaveState } from '../reader/SentencePanel'
import { WordPeek } from '../reader/WordPeek'
import { buildIndex } from '../reader/tokens'
import { useLookup } from '../reader/useLookup'
import { contextOf, useSelection } from '../reader/useSelection'
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

  // Значения ищем только для слов и знаков: во фразе искать нечего, статьи
  // на неё в словаре нет по определению. Запрос идёт и на обычном тапе, когда
  // показывать ещё нечего: словари лежат в той же базе, зато подсказка по
  // удержанию и панель по двойному тапу открываются уже из кэша.
  const lookupWord =
    selection.selected && selection.selected.granularity !== 'phrase'
      ? selection.selected.text
      : null
  const lookup = useLookup(lookupWord)

  // Разбор по знакам возможен только для одного токена: у фразы он был бы
  // разбором нескольких слов подряд, а это уже сам текст главы.
  const activeToken =
    selection.selection && selection.selection.from === selection.selection.to
      ? (index.tokens[selection.selection.from] ?? null)
      : null

  // Обычно idx совпадает с местом в массиве, но полагаться на это не стоит:
  // разъехавшаяся нумерация показала бы перевод соседнего предложения — и
  // выглядело бы это как плохой перевод, а не как ошибка.

  const activeSentence = useMemo(() => {
    const idx = selection.selected?.sentence ?? -1
    if (idx < 0 || !chapter) return null
    const direct = chapter.sentences[idx]
    return direct?.idx === idx ? direct : (chapter.sentences.find((s) => s.idx === idx) ?? null)
  }, [chapter, selection.selected?.sentence])

  // Состояние сохранения живёт по слову: перешли на другое — кнопка снова
  // готова, а «в словаре» не остаётся висеть от прошлого.
  const [saved, setSaved] = useState<Record<string, SaveState>>({})
  const selectedText = selection.selected?.text ?? ''
  const saveState: SaveState = saved[selectedText] ?? 'idle'

  async function saveWord() {
    const selected = selection.selected
    if (!selected || !chapter) return

    setSaved((prev) => ({ ...prev, [selected.text]: 'saving' }))
    const context = contextOf(index, content, selected)
    try {
      await api.saveWord({
        headword: selected.text,
        reading: lookup?.entries[0]?.reading ?? null,
        context: context
          ? { ...context, chapter_id: chapter.id, sentence_id: activeSentence?.id ?? null }
          : undefined,
      })
      setSaved((prev) => ({ ...prev, [selected.text]: 'saved' }))
    } catch {
      setSaved((prev) => ({ ...prev, [selected.text]: 'failed' }))
    }
  }

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

      {selection.selected && selection.view.kind === 'panel' && (
        <SentencePanel
          selected={selection.selected}
          sentence={activeSentence}
          lookup={lookup}
          token={activeToken}
          charOffset={selection.charSplit?.charOffset ?? null}
          onNarrow={selection.onNarrow}
          saveState={saveState}
          onSave={() => void saveWord()}
          onClose={selection.clear}
        />
      )}

      {selection.selected && selection.view.kind === 'peek' && (
        <WordPeek
          term={selection.selected.text}
          lookup={lookup}
          x={selection.view.x}
          y={selection.view.y}
        />
      )}
    </>
  )
}
