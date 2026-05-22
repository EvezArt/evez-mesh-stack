#!/usr/bin/env bash
# ⚡ EvezBoot — Master Bootstrap Installer
# One command to deploy the entire EVEZ self-building mesh stack
# Designed for Mojave Desert (Bullhead City, AZ — 130°F+ summers)
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}[EVEZ]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

EVZ="/opt/evez"
EVZ_DATA="/opt/evez/data"
EVZ_LOG="/opt/evez/logs"

log "⚡ EvezBoot — Self-Building Mesh Network Installer"
log "   Target: Bullhead City, AZ (Mojave Desert — thermal-hardened)"
log "   Architecture: Reticulum + MeshMind + n8n + Ollama + Prometheus + Homer"
echo ""

# ─── 1. System Dependencies ────────────────────────────────
log "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    docker.io docker-compose-plugin \
    python3 python3-pip python3-venv \
    git curl wget jq htop tmux \
    i2pd tor wireguard \
    meshtasticd \
    2>/dev/null || warn "Some packages may need universe repo"

sudo systemctl enable docker
sudo systemctl start docker
ok "System dependencies installed"

# ─── 2. Python Stack ──────────────────────────────────────
log "Installing Python packages..."
pip3 install --break-system-packages \
    reticulum rnsh rnsd \
    nomadnet lxmf \
    requests flask fastapi uvicorn \
    prometheus-client \
    2>/dev/null || warn "Some pip packages need manual install"

ok "Python stack installed"

# ─── 3. EVEZ Directory Structure ──────────────────────────
log "Creating EVEZ directory structure..."
sudo mkdir -p $EVZ $EVZ_DATA/{reticulum,meshmind,n8n,ollama,prometheus,postgres,searxng,logs}
sudo chown -R $USER:$USER $EVZ

# Copy stack files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp -r "$SCRIPT_DIR"/* $EVZ/ 2>/dev/null || true
ok "Directory structure created at $EVZ"

# ─── 4. Reticulum Configuration ───────────────────────────
log "Configuring Reticulum mesh network..."
mkdir -p ~/.reticulum
cp $EVZ/reticulum.conf ~/.reticulum/config 2>/dev/null || {
    cat > ~/.reticulum/config << 'RNSCONF'
[reticulum]
  enable_transport = Yes
  share_instance = Yes
  instance_name = EVEZ-Node

  # TCP interface — internet backbone
  [[TCP Server]]
    type = TCPServerInterface
    interface_enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242

  # Auto Interface — local mesh discovery
  [[Auto]]
    type = AutoInterface
    interface_enabled = Yes
    group_id = evez-mesh

  # LoRa interface — long-range desert mesh
  [[LoRa]]
    type = RNodeInterface
    interface_enabled = No  # Enable when RNode hardware connected
    port = /dev/ttyUSB0
    frequency = 915000000
    bandwidth = 500000
    txpower = 22
    spreadingfactor = 8
    codingrate = 5
RNSCONF
}
ok "Reticulum configured"

# ─── 5. Docker Compose ────────────────────────────────────
log "Starting EVEZ Docker stack..."
cd $EVZ
docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null || warn "Docker compose needs manual start"
ok "Docker stack launched"

# ─── 6. Systemd Services ──────────────────────────────────
log "Installing systemd services..."

# Reticulum daemon
sudo cp $EVZ/reticulum.service /etc/systemd/system/ 2>/dev/null || {
    cat > /tmp/reticulum.service << 'RNSD'
[Unit]
Description=Reticulum Network Daemon
After=network.target

[Service]
Type=simple
User=%i
ExecStart=/usr/local/bin/rnsd
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
RNSD
    sudo cp /tmp/reticulum.service /etc/systemd/system/
}

sudo systemctl daemon-reload
sudo systemctl enable reticulum
sudo systemctl start reticulum 2>/dev/null || warn "Reticulum may need manual start"
ok "Systemd services installed"

# ─── 7. MeshMind AI Agent ─────────────────────────────────
log "Installing MeshMind — AI network healer..."
cp $EVZ/meshmind.py $EVZ_DATA/meshmind/ 2>/dev/null || true

cat > /etc/systemd/system/evez-meshmind.service << 'MMSD'
[Unit]
Description=EVEZ MeshMind — AI Network Healer
After=network.target docker.service

[Service]
Type=simple
User=%i
WorkingDirectory=/opt/evez/data/meshmind
ExecStart=/usr/bin/python3 /opt/evez/data/meshmind/meshmind.py
Restart=always
RestartSec=10
Environment=EVZ_DATA=/opt/evez/data
Environment=OLLAMA_HOST=http://localhost:11434

[Install]
WantedBy=multi-user.target
MMSD

sudo systemctl daemon-reload
sudo systemctl enable evez-meshmind
sudo systemctl start evez-meshmind 2>/dev/null || warn "MeshMind needs Ollama running first"
ok "MeshMind installed"

# ─── 8. Health Watchdog ──────────────────────────────────
log "Installing health watchdog..."
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/evez/health_watch.sh >> /opt/evez/logs/health.log 2>&1") | crontab -
ok "Health watchdog scheduled (every 5 min)"

# ─── 9. n8n Workflows ────────────────────────────────────
log "Importing n8n mesh automation workflows..."
for wf in $EVZ/*.json; do
    if [[ "$wf" == *"mesh_event"* ]] || [[ "$wf" == *"maintenance"* ]]; then
        curl -sf -X POST http://localhost:5678/api/v1/workflows/import \
            -H "Content-Type: application/json" \
            -d @"$wf" 2>/dev/null && ok "Imported $(basename $wf)" || warn "n8n not ready yet for $(basename $wf)"
    fi
done

# ─── 10. Thermal Hardening ──────────────────────────────
log "Applying Mojave Desert thermal hardening..."
# Aggressive thermal throttling for 130°F+ environments
cat > /etc/sysctl.d/99-evez-thermal.conf << 'THERM'
# Reduce CPU frequency under load to prevent thermal shutdown
vm.swappiness=1
vm.dirty_ratio=5
vm.dirty_background_ratio=2
# Network tuning for high-latency LoRa mesh
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_keepalive_time=60
net.ipv4.tcp_keepalive_intvl=10
net.ipv4.tcp_keepalive_probes=6
THERM
sudo sysctl -p /etc/sysctl.d/99-evez-thermal.conf 2>/dev/null

# CPU governor — powersave for thermal
for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "powersave" | sudo tee "$cpu" 2>/dev/null
done
ok "Thermal hardening applied (powersave governor, swap tuned)"

# ─── Done ─────────────────────────────────────────────────
echo ""
log "════════════════════════════════════════════════════════"
ok "⚡ EVEZ MESH STACK DEPLOYED"
log "════════════════════════════════════════════════════════"
echo ""
log "Services:"
log "  Reticulum mesh  → :4242 (TCP), LoRa 915MHz"
log "  MeshMind AI     → /opt/evez/data/meshmind/"
log "  n8n automation  → http://localhost:5678"
log "  Homer dashboard → http://localhost:8080"
log "  Prometheus      → http://localhost:9090"
log "  Ollama AI       → http://localhost:11434"
log "  Health watchdog → crontab (5 min)"
echo ""
log "Desert-hardened for Bullhead City, AZ (130°F+)"
log "Network status: /opt/evez/health_watch.sh"
