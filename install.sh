#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0;3m'

echo -e "${GREEN}===================================="
echo "       SERVER SHOP INSTALLER        "
echo -e "====================================${NC}"
echo "1) Install System"
echo "2) Update (Git Pull)"
echo "3) Uninstall System"
echo "4) Exit"
read -p "Select [1-4]: " option

if [ "$option" -eq 1 ]; then
    echo -e "${GREEN}[*] Installing dependencies...${NC}"
    sudo apt-get update && sudo apt-get install -y python3-pip python3-venv sqlite3 git

    echo -e "${GREEN}[*] Configuration Setup...${NC}"
    read -p "Telegram Bot Token: " bot_token
    read -p "Admin Telegram ID (Numeric): " admin_id
    read -p "Web Panel Username: " panel_user
    read -p "Web Panel Password: " panel_pass
    read -p "Web Panel Port [Default 5000]: " panel_port
    panel_port=${panel_port:-5000}

    cat << EOF > .env
TELEGRAM_BOT_TOKEN="$bot_token"
ADMIN_TELEGRAM_ID="$admin_id"
PANEL_ADMIN_USER="$panel_user"
PANEL_ADMIN_PASS="$panel_pass"
PANEL_PORT=$panel_port
SECRET_KEY="$(head -c 16 /dev/urandom | hex)"
DATABASE_URL="sqlite:///server_shop.db"
EOF

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

    python3 -c "from database import init_db; init_db()"
    echo -e "${GREEN}[✅] Installation Complete! Run 'source venv/bin/activate && python3 main.py' to start the app.${NC}"
    echo -e "${GREEN}[*] Add your Hetzner APIs via Web Panel.${NC}"

elif [ "$option" -eq 2 ]; then
    git pull
elif [ "$option" -eq 3 ]; then
    rm -rf venv .env server_shop.db server_shop.log
    echo -e "${RED}[🗑️] Uninstalled.${NC}"
fi

