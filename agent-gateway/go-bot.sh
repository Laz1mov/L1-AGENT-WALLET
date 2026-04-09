#!/usr/bin/env bash
# Sovereign Bitcoin Agent Bot — UI Launcher
# 🏛️ Headquarters: agent-bitcoin-wallet/openclaude

if [ ! -f ".env" ]; then
    echo "⚠️  Configuration file not found: .env"
    exit 1
fi

source venv/bin/activate
export PYTHONPATH="$PYTHONPATH:$(pwd)"

echo "🚀 Starting Sovereign Telegram Bot (The Mouthpiece)..."
python3 telegram_bot.py
