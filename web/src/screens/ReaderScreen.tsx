/**
 * Экран чтения главы.
 *
 * Текст рендерится по токенам (T1.15): тап подсвечивает слово, удержание даёт
 * подсказку с переводом у пальца, двойной тап раскрывает панель, протяжка
 * собирает фразу в пределах предложения. Панель перевода предложения — T1.16.
 *
 * Отказ перевода не прячет текст: глава читаема начиная с `segmented`, и
 * предупреждение висит над ней, а не вместо неё.
 *
 * ## Два вида одной главы
 *
 * Оригинал и перевод — это одна разметка в двух проекциях, а не два экрана.
 * Абзацы стоят на тех же местах, переключение не сдвигает текст, а озвучка
 * идёт в обоих: подсветка едет либо по токенам оригинала, либо по фразам
 * перевода, но предложение подсвечивается одно и то же.
 *
 * Жесты живут только в оригинале — в переводе разбирать нечего, он и так
 * по-русски. Поэтому `useTokenGestures` получает признак «текст на экране»:
 * без него возврат к оригиналу оставил бы жесты мёртвыми.
 *
 * ## Язык приезжает с главой
 *
 * Ни один экран его не угадывает. От `chapter.lang` зависят гарнитура текста,
 * язык запроса к словарю и язык, под которым слово ляжет в личный словарь —
 * три места, где ошибка выглядела бы как «словарь сломался».
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, isPending, isReadable } from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { describeStatus } from '../errors'
import { ChapterText } from '../reader/ChapterText'
import { ReaderBar } from '../reader/ReaderBar'
import { SentencePanel, type SaveState } from '../reader/SentencePanel'
import { TranslationText } from '../reader/TranslationText'
import { WordPeek } from '../reader/WordPeek'
import { buildIndex } from '../reader/tokens'
import { useLookup } from '../reader/useLookup'
import { useReadingMode } from '../reader/useReadingMode'
import { contextOf, useSelection } from '../reader/useSelection'
import { useSpeech } from '../reader/useSpeech'
import { useTokenGestures } from '../reader/useTokenGestures'
import { hrefFor, navigate } from '../router'
import { useChapter } from '../useChapter'

/** Держать озвучиваемую фразу на виду: слушать главу, глядя в её начало, незачем. */
function useFollowSpeech(sentence: number, playing: boolean): void {
  useEffect(() => {
    if (!playing || sentence < 0) return
    const node = document.querySelector('.is-speaking')
    if (!node) return
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    node.scrollIntoView({ block: 'center', behavior: calm ? 'auto' : 'smooth' })
  }, [sentence, playing])
}

/**
 * Сколько глав вперёд предлагать, пока сервер не сказал свой предел.
 *
 * Не вторая копия настройки, а значение на те полсекунды, пока не приехало
 * настоящее: держать его константой значило бы однажды предложить больше, чем
 * примут в ответ.
 */
const FALLBACK_AHEAD = 10

