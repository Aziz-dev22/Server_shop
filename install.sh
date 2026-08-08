#!/usr/bin/env bash
# نصب‌کننده Hetzner Shop Bot + پنل مدیریت تحت وب، با منوی چهار گزینه‌ای
#
# اجرا (روش امن‌تر روی سرورهای مینیمال):
#   curl -fsSL -o install.sh https://raw.githubusercontent.com/Aziz-dev22/server_shop/main/install.sh
#   sudo bash install.sh
#
set -e

REPO_URL="https://github.com/Aziz-dev22/server_shop.git"
APP_DIR="/opt/hetzner-shop-bot"
SERVICE_NAME="hetzner-shop-bot"
PANEL_SERVICE_NAME="hetzner-shop-panel"
PANEL_PORT="8088"
BACKUP_DIR="/root/hetzner-shop-bot-backups"

if [ "$EUID" -ne 0 ]; then
  echo "لطفاً این اسکریپت را با دسترسی روت اجرا کنید:"
  echo "  sudo bash install.sh"
  exit 1
fi

# ---------------------------------------------------------------------------
# عملیات: نصب
# ---------------------------------------------------------------------------
do_install() {
  echo "================================================"
  echo "  نصب Hetzner Shop Bot + پنل مدیریت تحت وب"
  echo "================================================"

  echo "[1/5] بروزرسانی و نصب پیش‌نیازها..."
  apt-get update -y
  apt-get install -y python3 python3-venv python3-pip git curl

  echo
  echo "[2/5] دریافت کد از گیت‌هاب..."
  if [ -d "$APP_DIR/.git" ]; then
    echo "پوشه نصب از قبل وجود دارد. برای نصب تازه، ابتدا گزینه «حذف کامل» را بزنید یا از گزینه «آپدیت» استفاده کنید."
    exit 1
  fi
  rm -rf "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"

  echo
  echo "[3/5] لطفاً اطلاعات زیر را وارد کنید:"
  read -rp "توکن ربات تلگرام (از @BotFather): " BOT_TOKEN
  read -rp "شناسه عددی تلگرام ادمین (از @userinfobot): " ADMIN_IDS
  read -rp "نام کاربری دلخواه برای ورود به پنل تحت وب [admin]: " PANEL_USERNAME
  PANEL_USERNAME=${PANEL_USERNAME:-admin}
  read -rp "رمز عبور دلخواه برای ورود به پنل تحت وب: " PANEL_PASSWORD
  while [ -z "$PANEL_PASSWORD" ]; do
    echo "رمز عبور نمی‌تواند خالی باشد."
    read -rp "رمز عبور دلخواه برای ورود به پنل تحت وب: " PANEL_PASSWORD
  done

  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
DB_PATH=shop.db
PANEL_USERNAME=${PANEL_USERNAME}
PANEL_PASSWORD=${PANEL_PASSWORD}
EOF

  echo
  echo "[4/5] ساخت محیط مجازی پایتون و نصب کتابخانه‌ها..."
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt

  echo
  echo "[5/5] ساخت و اجرای سرویس‌های systemd (ربات + پنل تحت وب)..."
  write_service_file
  write_panel_service_file
  systemctl daemon-reload
  systemctl enable ${SERVICE_NAME}
  systemctl enable ${PANEL_SERVICE_NAME}
  systemctl restart ${SERVICE_NAME}
  systemctl restart ${PANEL_SERVICE_NAME}

  SERVER_IP="$(curl -s -4 ifconfig.me 2>/dev/null || echo YOUR_SERVER_IP)"

  echo
  echo "================================================"
  echo "✅ نصب با موفقیت انجام شد!"
  echo
  echo "🌐 پنل مدیریت تحت وب:  http://${SERVER_IP}:${PANEL_PORT}"
  echo "   نام کاربری: ${PANEL_USERNAME}"
  echo "   رمز عبور: (همانی که وارد کردید)"
  echo
  echo "مراحل بعدی:"
  echo "  1) به ربات تلگرام پیام /start بدهید."
  echo "  2) وارد پنل تحت وب یا /admin در تلگرام شوید."
  echo "  3) در «API های Hetzner» یک API Key اضافه کنید."
  echo "  4) در «پلن‌ها» حداقل یک پلن با درصد سود دلخواه تعریف کنید."
  echo "  5) اگر پرداخت خودکار ارز دیجیتال می‌خواهید، در «تنظیمات» کلید OxaPay را وارد کنید."
  echo "  6) در «تنظیمات» آدرس واریز دستی (کارت/ولت) را وارد کنید."
  echo "================================================"
  echo "⚠️ پورت ${PANEL_PORT} باید در فایروال سرور باز باشد (مثلاً: ufw allow ${PANEL_PORT})."
  echo "   برای امنیت بیشتر توصیه می‌شود پنل را پشت یک دامنه + HTTPS (nginx/certbot) قرار دهید."
  echo "================================================"
}

