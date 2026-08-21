/**
 * Полоса управления чтением: что показывать и слушать ли это вслух.
 *
 * Стоит над текстом и остаётся на месте при прокрутке. Переключаться между
 * оригиналом и переводом читатель будет посреди главы, а не перед ней:
 * решение «дальше я не разберу» приходит на середине абзаца, и лезть за ним
 * наверх страницы — значит терять место, на котором остановился.
 *
 * Переключатель сделан парой кнопок с `aria-pressed`, а не `<select>`: два
 * варианта, оба видны сразу, и нажатие стоит одного касания вместо трёх.
 *
 * Кнопка озвучки не прячется, когда синтез не настроен. Спрятанная кнопка
 * неотличима от отсутствующей возможности, а нажатая — честно скажет, чего не
 * хватает (`speech_failed` в errors.ts).
 */

import type { ReadingMode } from '../api'
import type { SpeechState } from './useSpeech'

const MODES: { value: ReadingMode; label: string; hint: string }[] = [
  { value: 'source', label: 'Оригинал', hint: 'Текст главы с разбором по словам' },
  { value: 'translation', label: 'Перевод', hint: 'Русский перевод целиком' },
]

export function ReaderBar({
  mode,
  onMode,
  speech,
}: {
  mode: ReadingMode
  onMode: (next: ReadingMode) => void
  speech: SpeechState
}) {
  return (
    <div className="readerbar">
      <div className="switch" role="group" aria-label="Что показывать">
        {MODES.map((item) => (
          <button
            className={`switch__option${mode === item.value ? ' is-selected' : ''}`}
            key={item.value}
            type="button"
            aria-pressed={mode === item.value}
            title={item.hint}
            onClick={() => onMode(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <button
        className="readerbar__play"
        type="button"
        disabled={!speech.available}
        aria-pressed={speech.playing}
        title={
          speech.available
            ? 'Читать перевод вслух'
            : 'Переводов ещё нет — озвучивать нечего'
        }
        onClick={speech.toggle}
      >
        <span aria-hidden="true">{speech.playing ? '❚❚' : '▶'}</span>
        <span className="readerbar__label">{speech.playing ? 'Пауза' : 'Слушать'}</span>
      </button>

      {speech.playing && (
        <button className="readerbar__stop" type="button" onClick={speech.stop}>
          Стоп
        </button>
      )}
    </div>
  )
}
