#!/bin/bash
# ============================================================
# BBPro Ultimate — Health Monitor for Termux
# يراقب البوت ويرسل تنبيهات لو في مشكلة
# ============================================================

BOT_URL="http://localhost:5100/health"
CHECK_INTERVAL=60  # every 60 seconds
LOG_FILE="$HOME/bbpro-health.txt"
FAIL_COUNT=0
MAX_FAILS=3

# Load .env
if [ -f "$HOME/bbpro-ultimate-bot/.env" ]; then
    source "$HOME/bbpro-ultimate-bot/.env"
fi

send_telegram() {
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" \
            -d text="$1" > /dev/null 2>&1
    fi
}

echo "[$(date)] Health monitor started" | tee -a "$LOG_FILE"

while true; do
    # Check if bot process is running
    if ! pgrep -f "python bot/main.py" > /dev/null 2>&1; then
        FAIL_COUNT=$((FAIL_COUNT+1))
        echo "[$(date)] Bot process NOT running (fail $FAIL_COUNT/$MAX_FAILS)" | tee -a "$LOG_FILE"
        
        if [ $FAIL_COUNT -ge $MAX_FAILS ]; then
            send_telegram "🚨 BBPro: Bot process is DOWN! Health monitor detected the bot is not running."
            FAIL_COUNT=0
        fi
    else
        # Check HTTP health endpoint
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BOT_URL" 2>/dev/null)
        
        if [ "$HTTP_CODE" != "200" ]; then
            FAIL_COUNT=$((FAIL_COUNT+1))
            echo "[$(date)] Health check failed (HTTP $HTTP_CODE)" | tee -a "$LOG_FILE"
            
            if [ $FAIL_COUNT -ge $MAX_FAILS ]; then
                send_telegram "⚠️ BBPro: Health check failed (HTTP $HTTP_CODE). Bot may be stuck."
                FAIL_COUNT=0
            fi
        else
            # Reset on success
            if [ $FAIL_COUNT -gt 0 ]; then
                echo "[$(date)] Bot recovered ✅" | tee -a "$LOG_FILE"
            fi
            FAIL_COUNT=0
        fi
    fi
    
    sleep $CHECK_INTERVAL
done
