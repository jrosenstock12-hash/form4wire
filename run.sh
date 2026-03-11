#!/bin/bash
# run.sh — Load .env and start the bot
set -a
source .env
set +a
echo "Starting Form4Wire..."
python bot.py