# ---------------------------------------------------------------------------
# عملیات: آپدیت
# ---------------------------------------------------------------------------
do_update() {
  echo "================================================"
  echo "  بروزرسانی Hetzner Shop Bot + پنل تحت وب"
  echo "================================================"
  if [ ! -d "$APP_DIR/.git" ]; then
    echo "نصب نشده است. ابتدا گزینه «نصب» را انتخاب کنید."
    exit 1
  fi
  echo "[1/4] توقف موقت سرویس‌ها..."
  systemctl stop ${SERVICE_NAME} 2>/dev/null || true
  systemctl stop ${PANEL_SERVICE_NAME} 2>/dev/null || true

  echo "[2/4] دریافت آخرین نسخه کد..."
  git -C "$APP_DIR" pull --ff-only

  echo "[3/4] بروزرسانی کتابخانه‌ها..."
  cd "$APP_DIR"
  ./venv/bin/pip install --upgrade pip -q
  ./venv/bin/pip install -r requirements.txt -q

  # اگر از نسخه قدیمی‌تر بدون پنل تحت وب آپدیت می‌کنید، تنظیمات پنل را اضافه کن
  if ! grep -q "^PANEL_USERNAME=" .env 2>/dev/null; then
    echo "PANEL_USERNAME=admin" >> .env
  fi
  if ! grep -q "^PANEL_PASSWORD=" .env 2>/dev/null; then
    read -rp "رمز عبور دلخواه برای ورود به پنل تحت وب جدید: " NEW_PANEL_PASSWORD
    echo "PANEL_PASSWORD=${NEW_PANEL_PASSWORD}" >> .env
  fi

  echo "[4/4] بازسازی سرویس‌ها و ری‌استارت..."
  write_service_file
  write_panel_service_file
  systemctl daemon-reload
  systemctl enable ${PANEL_SERVICE_NAME} 2>/dev/null || true
  systemctl restart ${SERVICE_NAME}
  systemctl restart ${PANEL_SERVICE_NAME}

  echo
  echo "✅ آپدیت انجام شد. فایل .env و دیتابیس شما دست‌نخورده باقی ماندند."
  echo "🌐 پنل تحت وب روی پورت ${PANEL_PORT} در دسترس است."
}

# ---------------------------------------------------------------------------
# عملیات: بکاپ کامل
# ---------------------------------------------------------------------------
do_backup() {
  echo "================================================"
  echo "  بکاپ کامل Hetzner Shop Bot"
  echo "================================================"
  if [ ! -d "$APP_DIR" ]; then
    echo "نصب نشده است، چیزی برای بکاپ‌گیری وجود ندارد."
    exit 1
  fi
  mkdir -p "$BACKUP_DIR"
  TS="$(date +%Y%m%d-%H%M%S)"
  BACKUP_FILE="${BACKUP_DIR}/hetzner-shop-bot-backup-${TS}.tar.gz"

  echo "در حال ساخت بکاپ از .env و دیتابیس و کل پوشه پروژه..."
  tar --exclude="${APP_DIR}/venv" -czf "$BACKUP_FILE" -C "$(dirname "$APP_DIR")" "$(basename "$APP_DIR")"

  echo
  echo "✅ بکاپ با موفقیت ساخته شد:"
  echo "   ${BACKUP_FILE}"
  echo
  echo "برای دانلود این فایل به کامپیوتر خودتان می‌توانید از دستور زیر (در کامپیوتر خودتان) استفاده کنید:"
  echo "   scp root@$(curl -s -4 ifconfig.me 2>/dev/null || echo SERVER_IP):${BACKUP_FILE} ."
}