export function ReaderScreen({ id }: { id: number }) {
  const { chapter, requestError, loading, reload } = useChapter(id)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<ApiError | null>(null)
  const [loadingNext, setLoadingNext] = useState(false)
  const [ahead, setAhead] = useState(0)
  const [maxAhead, setMaxAhead] = useState(FALLBACK_AHEAD)
  const [aheadNote, setAheadNote] = useState<string | null>(null)
  const [relinking, setRelinking] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Предел спрашиваем у сервера: он же его и применяет. Отказ глушим — без
  // предела экран работает, просто с догадкой.
  useEffect(() => {
    api
      .health()
      .then((health) => setMaxAhead(health.limits.max_chapters_per_run))
      .catch(() => undefined)
  }, [])

  const [mode, setMode] = useReadingMode()
  const content = chapter?.content ?? ''
  const lang = chapter?.lang ?? 'zh'

  // Индекс перестраивается только при смене текста главы. Держать в
  // зависимостях `tokens` и `sentences` нельзя, хотя тянет: разбор ответа даёт
  // новые массивы на каждый опрос, и 2500 токенов раскладывались бы заново
  // каждые полторы секунды, пока идёт перевод.
  //
  // Взамен достаточно канона: токены и границы предложений считаны по нему и
  // без него не меняются. Переводы меняются — но в индекс они не входят.
  const index = useMemo(
    () => buildIndex(content, chapter?.tokens ?? [], chapter?.sentences ?? []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- см. выше
    [chapter?.id, content],
  )

  const speech = useSpeech(chapter)
  const showSource = mode === 'source'

  const selection = useSelection(index, content)
  useTokenGestures(containerRef, index, selection, showSource)

  // Значения ищем только для слов и знаков: во фразе искать нечего, статьи
  // на неё в словаре нет по определению. Запрос идёт и на обычном тапе, когда
  // показывать ещё нечего: словари лежат в той же базе, зато подсказка по
  // удержанию и панель по двойному тапу открываются уже из кэша.
  const lookupWord =
    selection.selected && selection.selected.granularity !== 'phrase'
      ? selection.selected.text
      : null
  const lookup = useLookup(lookupWord, lang)

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

  // Озвучка адресуется номером предложения, а индексы главы — местом в
  // массиве. Здесь эти два числа сходятся, и только здесь.
  const speaking = speech.current
  const speakingTokens = useMemo(() => {
    if (speaking < 0 || !chapter) return null
    const position = chapter.sentences.findIndex((s) => s.idx === speaking)
    if (position < 0) return null
    const from = index.firstToken[position]
    const to = index.lastToken[position]
    return from >= 0 && to >= 0 ? { from, to } : null
  }, [speaking, chapter, index])

  useFollowSpeech(speaking, speech.playing)

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
        lang: chapter.lang,
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

  /**
   * Загрузить следующую главу и перейти к ней сразу.
   *
   * Ждать здесь нечего: экран новой главы сам опрашивает статус и показывает
   * прогресс, а стоять на прочитанной главе со спиннером — значит смотреть на
   * то, что уже прочитано.
   */
  async function openNext() {
    if (!chapter?.next_url || loadingNext) return
    setLoadingNext(true)
    setRetryError(null)
    try {
      const accepted = await api.createChapter(chapter.next_url)
      speech.stop()
      navigate({ name: 'chapter', id: accepted.id })
    } catch (e) {
      setRetryError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setLoadingNext(false)
    }
  }

  /**
   * Загрузить пачку глав вперёд, оставшись на этой.
   *
   * Обход идёт от текущей главы: `follow` на уже загруженной главе на сайт за
   * ней не ходит, а продолжает цепочку с неё (RFC §4). Уходить никуда не надо
   * — читатель дочитывает эту главу, пока грузятся следующие.
   */
  async function loadAhead() {
    if (!chapter || ahead < 1 || loadingNext) return
    setLoadingNext(true)
    setRetryError(null)
    setAheadNote(null)
    try {
      await api.createChapter(chapter.url, ahead)
      setAheadNote(
        `Загружаю ${ahead} гл. вперёд — можно читать дальше, работа идёт фоном. ` +
          'Загруженное появится в оглавлении книги.',
      )
    } catch (e) {
      setRetryError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setLoadingNext(false)
    }
  }

  /**
   * Спросить у сайта заново, куда ведёт глава.
   *
   * Нужно главам, загруженным до того, как адаптер научился читать ссылку
   * вперёд: у них её нет и само не появится. Текст и переводы не трогаются.
   */
  async function relink() {
    setRelinking(true)
    setRetryError(null)
    try {
      await api.relinkChapter(id)
      await reload()
    } catch (e) {
      setRetryError(e instanceof ApiError ? e : new ApiError('network', String(e)))
    } finally {
      setRelinking(false)
    }
  }

  if (requestError) {
    return <ErrorNote kind={requestError.kind} detail={requestError.message} onRetry={reload} />
  }

  if (loading || !chapter) {
    return <p className="muted">Загружаю…</p>
  }

  const missingTranslation = chapter.sentences.some((s) => s.translation === null)
  const readable = isReadable(chapter.status) && Boolean(chapter.content)

  return (
    <>
      {chapter.title && (
        <h1 className="reader__title" lang={lang === 'zh' ? 'zh-Hans' : 'en'}>
          {chapter.title}
        </h1>
      )}

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

      {readable && <ReaderBar mode={mode} onMode={setMode} speech={speech} />}

      {speech.failure && <ErrorNote kind={speech.failure} detail="" />}

      {readable ? (
        showSource ? (
          <ChapterText
            index={index}
            selection={selection.selection}
            charSplit={selection.charSplit}
            speaking={speakingTokens}
            lang={lang}
            containerRef={containerRef}
          />
        ) : (
          <TranslationText
            index={index}
            content={content}
            sentences={chapter.sentences}
            lang={lang}
            speaking={speaking}
            onPlayFrom={speech.playFrom}
          />
        )
      ) : (
        !isPending(chapter.status) && !chapter.error && <p className="muted">Текста пока нет.</p>
      )}

      {readable && (
        <nav className="chapternav" aria-label="Переход по главам">
          {chapter.next_chapter_id !== null ? (
            <a
              className="button"
              href={hrefFor({ name: 'chapter', id: chapter.next_chapter_id })}
              onClick={speech.stop}
            >
              Следующая глава →
            </a>
          ) : chapter.next_url ? (
            <button
              className="button"
              type="button"
              onClick={() => void openNext()}
              disabled={loadingNext}
            >
              {loadingNext ? 'Загружаю…' : 'Загрузить следующую →'}
            </button>
          ) : (
            // Ссылки нет по одной из двух причин, и различить их можно только
            // сходив на сайт: книга кончилась — или главу загрузили тогда,
            // когда ссылки вперёд не читались вовсе.
            <div className="chapternav__missing">
              <p className="muted">Ссылки на следующую главу у этой главы не записано.</p>
              <button
                className="button button--quiet"
                type="button"
                onClick={() => void relink()}
                disabled={relinking}
              >
                {relinking ? 'Спрашиваю сайт…' : 'Найти ссылку заново'}
              </button>
            </div>
          )}

          {/* Загрузка вперёд не уводит с главы: пока грузятся следующие,
              читатель дочитывает эту. Ждать тут нечего. */}
          {(chapter.next_url || chapter.next_chapter_id !== null) && (
            <form
              className="chapternav__ahead"
              onSubmit={(e) => {
                e.preventDefault()
                void loadAhead()
              }}
            >
              <label className="label" htmlFor="ahead">
                Загрузить вперёд
              </label>
              <input
                id="ahead"
                className="input input--narrow"
                type="number"
                inputMode="numeric"
                min={0}
                max={maxAhead}
                value={ahead}
                onChange={(e) =>
                  setAhead(Math.min(maxAhead, Math.max(0, Number(e.target.value))))
                }
                disabled={loadingNext}
              />
              <button className="button button--quiet" type="submit" disabled={loadingNext || !ahead}>
                {loadingNext ? 'Ставлю…' : `Ещё ${ahead || ''} гл.`.trim()}
              </button>
              <span className="muted label">до {maxAhead}; всю книгу — с экрана книги</span>
            </form>
          )}

          {aheadNote && (
            <p className="muted progress" role="status" aria-live="polite">
              {aheadNote}
            </p>
          )}
        </nav>
      )}

      {showSource && selection.selected && selection.view.kind === 'panel' && (
        <SentencePanel
          selected={selection.selected}
          sentence={activeSentence}
          lookup={lookup}
          token={activeToken}
          lang={lang}
          charOffset={selection.charSplit?.charOffset ?? null}
          onNarrow={selection.onNarrow}
          saveState={saveState}
          onSave={() => void saveWord()}
          onClose={selection.clear}
        />
      )}

      {showSource && selection.selected && selection.view.kind === 'peek' && (
        <WordPeek
          term={selection.selected.text}
          lookup={lookup}
          lang={lang}
          x={selection.view.x}
          y={selection.view.y}
        />
      )}
    </>
  )
}
