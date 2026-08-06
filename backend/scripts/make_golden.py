"""Пересборка золотого набора сегментации (segmentation.md §3).

    PYTHONPATH=. python scripts/make_golden.py

Набор пересобирается **осознанно**, а не на каждом прогоне тестов: его смысл
в том, чтобы смена или донастройка сегментатора давала видимый diff, а не
молча уезжала. Поэтому запускать это стоит, только когда изменение разметки
ожидаемо, и глазами смотреть, что именно изменилось.

Два файла на выходе:

* `segment-golden.txt` — предложения, размеченные на токены через `|`. Файл
  самодостаточен: сам текст восстанавливается склейкой, отдельного входа не
  нужно и разъехаться им негде;
* `segment-userdict.txt` — вырезка боевого userdict: только слова, которые
  встречаются в этих предложениях. Целиком он весит 300 КБ и живёт в `data/`
  вне git, а без него снапшот проверял бы не ту конфигурацию, что в бою.

Предложения берутся вразброс по всей главе и короткие: нужна выборка разных
явлений (диалог, описание, числа), а не связный отрывок чужого романа в
репозитории.
"""

import sys
from pathlib import Path

from app.adapters.shucheng import ShuchengAdapter
from app.config import settings
from app.lang.normalize import normalize
from app.lang.segment import Segmenter
from app.lang.sentences import split_sentences

GOLDEN_SENTENCES = 25
# Длинные предложения в наборе не нужны: явлений в них не больше, а diff при
# донастройке читать труднее.
MAX_SENTENCE_CHARS = 40

BACKEND = Path(__file__).resolve().parents[1]
OUT_DIR = BACKEND / "tests" / "data"

fixture = settings.data_dir / "chapter-fixture.html"
if not fixture.exists():
    sys.exit(f"нет фикстуры главы: {fixture} (снимается в T0.5)")

raw = ShuchengAdapter().parse_chapter(
    fixture.read_text(encoding="utf-8"), "https://51shucheng.net/"
)
canon = normalize(raw.paragraphs)
spans = split_sentences(canon)

short = [s.text for s in spans if len(s.text) <= MAX_SENTENCE_CHARS]
step = max(1, len(short) // GOLDEN_SENTENCES)
picked = short[::step][:GOLDEN_SENTENCES]

# Вырезка userdict — до сегментации: с ней же будет работать и тест.
full = settings.userdict_path.read_text(encoding="utf-8").splitlines()
subset = [line for line in full if (w := line.split(" ")[0]) and any(w in s for s in picked)]

OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "segment-userdict.txt").write_text("\n".join(subset) + "\n", encoding="utf-8")

seg = Segmenter(OUT_DIR / "segment-userdict.txt")
lines = ["|".join(t.surface for t in seg.segment(s)) for s in picked]
(OUT_DIR / "segment-golden.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"предложений: {len(picked)}, слов в вырезке userdict: {len(subset)}")
print(f"токенов: {sum(len(line.split('|')) for line in lines)}")