# ---------------------------------------------------------------------------
# عملیات: حذف کامل
# ---------------------------------------------------------------------------
do_uninstall() {
  echo "================================================"
  echo "  حذف کامل Hetzner Shop Bot + پنل تحت وب"
  echo "================================================"
  echo "⚠️  این عملیات سرویس‌ها، فایل‌های پروژه و دیتابیس (shop.db) را برای همیشه حذف می‌کند."
  read -rp "آیا مطمئن هستید؟ برای تایید عبارت yes را تایپ کنید: " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "عملیات لغو شد."
    exit 0
  fi

  read -rp "آیا قبل از حذف، یک بکاپ کامل گرفته شود؟ (y/n): " DO_BACKUP
  if [ "$DO_BACKUP" = "y" ] || [ "$DO_BACKUP" = "Y" ]; then
    do_backup
  fi

  echo "در حال توقف و حذف سرویس‌ها..."
  systemctl stop ${SERVICE_NAME} 2>/dev/null || true
  systemctl disable ${SERVICE_NAME} 2>/dev/null || true
  rm -f /etc/systemd/system/${SERVICE_NAME}.service
  systemctl stop ${PANEL_SERVICE_NAME} 2>/dev/null || true
  systemctl disable ${PANEL_SERVICE_NAME} 2>/dev/null || true
  rm -f /etc/systemd/system/${PANEL_SERVICE_NAME}.service
  systemctl daemon-reload

  echo "در حال حذف فایل‌های پروژه..."
  rm -rf "$APP_DIR"

  echo
  echo "✅ حذف کامل انجام شد."
}

# ---------------------------------------------------------------------------
# ساخت فایل‌های سرویس systemd
# ---------------------------------------------------------------------------
write_service_file() {
  cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Hetzner Shop Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python3 ${APP_DIR}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

write_panel_service_file() {
  cat > /etc/systemd/system/${PANEL_SERVICE_NAME}.service <<EOF
[Unit]
Description=Hetzner Shop Web Admin Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python3 -m uvicorn panel:app --host 0.0.0.0 --port ${PANEL_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

# ---------------------------------------------------------------------------
# منوی اصلی
# ---------------------------------------------------------------------------
echo "================================================"
echo "  مدیریت Hetzner Shop Bot + پنل تحت وب"
echo "================================================"
echo "1) نصب"
echo "2) آپدیت (بروزرسانی به آخرین نسخه)"
echo "3) بکاپ کامل"
echo "4) حذف کامل"
echo "================================================"
read -rp "یک گزینه را انتخاب کنید [1-4]: " CHOICE

case "$CHOICE" in
  1) do_install ;;
  2) do_update ;;
  3) do_backup ;;
  4) do_uninstall ;;
  *) echo "گزینه نامعتبر است." ; exit 1 ;;
esac

echo
echo "دستورات مفید مدیریت سرویس‌ها:"
echo "  وضعیت ربات      : systemctl status ${SERVICE_NAME}"
echo "  لاگ ربات         : journalctl -u ${SERVICE_NAME} -f"
echo "  وضعیت پنل تحت وب : systemctl status ${PANEL_SERVICE_NAME}"
echo "  لاگ پنل تحت وب    : journalctl -u ${PANEL_SERVICE_NAME} -f"
echo "  ری‌استارت هر دو    : systemctl restart ${SERVICE_NAME} ${PANEL_SERVICE_NAME}"
