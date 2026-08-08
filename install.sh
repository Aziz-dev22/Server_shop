#!/usr/bin/env bash
# نصب خودکار Hetzner Shop Bot روی سرور اوبونتو
# اجرا: bash install.sh
set -e

APP_DIR="/opt/hetzner-shop-bot"
SERVICE_NAME="hetzner-shop-bot"

echo "================================================"
echo "  نصب‌کننده ربات فروش سرور مجازی Hetzner"
echo "================================================"
echo

if [ "$EUID" -ne 0 ]; then
  echo "لطفاً این اسکریپت را با دسترسی روت اجرا کنید (sudo bash install.sh)"
  exit 1
fi

echo "[1/6] بروزرسانی و نصب پیش‌نیازها..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl

echo
echo "[2/6] کپی کردن فایل‌های پروژه به $APP_DIR ..."
mkdir -p "$APP_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -r "$SCRIPT_DIR"/* "$APP_DIR"/

cd "$APP_DIR"

echo
echo "[3/6] لطفاً اطلاعات زیر را وارد کنید:"
read -rp "توکن ربات تلگرام (از @BotFather): " BOT_TOKEN
read -rp "شناسه عددی تلگرام ادمین (از @userinfobot): " ADMIN_IDS
read -rp "شماره کارت برای شارژ کیف پول کاربران: " CARD_NUMBER

cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
DB_PATH=shop.db
CARD_NUMBER=${CARD_NUMBER}
EOF

echo
echo "[4/6] ساخت محیط مجازی پایتون و نصب کتابخانه‌ها..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo
echo "[5/6] ساخت سرویس systemd برای اجرای همیشگی ربات..."
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

echo
echo "[6/6] فعال‌سازی و اجرای سرویس..."
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

echo
echo "================================================"
echo "✅ نصب با موفقیت انجام شد!"
echo "ربات شما در حال اجراست."
echo
echo "مراحل بعدی داخل خود تلگرام:"
echo "  1) به ربات پیام /start بدهید."
echo "  2) با /admin وارد پنل مدیریت شوید."
echo "  3) در بخش «اتصال Hetzner» توکن API هتزنر را وارد کنید."
echo "  4) در بخش «مدیریت پلن‌ها» حداقل یک پلن با قیمت تعریف کنید."
echo
echo "دستورات مفید مدیریت سرویس:"
echo "  مشاهده وضعیت : systemctl status ${SERVICE_NAME}"
echo "  مشاهده لاگ    : journalctl -u ${SERVICE_NAME} -f"
echo "  ری‌استارت     : systemctl restart ${SERVICE_NAME}"
echo "================================================"
