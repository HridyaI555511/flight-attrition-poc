#!/bin/bash
# Daily attrition dashboard refresh

export PATH="/Users/I555511/.nvm/versions/node/v20.20.1/bin:/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin:/usr/bin:/bin"

REPO="/Users/I555511/flight-attrition-poc"
LOG="$REPO/refresh.log"

echo "=== $(date) ===" >> "$LOG"

cd "$REPO" || exit 1

npm run fetch:all >> "$LOG" 2>&1
npm run run       >> "$LOG" 2>&1

echo "Done." >> "$LOG"
