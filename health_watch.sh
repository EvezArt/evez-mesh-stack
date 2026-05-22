#!/usr/bin/env bash
# ⚡ EVEZ Health Watchdog — runs every 5 minutes via cron
# Checks all services, restarts failures, logs events
set -euo pipefail

EVZ="/opt/evez"
LOG="$EVZ/logs/health.log"
ALERT=0
ALERTS=""

timestamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() { echo "[$(timestamp)] $*" >> "$LOG"; }

# ─── Thermal Check ────────────────────────────────
TEMP=0
if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
    TEMP=$(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 ))
fi

if [ "$TEMP" -ge 85 ]; then
    log "CRITICAL: CPU temp ${TEMP}°C — stopping non-essential services"
    docker stop evez-homer evez-grafana evez-syncthing 2>/dev/null || true
    ALERT=1; ALERTS="${ALERTS}THERMAL CRITICAL ${TEMP}°C. "
elif [ "$TEMP" -ge 75 ]; then
    log "WARNING: CPU temp ${TEMP}°C — reducing load"
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo "powersave" > "$cpu" 2>/dev/null || true
    done
    ALERT=1; ALERTS="${ALERTS}THERMAL WARNING ${TEMP}°C. "
fi

# ─── Docker Services Check ────────────────────────
for svc in evez-reticulum evez-meshmind evez-n8n evez-ollama evez-prometheus evez-postgres; do
    STATUS=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "missing")
    if [ "$STATUS" != "running" ]; then
        log "RESTART: $svc was $STATUS — restarting"
        docker start "$svc" 2>/dev/null || docker restart "$svc" 2>/dev/null || log "FAIL: Could not restart $svc"
        ALERT=1; ALERTS="${ALERTS}${svc} was ${STATUS}, restarted. "
    fi
done

# ─── Network Check ────────────────────────────────
if ! ping -c1 -W3 1.1.1.1 &>/dev/null; then
    log "WARN: Internet unreachable"
    ALERT=1; ALERTS="${ALERTS}Internet unreachable. "
fi

# ─── Disk Space Check ────────────────────────────
DISK_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_PCT" -ge 90 ]; then
    log "CRITICAL: Disk ${DISK_PCT}% full — cleaning Docker"
    docker system prune -af --volumes 2>/dev/null || true
    ALERT=1; ALERTS="${ALERTS}Disk ${DISK_PCT}%, pruned. "
fi

# ─── Memory Check ────────────────────────────────
MEM_PCT=$(free | awk '/Mem/{printf("%.0f", $3/$2*100)}')
if [ "$MEM_PCT" -ge 90 ]; then
    log "WARNING: Memory ${MEM_PCT}% used — restarting top consumers"
    docker restart evez-ollama 2>/dev/null || true
    ALERT=1; ALERTS="${ALERTS}Memory ${MEM_PCT}%, restarted Ollama. "
fi

# ─── MeshMind API Check ──────────────────────────
if curl -sf http://localhost:8899/api/health > /dev/null 2>&1; then
    log "OK: MeshMind healthy"
else
    log "WARN: MeshMind API unreachable"
fi

# ─── Summary ──────────────────────────────────────
if [ "$ALERT" -eq 0 ]; then
    log "OK: All systems nominal (temp=${TEMP}°C, disk=${DISK_PCT}%, mem=${MEM_PCT}%)"
else
    log "ALERTS: $ALERTS"
    # Could send to n8n webhook, Slack, etc.
    curl -sf -X POST http://localhost:5678/webhook/evez-alert \
        -H "Content-Type: application/json" \
        -d "{\"alerts\": \"$ALERTS\", \"temp\": $TEMP, \"disk\": $DISK_PCT, \"mem\": $MEM_PCT}" \
        2>/dev/null || true
fi
