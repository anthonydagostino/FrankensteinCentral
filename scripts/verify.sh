#!/usr/bin/env bash
# Verify the live deployment ON THE BOX: which services are up and whether
# each is returning REAL data vs an empty/not-connected state. Run this on the
# OptiPlex:  bash scripts/verify.sh
set -uo pipefail

host="${1:-localhost}"
echo "== FrankensteinCentral live check @ $host =="

j() { curl -s --max-time 8 "$@"; }
line() { printf '%-14s %s\n' "$1" "$2"; }

# containers
echo; echo "-- containers --"
(docker compose ps 2>/dev/null || docker-compose ps 2>/dev/null) | tail -n +1

echo; echo "-- integrations (real data?) --"

# gateway
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$host:8080/")
line "hub :8080" "$([ "$code" = 200 ] && echo 'UP' || echo "DOWN ($code)")"

# core
t=$(j "http://$host:8098/today")
line "core" "$(echo "$t" | grep -q '"score"' && echo "OK — score $(echo "$t" | grep -o '"score":[0-9]*' | head -1 | cut -d: -f2)" || echo 'no data')"

# firefly
fh=$(j "http://$host:8097/health")
if echo "$fh" | grep -q '"connected":true'; then
  sp=$(j "http://$host:8097/spending")
  line "firefly" "CONNECTED — today \$$(echo "$sp" | grep -o '"today":[0-9.]*' | cut -d: -f2), month \$$(echo "$sp" | grep -o '"month":[0-9.]*' | cut -d: -f2)"
else
  line "firefly" "NOT connected (set FIREFLY_URL + FIREFLY_TOKEN in .env)"
fi

# gmail
gm=$(j "http://$host:8083/needs-reply")
mode=$(echo "$gm" | grep -o '"mode":"[a-z]*"' | head -1 | cut -d'"' -f4)
n=$(echo "$gm" | grep -o '"id"' | wc -l)
line "gmail" "mode=$mode, ${n} triaged email(s)$([ "$mode" != live ] && echo '  <-- connect for real inbox')"

# stocks
pf=$(j "http://$host:8099/portfolio")
line "stocks" "$(echo "$pf" | grep -q '"configured":true' && echo "CONFIGURED — value \$$(echo "$pf" | grep -o '"value":[0-9.]*' | head -1 | cut -d: -f2)" || echo 'no holdings (add in Settings)')"

# tasks / schedule / fitness
line "tasks" "$(j "http://$host:8087/summary" | grep -o '"open":[0-9]*' | head -1 | cut -d: -f2 | sed 's/^/open: /' || echo '—')"
line "schedule" "$(j "http://$host:8084/events" | grep -o '"title"' | wc -l | sed 's/^/events: /')"
line "fitness" "$(j "http://$host:8082/visits" | grep -o '"count":[0-9]*' | cut -d: -f2 | sed 's/^/visits: /' || echo '—')"

echo; echo "-- the assembled home payload --"
home=$(j "http://$host:8085/home?fresh=1")
if echo "$home" | grep -q '"do_next"'; then
  echo "do_next : $(echo "$home" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["do_next"]["title"],"—",d["do_next"]["reason"])' 2>/dev/null)"
  echo "briefing: $(echo "$home" | python3 -c 'import sys,json;print(" · ".join(json.load(sys.stdin)["briefing"]))' 2>/dev/null)"
  echo "systems : $(echo "$home" | python3 -c 'import sys,json;s=json.load(sys.stdin)["systems"];print("healthy" if s["healthy"] else "DOWN: "+",".join(s["down"]))' 2>/dev/null)"
else
  echo "assistant /home did not respond — check: docker compose logs assistant"
fi
echo; echo "Done. Anything marked NOT connected / no holdings just needs config in .env or ⚙ Settings."
