#!/usr/bin/env bash
# Start the supervisor (app + tunnel) detached from this terminal.
cd "$(dirname "$0")"; mkdir -p data
setsid nohup ./supervise.sh >> data/supervise.log 2>&1 < /dev/null &
sleep 1; echo "supervisor launched; log: data/supervise.log; public URL: data/public_url.txt"
