#!/usr/bin/env bash
# Keeps the shop (gunicorn) and the Cloudflare quick tunnel running; restarts either if it dies.
# Start with ./start-all.sh (runs this in the background). Stop with ./stop-all.sh.
# If the tunnel restarts, Cloudflare hands out a NEW random URL: this script then updates BASE_URL in .env,
# restarts the app, regenerates flyers, and logs a loud warning — previously printed QR codes stop working.
cd "$(dirname "$0")"
mkdir -p data flyers
exec 9>data/supervise.lock
flock -n 9 || { echo "supervisor already running"; exit 0; }
PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2); PORT=${PORT:-5055}
TUNNEL=${TUNNEL:-0}   # set TUNNEL=1 to also run a Cloudflare quick tunnel (needs outbound port 7844; blocked on the Grok box)
log(){ echo "$(date '+%F %T %Z') $*" >> data/supervise.log; }

start_app(){
  [ -f data/app.pid ] && kill "$(cat data/app.pid)" 2>/dev/null; sleep 1
  setsid .venv/bin/gunicorn -w 1 --threads 8 -b 0.0.0.0:"$PORT" --access-logfile data/access.log \
     app:app >> data/server.log 2>&1 < /dev/null &
  echo $! > data/app.pid; log "app started pid $!"
}
app_ok(){ curl -fsS -m 5 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; }

start_tunnel(){
  : > data/tunnel.log
  setsid bin/cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" >> data/tunnel.log 2>&1 < /dev/null &
  echo $! > data/tunnel.pid; log "tunnel started pid $!"
  for _ in $(seq 1 30); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' data/tunnel.log | head -1)
    [ -n "$URL" ] && break; sleep 1
  done
  [ -z "$URL" ] && { log "tunnel gave no URL yet"; return; }
  echo "$URL" > data/public_url.txt
  OLD=$(grep -E '^BASE_URL=' .env | cut -d= -f2-)
  if [ "$URL" != "$OLD" ]; then
    sed -i "s|^BASE_URL=.*|BASE_URL=$URL|" .env
    log "PUBLIC URL CHANGED: $OLD -> $URL  (re-print flyers! old QR codes are dead)"
    .venv/bin/python make_flyer.py --src flyer1 --base-url "$URL" >> data/supervise.log 2>&1
    start_app
  fi
}
tunnel_alive(){ [ -f data/tunnel.pid ] && kill -0 "$(cat data/tunnel.pid)" 2>/dev/null; }

log "supervisor starting (port $PORT, tunnel=$TUNNEL)"
app_ok || start_app
while true; do
  if [ "$TUNNEL" = 1 ] && ! tunnel_alive; then start_tunnel; fi
  if ! app_ok; then sleep 3; app_ok || { log "app not healthy - restarting"; start_app; }; fi
  sleep 10
done
