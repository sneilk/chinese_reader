#!/usr/bin/env bash
# Разовая настройка машины: Caddy, юнит сервиса, пароль (T1.17, T1.18).
#
# Запускается НА ВМ, от пользователя с sudo. Повторный запуск безопасен:
# пароль и профиль браузера не перезаписываются, данные не трогаются.
#
#   sudo bash setup-vm.sh <домен>
#
# Пароль сюда не передаётся и в вывод не печатается: он генерируется на месте
# и остаётся в /etc/chinese-reader/webauth с правами 600. Посмотреть —
# `sudo cat /etc/chinese-reader/webauth`.
set -euo pipefail

DOMAIN="${1:?нужен домен, например 89-169-158-177.sslip.io}"
APP_DIR=/opt/chinese-reader
APP_USER=yc-user
SECRET_DIR=/etc/chinese-reader

echo "==> ставлю Caddy"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq caddy sqlite3 rsync >/dev/null

echo "==> каталоги"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR/data" "$APP_DIR/web" "$APP_DIR/backend"
install -d -m 700 "$SECRET_DIR"

echo "==> пароль для входа"
if [ -f "$SECRET_DIR/webauth" ]; then
	echo "    уже есть, не трогаю"
else
	# Пароль генерируется здесь и никуда не уезжает: ни в вывод, ни в git.
	# Без конвейера намеренно: `tr … | head -c N` под `set -o pipefail`
	# завершается ошибкой, потому что head закрывает поток и tr получает
	# SIGPIPE — скрипт падает ровно на генерации пароля.
	password="$(openssl rand -hex 12)"
	printf 'логин: reader\nпароль: %s\n' "$password" >"$SECRET_DIR/webauth"
	chmod 600 "$SECRET_DIR/webauth"
	echo "$password" >"$SECRET_DIR/.webauth-plain"
	chmod 600 "$SECRET_DIR/.webauth-plain"
fi
password_hash="$(caddy hash-password --plaintext "$(cat "$SECRET_DIR/.webauth-plain")")"

echo "==> Caddyfile для $DOMAIN"
sed -e "s|{{DOMAIN}}|$DOMAIN|g" -e "s|{{PASSWORD_HASH}}|$password_hash|g" \
	"$APP_DIR/deploy/Caddyfile.template" >/etc/caddy/Caddyfile
chmod 644 /etc/caddy/Caddyfile

echo "==> юнит сервиса"
install -m 644 "$APP_DIR/deploy/chinese-reader.service" /etc/systemd/system/chinese-reader.service
install -m 644 "$APP_DIR/deploy/chinese-reader-backup.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/chinese-reader-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chinese-reader-backup.timer >/dev/null
systemctl enable --now xvfb.service >/dev/null 2>&1 || true
# enable, а не только start: без него сервис не переживёт перезагрузку ВМ, и
# обнаружится это в худший момент — когда машина перезагрузится сама.
systemctl enable chinese-reader.service >/dev/null

echo "==> проверяю конфигурацию Caddy"
caddy validate --config /etc/caddy/Caddyfile >/dev/null

systemctl enable caddy >/dev/null
systemctl restart caddy

echo
echo "готово. пароль: sudo cat $SECRET_DIR/webauth"
