#!/usr/bin/env bash
# نصب‌کننده Hetzner Shop Bot با منوی چهار گزینه‌ای
#
# اجرا با یک دستور:
#   sudo bash <(curl -fsSL https://raw.githubusercontent.com/Aziz-dev22/server_shop/main/install.sh)
#
set -e

REPO_URL="https://github.com/Aziz-dev22/server_shop.git"
APP_DIR="/opt/hetzner-shop-bot"
SERVICE_NAME="hetzner-shop-bot"
BACKUP_DIR="/root/hetzner-shop-bot-backups"

if [ "$EUID" -ne 0 ]; then
  echo "لطفاً این اسکریپت را با دسترسی روت اجرا کنید:"
  echo "  sudo bash <(curl -fsSL https://raw.githubusercontent.com/Aziz-dev22/server_shop/main/install.sh)"
  exit 1
fi

# ---------------------------------------------------------------------------
# عملیات: نصب
# ---------------------------------------------------------------------------
do_install() {
  echo "================================================"
  echo "  نصب Hetzner Shop Bot"
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

  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
DB_PATH=shop.db
EOF

  echo
  echo "[4/5] ساخت محیط مجازی پایتون و نصب کتابخانه‌ها..."
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt

  echo
  echo "[5/5] ساخت و اجرای سرویس systemd..."
  write_service_file
  systemctl daemon-reload
  systemctl enable ${SERVICE_NAME}
  systemctl restart ${SERVICE_NAME}

  echo
  echo "================================================"
  echo "✅ نصب با موفقیت انجام شد! ربات در حال اجراست."
  echo
  echo "مراحل بعدی داخل خود تلگرام:"
  echo "  1) به ربات پیام /start بدهید."
  echo "  2) با /admin وارد پنل مدیریت شوید."
  echo "  3) در «مدیریت API های Hetzner» یک API Key اضافه کنید."
  echo "  4) در «مدیریت پلن‌ها» حداقل یک پلن با درصد سود دلخواه تعریف کنید."
  echo "  5) اگر می‌خواهید پرداخت خودکار ارز دیجیتال داشته باشید، در «درگاه ارز دیجیتال (OxaPay)» API Key آن را وارد کنید."
  echo "  6) در «آدرس واریز دستی» شماره کارت یا آدرس ولت خود را برای پرداخت‌های دستی وارد کنید."
  echo "================================================"
}

# ---------------------------------------------------------------------------
# عملیات: آپدیت
# ---------------------------------------------------------------------------
do_update() {
  echo "================================================"
  echo "  بروزرسانی Hetzner Shop Bot"
  echo "================================================"
  if [ ! -d "$APP_DIR/.git" ]; then
    echo "ربات نصب نشده است. ابتدا گزینه «نصب» را انتخاب کنید."
    exit 1
  fi
  echo "[1/3] توقف موقت سرویس..."
  systemctl stop ${SERVICE_NAME} 2>/dev/null || true

  echo "[2/3] دریافت آخرین نسخه کد..."
  git -C "$APP_DIR" pull --ff-only

  echo "[3/3] بروزرسانی کتابخانه‌ها و ری‌استارت سرویس..."
  cd "$APP_DIR"
  ./venv/bin/pip install --upgrade pip -q
  ./venv/bin/pip install -r requirements.txt -q
  write_service_file
  systemctl daemon-reload
  systemctl restart ${SERVICE_NAME}

  echo
  echo "✅ آپدیت انجام شد. فایل .env و دیتابیس شما دست‌نخورده باقی ماندند."
}

# ---------------------------------------------------------------------------
# عملیات: بکاپ کامل
# ---------------------------------------------------------------------------
do_backup() {
  echo "================================================"
  echo "  بکاپ کامل Hetzner Shop Bot"
  echo "================================================"
  if [ ! -d "$APP_DIR" ]; then
    echo "ربات نصب نشده است، چیزی برای بکاپ‌گیری وجود ندارد."
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
  echo "  حذف کامل Hetzner Shop Bot"
  echo "================================================"
  echo "⚠️  این عملیات سرویس، فایل‌های ربات و دیتابیس (shop.db) را برای همیشه حذف می‌کند."
  read -rp "آیا مطمئن هستید؟ برای تایید عبارت yes را تایپ کنید: " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "عملیات لغو شد."
    exit 0
  fi

  read -rp "آیا قبل از حذف، یک بکاپ کامل گرفته شود؟ (y/n): " DO_BACKUP
  if [ "$DO_BACKUP" = "y" ] || [ "$DO_BACKUP" = "Y" ]; then
    do_backup
  fi

  echo "در حال توقف و حذف سرویس..."
  systemctl stop ${SERVICE_NAME} 2>/dev/null || true
  systemctl disable ${SERVICE_NAME} 2>/dev/null || true
  rm -f /etc/systemd/system/${SERVICE_NAME}.service
  systemctl daemon-reload

  echo "در حال حذف فایل‌های پروژه..."
  rm -rf "$APP_DIR"

  echo
  echo "✅ حذف کامل انجام شد."
}

# ---------------------------------------------------------------------------
# ساخت فایل سرویس systemd
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

# ---------------------------------------------------------------------------
# منوی اصلی
# ---------------------------------------------------------------------------
echo "================================================"
echo "  مدیریت Hetzner Shop Bot"
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
echo "دستورات مفید مدیریت سرویس:"
echo "  مشاهده وضعیت : systemctl status ${SERVICE_NAME}"
echo "  مشاهده لاگ    : journalctl -u ${SERVICE_NAME} -f"
echo "  ری‌استارت     : systemctl restart ${SERVICE_NAME}"
