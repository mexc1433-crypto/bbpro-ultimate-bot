#!/bin/bash
# ============================================================
# BBPro Ultimate — Termux Auto-Restart Wrapper
# يعيد تشغيل البوت تلقائياً لو وقف أو فصل
# ============================================================

BOT_DIR="$HOME/bbpro-ultimate-bot"
LOG_FILE="$HOME/bbpro-logs.txt"
MAX_RESTARTS=50
RESTART_COUNT=0
RESTART_DELAY=10

# Ensure we're in the right environment
export TERMUX=true
export PYTHONUNBUFFERED=1
export PORT=5100

# Load .env if exists
if [ -f "$BOT_DIR/.env" ]; then
    source "$BOT_DIR/.env"
fi

echo "========================================" | tee -a "$LOG_FILE"
echo "[$(date)] BBPro Termux Launcher Started" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# Check if repo exists, clone if not
if [ ! -d "$BOT_DIR/bot" ]; then
    echo "[$(date)] Cloning repository..." | tee -a "$LOG_FILE"
    git clone https://github.com/mexc1433-crypto/bbpro-ultimate-bot.git "$BOT_DIR" 2>&1 | tee -a "$LOG_FILE"
fi

# Install/update dependencies
echo "[$(date)] Installing dependencies..." | tee -a "$LOG_FILE"
cd "$BOT_DIR"
pip install --upgrade pip 2>&1 | tail -1 | tee -a "$LOG_FILE"
pip install -r requirements.txt 2>&1 | tail -1 | tee -a "$LOG_FILE"

# Main loop — auto restart on crash
while [ $RESTART_COUNT -lt $MAX_RESTARTS ]; do
    echo "" | tee -a "$LOG_FILE"
    echo "[$(date)] === Starting bot (attempt $((RESTART_COUNT+1))/$MAX_RESTARTS) ===" | tee -a "$LOG_FILE"
    
    # Run the bot
    cd "$BOT_DIR"
    python bot/main.py 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=$?
    
    echo "[$(date)] Bot exited with code $EXIT_CODE" | tee -a "$LOG_FILE"
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date)] Clean exit. Not restarting." | tee -a "$LOG_FILE"
        break
    fi
    
    # Send Telegram alert about crash
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        MSG="⚠️ BBPro Bot crashed (exit code: $EXIT_CODE). Restarting in ${RESTART_DELAY}s... (attempt $((RESTART_COUNT+1))/$MAX_RESTARTS)"
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d text="$MSG" > /dev/null 2>&1
    fi
    
    RESTART_COUNT=$((RESTART_COUNT+1))
    echo "[$(date)] Waiting ${RESTART_DELAY}s before restart..." | tee -a "$LOG_FILE"
    sleep $RESTART_DELAY
done

# If we hit max restarts, alert
if [ $RESTART_COUNT -ge $MAX_RESTARTS ]; then
    echo "[$(date)] MAX RESTARTS REACHED. Bot stopped." | tee -a "$LOG_FILE"
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        MSG="🚨 BBPro Bot: MAX RESTARTS ($MAX_RESTARTS) reached. Manual intervention needed!"
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d text="$MSG" > /dev/null 2>&1
    fi
fi
