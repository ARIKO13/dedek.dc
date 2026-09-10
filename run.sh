#!/bin/bash
# ====================================================
# ATMDS Bot Runner with Auto-Restart
# Run once, stays active forever
# ====================================================

BOT_DIR="/home/z/my-project/atmds_bot"
VENV_PYTHON="$BOT_DIR/venv/bin/python"
PYTHON="${PYTHON:-$VENV_PYTHON}"
LOG_FILE="$BOT_DIR/bot.log"
PID_FILE="$BOT_DIR/bot.pid"
MAX_RESTARTS=100
RESTART_DELAY=5

# Check venv exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Virtualenv tidak ditemukan di $VENV_PYTHON"
    echo "   Jalankan: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

cd "$BOT_DIR" || { echo "❌ Dir $BOT_DIR tidak ada"; exit 1; }

# Check .env exists
if [ ! -f "$BOT_DIR/.env" ]; then
    echo "❌ File .env belum dibuat!"
    echo "   Jalankan: cp .env.example .env"
    echo "   Lalu edit .env dan isi token kamu"
    exit 1
fi

# Functions
start_bot() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚀 Starting ATMDS Bot..." | tee -a "$LOG_FILE"
    $PYTHON bot.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Bot started (PID: $(cat $PID_FILE))" | tee -a "$LOG_FILE"
}

stop_bot() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🛑 Stopping bot (PID: $PID)..." | tee -a "$LOG_FILE"
            kill "$PID" 2>/dev/null
            sleep 2
            # Force kill if still running
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null
            fi
        fi
        rm -f "$PID_FILE"
    fi
}

status_bot() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        PID=$(cat "$PID_FILE")
        echo "✅ Bot running (PID: $PID)"
        echo "📄 Log: $LOG_FILE"
        echo "---"
        echo "Last 10 log lines:"
        tail -10 "$LOG_FILE" 2>/dev/null
    else
        echo "❌ Bot not running"
    fi
}

tail_log() {
    echo "📺 Tailing log (Ctrl+C to stop)..."
    tail -f "$LOG_FILE"
}

# Handle Ctrl+C
trap 'echo ""; echo "👋 Shutting down..."; stop_bot; exit 0' SIGINT SIGTERM

case "${1:-start}" in
    start)
        # AGGRESSIVE: kill ALL existing bot processes first (prevent zombies)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🧹 Cleaning up any existing bot processes..." | tee -a "$LOG_FILE"
        # Kill all bot.py processes (but not this script itself)
        for pid in $(pgrep -f "$BOT_DIR/venv/bin/python.*bot.py" 2>/dev/null); do
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 1
        rm -f "$PID_FILE"

        # Check if already running (after cleanup)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            PID=$(cat "$PID_FILE")
            echo "⚠️ Bot sudah jalan (PID: $PID)" | tee -a "$LOG_FILE"
            exit 1
        fi

        # Auto-restart loop
        RESTART_COUNT=0
        while [ $RESTART_COUNT -lt $MAX_RESTARTS ]; do
            start_bot
            PID=$(cat "$PID_FILE")
            wait $PID
            EXIT_CODE=$?
            RESTART_COUNT=$((RESTART_COUNT + 1))
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ Bot exited (code: $EXIT_CODE). Restart $RESTART_COUNT/$MAX_RESTARTS in $RESTART_DELAY sec..." | tee -a "$LOG_FILE"
            sleep $RESTART_DELAY
        done

        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ Max restarts reached. Stopping." | tee -a "$LOG_FILE"
        ;;

    start-bg)
        # AGGRESSIVE: kill ALL existing bot processes first (prevent zombies)
        echo "🧹 Cleaning up any existing bot processes..."
        for pid in $(pgrep -f "$BOT_DIR/venv/bin/python.*bot.py" 2>/dev/null); do
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 1
        rm -f "$PID_FILE"

        # Start in background (nohup)
        nohup bash "$0" start > /dev/null 2>&1 &
        echo "✅ Bot started in background"
        echo "   Cek status: $0 status"
        echo "   Stop: $0 stop"
        ;;

    stop)
        stop_bot
        echo "✅ Bot stopped"
        ;;

    restart)
        stop_bot
        sleep 2
        echo "🔄 Restarting..."
        start_bot
        ;;

    status)
        status_bot
        ;;

    log|logs)
        tail_log
        ;;

    *)
        echo "ATMDS Bot Runner"
        echo ""
        echo "Usage: $0 {start|start-bg|stop|restart|status|log}"
        echo ""
        echo "Commands:"
        echo "  start     Run in foreground with auto-restart"
        echo "  start-bg  Run in background (nohup)"
        echo "  stop      Stop the bot"
        echo "  restart   Restart the bot"
        echo "  status    Check bot status"
        echo "  log       Tail the log file"
        exit 1
        ;;
esac
