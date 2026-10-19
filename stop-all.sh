#!/usr/bin/env bash
cd "$(dirname "$0")"
pkill -f "$(pwd)/supervise.sh" ; pkill -f "supervise.sh" 2>/dev/null
for f in data/app.pid data/tunnel.pid; do [ -f $f ] && kill "$(cat $f)" 2>/dev/null; done
echo stopped
