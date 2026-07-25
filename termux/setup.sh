#!/bin/bash
# ============================================================
# BBPro Ultimate — Termux Setup Script
# يثبت كل حاجة من الصفر على Termux
# ============================================================

echo "============================================"
echo "  BBPro Ultimate — Termux Setup"
echo "============================================"

# 1. Install Termux packages
echo "[1/6] Installing system packages..."
pkg update -y && pkg upgrade -y
pkg install -y python python-pip git curl wget termux-api openssh

# 2. Install Termux:Boot (for auto-start on phone boot)
echo ""
echo "[2/6] Setting up auto-start on boot..."
mkdir -p ~/.termux/boot

# 3. Grant wake lock (prevent Android from killing the process)
echo ""
echo "[3/6] Acquiring wake lock (prevents Android from killing bot)..."
termux-wake-lock 2>/dev/null || echo "  (Install Termux:API app from Play Store for wake lock)"

# 4. Clone repo
echo ""
echo "[4/6] Cloning repository..."
BOT_DIR="$HOME/bbpro-ultimate-bot"
if [ -d "$BOT_DIR" ]; then
    echo "  Repository exists. Pulling latest..."
    cd "$BOT_DIR"
    git pull origin main
else
    git clone https://github.com/mexc1433-crypto/bbpro-ultimate-bot.git "$BOT_DIR"
fi

# 5. Install Python dependencies
echo ""
echo "[5/6] Installing Python dependencies..."
cd "$BOT_DIR"
pip install --upgrade pip
pip install -r requirements.txt

# 6. Create .env file if not exists
echo ""
echo "[6/6] Setting up environment variables..."
if [ ! -f "$BOT_DIR/.env" ]; then
    cat > "$BOT_DIR/.env" << 'ENVEOF'
# === cTrader ===
CTRADER_ACCESS_TOKEN=ضع_التوكن_هنا
CTRADER_ACCOUNT_ID=47838646

# === Telegram ===
TELEGRAM_BOT_TOKEN=ضع_توكن_تيليجرام_هنا
TELEGRAM_CHAT_ID=7005859703

# === Groq AI ===
GROQ_API_KEY=ضع_مفتاح_جروك_هنا

# === Bot Settings ===
PORT=5100
PYTHONUNBUFFERED=1
TERMUX=true
ENVEOF
    echo "  Created .env file — you need to edit it with your tokens!"
    echo "  Run: nano $BOT_DIR/.env"
else
    echo "  .env already exists."
fi

# Create boot script for auto-start on phone restart
cat > ~/.termux/boot/start-bbpro.sh << 'BOOTEOF'
#!/bin/bash
termux-wake-lock
sleep 10
bash ~/bbpro-ultimate-bot/termux/start_bot.sh
BOOTEOF
chmod +x ~/.termux/boot/start-bbpro.sh

# Make start script executable
chmod +x "$BOT_DIR/termux/start_bot.sh"

echo ""
echo "============================================"
echo "  ✅ Setup Complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "1. Edit your tokens:  nano ~/bbpro-ultimate-bot/.env"
echo "2. Start the bot:     bash ~/bbpro-ultimate-bot/termux/start_bot.sh"
echo ""
echo "Auto-start on phone boot: ENABLED ✅"
echo "Wake lock (anti-kill):    ENABLED ✅"
echo "Auto-restart on crash:    ENABLED ✅ (50 retries max)"
echo ""
echo "⚠️ Install these apps from Play Store:"
echo "   - Termux:Boot (auto-start when phone restarts)"
echo "   - Termux:API (wake lock + notifications)"
echo ""
echo "⚠️ Disable battery optimization for Termux:"
echo "   Settings > Apps > Termux > Battery > Unrestricted"
echo ""
