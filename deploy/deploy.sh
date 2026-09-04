#!/usr/bin/env bash
# Выкладка на ВМ (T1.18). Запускается с рабочей машины:
#
#   deploy/deploy.sh [пользователь@адрес]
#
# Что делает: собирает фронт, синхронизирует код, ставит зависимости,
# накатывает миграции, перезапускает сервис.
#
# Чего НЕ делает — и это главное: не трогает `data/`. Там база, профиль
# браузера с куками, за которые заплачено проходом челленджа (T0.3), и
# словарные дампы. Затереть их выкладкой значит потерять то, что не
# восстанавливается из репозитория.
set -euo pipefail

# Адрес машины в репозиторий не пишем: он меняется при пересоздании ВМ, а
# главное — публичный git не место для адреса хоста с открытым SSH. Берём из
# переменной окружения или аргументом.
TARGET="${1:-${CHINESE_READER_HOST:?укажите адрес: deploy/deploy.sh user@host или CHINESE_READER_HOST=user@host}}"
APP_DIR=/opt/chinese-reader
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\n==> %s\n' "$1"; }

say "собираю фронт"
(cd "$ROOT/web" && npm run build)

say "синхронизирую код"
# --delete чистит то, чего в репозитории уже нет: иначе удалённый модуль
# продолжает жить на машине и импортируется как ни в чём не бывало.
# Исключение '/data/' якорное: без ведущего слэша rsync вырезает каталог с
# таким именем на любом уровне, а значит и tests/data с фикстурами — на
# машине их потом не хватает ровно тогда, когда захочется что-то проверить.
rsync -az --delete \
	--exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
	--exclude '.ruff_cache/' --exclude '/data/' \
	"$ROOT/backend/" "$TARGET:$APP_DIR/backend/"

rsync -az --delete "$ROOT/web/dist/" "$TARGET:$APP_DIR/web/"
rsync -az "$ROOT/deploy/" "$TARGET:$APP_DIR/deploy/"

say "зависимости и миграции"
ssh "$TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/chinese-reader

# venv переживает выкладки: пересоздавать его каждый раз — минуты впустую.
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r backend/requirements.txt

cd backend
DATA_DIR=/opt/chinese-reader/data ../venv/bin/alembic upgrade head
REMOTE

say "обновляю юниты"
# Юниты живут в /etc/systemd/system, а выкладка синхронизирует только
# /opt/chinese-reader/deploy — то есть правка юнита сама по себе не доезжала
# никогда. Замечено на --timeout-graceful-shutdown: файл в репозитории
# изменился, systemd продолжал работать по старому, и понять это можно было
# только по поведению. Установка идемпотентна, перезапуск идёт следом.
ssh "$TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
sudo install -m 644 /opt/chinese-reader/deploy/chinese-reader.service /etc/systemd/system/
sudo install -m 644 /opt/chinese-reader/deploy/chinese-reader-backup.service /etc/systemd/system/
sudo install -m 644 /opt/chinese-reader/deploy/chinese-reader-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
REMOTE

say "перезапускаю сервис"
ssh "$TARGET" 'sudo systemctl restart chinese-reader && sleep 2 && systemctl is-active chinese-reader'

say "проверяю живость"
# Ждём ответа, а не спрашиваем один раз. Приложение поднимается около секунды:
# jieba строит префиксный словарь, и `systemctl is-active` отвечает «active»
# раньше, чем uvicorn начинает слушать. Разовый curl из-за этой гонки сообщал
# об отказе на успешной выкладке — а хуже ложного отказа только пропущенный
# настоящий, потому что верить перестают обоим.
ssh "$TARGET" 'for _ in $(seq 30); do
	curl -fsS localhost:8000/api/health && echo && exit 0
	sleep 1
done
echo "сервис не ответил за 30 секунд, последние логи:" >&2
journalctl -u chinese-reader -n 20 --no-pager
exit 1'

say "готово"
