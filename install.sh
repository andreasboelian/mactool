#!/bin/bash
# EBM Mactool Installation Script
# Installiert das Tool vollständig auf macOS mit Autostart

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR=~/Applications/mactool
LAUNCHAGENT_DIR=~/Library/LaunchAgents
LAUNCHAGENT_LABEL=com.ebm.mactool
STORAGE_DIR=~/Desktop/GramBotStorage

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}    EBM Mactool — Installation Script${NC}"
echo -e "${BLUE}================================================${NC}\n"

# 1. macOS Version Check
echo -e "${YELLOW}[1/13] Checking macOS version...${NC}"
MACOS_VERSION=$(sw_vers -productVersion | cut -d. -f1,2)
REQUIRED_VERSION="10.13"

if [[ ! $(printf '%s\n' "$REQUIRED_VERSION" "$MACOS_VERSION" | sort -V | head -n1) == "$REQUIRED_VERSION" ]]; then
    echo -e "${RED}✗ macOS $REQUIRED_VERSION+ required (current: $MACOS_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ macOS version OK ($MACOS_VERSION)${NC}\n"

# 2. Check/Install Homebrew
echo -e "${YELLOW}[2/13] Checking Homebrew...${NC}"
if ! command -v brew &> /dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
echo -e "${GREEN}✓ Homebrew ready${NC}\n"

# 3. Check/Install Python 3.12
echo -e "${YELLOW}[3/13] Checking Python 3.11+...${NC}"

# Check Python version more robustly
PYTHON_VERSION=""
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
fi

NEEDS_PYTHON=false
if [ -z "$PYTHON_VERSION" ]; then
    NEEDS_PYTHON=true
else
    # Compare versions: if < 3.11, need to update
    if python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
        :  # Version is OK
    else
        NEEDS_PYTHON=true
    fi
fi

if [ "$NEEDS_PYTHON" = true ]; then
    echo "  Installing Python 3.12 via Homebrew..."
    brew install python@3.12
    brew link python@3.12 --force
fi

# Use 'python3.12' from PATH (works on both Intel and Apple Silicon)
PYTHON3_CMD="python3.12"

# Verify it exists
if ! command -v $PYTHON3_CMD &> /dev/null; then
    echo -e "${RED}✗ Python 3.12 not found in PATH${NC}"
    echo "Try: brew install python@3.12"
    exit 1
fi

FINAL_VERSION=$($PYTHON3_CMD --version 2>&1 | cut -d' ' -f2)
echo -e "${GREEN}✓ Python $FINAL_VERSION ready${NC}\n"

# 4. Create Installation Directory
echo -e "${YELLOW}[4/13] Creating installation directory...${NC}"
mkdir -p "$INSTALL_DIR"
echo -e "${GREEN}✓ Directory: $INSTALL_DIR${NC}\n"

# 5. Copy Mactool Files
echo -e "${YELLOW}[5/13] Copying mactool files...${NC}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cp "$SCRIPT_DIR"/*.py "$INSTALL_DIR/"
cp "$SCRIPT_DIR"/requirements.txt "$INSTALL_DIR/"
cp "$SCRIPT_DIR"/config.json.example "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/logs"
echo -e "${GREEN}✓ Files copied${NC}\n"

# 6. Create Python Virtual Environment
echo -e "${YELLOW}[6/13] Creating Python virtual environment...${NC}"
$PYTHON3_CMD -m venv "$INSTALL_DIR/venv"
echo -e "${GREEN}✓ venv created${NC}\n"

# 7. Install Python Dependencies
echo -e "${YELLOW}[7/13] Installing Python dependencies...${NC}"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}✓ Dependencies installed${NC}\n"

# 8. Interactive Configuration
echo -e "${YELLOW}[8/13] Configuring mactool...${NC}\n"

read -p "  Enter server name (e.g., mac04): " SERVER_NAME
SERVER_NAME=${SERVER_NAME:-mac04}

read -sp "  Enter Supabase Service Role Key (will be hidden): " SUPABASE_KEY
echo ""

if [ -z "$SUPABASE_KEY" ]; then
    echo -e "${RED}✗ Supabase key is required!${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Configuration collected${NC}\n"

# 9. Generate config.json
echo -e "${YELLOW}[9/13] Generating config.json...${NC}"
cat > "$INSTALL_DIR/config.json" <<EOF
{
  "server_name": "$SERVER_NAME",
  "sync_times": [
    "09:00",
    "14:30"
  ],
  "blacklist": [],
  "supabase_url": "https://fxreaveeihaawkusmybi.supabase.co",
  "supabase_key": "$SUPABASE_KEY",
  "bot_app_path": "/Applications/botapp.app/Contents/MacOS/BotApp",
  "adb_path": "adb",
  "sqlite_db_path": "~/Desktop/GramBotStorage/super.db",
  "webhook_url": "https://n8n.srv882018.hstgr.cloud/webhook/4e031c12-36a0-492b-bcf3-c5b2b5a0b7fb-device-offline",
  "device_check_interval_hours": 1,
  "bot_check_interval_minutes": 5,
  "log_level": "INFO"
}
EOF
chmod 600 "$INSTALL_DIR/config.json"
echo -e "${GREEN}✓ config.json created (mode 600)${NC}\n"

# 10. Create GramBotStorage Directory
echo -e "${YELLOW}[10/13] Creating GramBotStorage directory...${NC}"
mkdir -p "$STORAGE_DIR"
echo -e "${GREEN}✓ Directory: $STORAGE_DIR${NC}\n"

# 11. Create LaunchAgent Plist
echo -e "${YELLOW}[11/13] Creating LaunchAgent plist...${NC}"
mkdir -p "$LAUNCHAGENT_DIR"

PYTHON_PATH="$INSTALL_DIR/venv/bin/python3"

cat > "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist" <<'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ebm.mactool</string>

    <key>ProgramArguments</key>
    <array>
        <string>PYTHON_PATH_PLACEHOLDER</string>
        <string>INSTALL_DIR_PLACEHOLDER/main.py</string>
        <string>--web-ui</string>
    </array>

    <key>WorkingDirectory</key>
    <string>INSTALL_DIR_PLACEHOLDER</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>INSTALL_DIR_PLACEHOLDER/logs/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>INSTALL_DIR_PLACEHOLDER/logs/stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLISTEOF

# Replace placeholders
sed -i '' "s|PYTHON_PATH_PLACEHOLDER|$PYTHON_PATH|g" "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
sed -i '' "s|INSTALL_DIR_PLACEHOLDER|$INSTALL_DIR|g" "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"

chmod 644 "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
echo -e "${GREEN}✓ Plist created: $LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist${NC}\n"

# 12. Load LaunchAgent
echo -e "${YELLOW}[12/13] Loading LaunchAgent...${NC}"

# Unload if already loaded
launchctl unload "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist" 2>/dev/null || true

# Load the agent
launchctl load "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"

# Wait a moment for the service to start
sleep 2

echo -e "${GREEN}✓ LaunchAgent loaded${NC}\n"

# 13. Final Status
echo -e "${YELLOW}[13/13] Verifying installation...${NC}\n"

if launchctl list | grep -q "$LAUNCHAGENT_LABEL"; then
    echo -e "${GREEN}✓ LaunchAgent is running${NC}"
else
    echo -e "${YELLOW}⚠ LaunchAgent status: pending (check in a moment)${NC}"
fi

echo ""
echo -e "${BLUE}================================================${NC}"
echo -e "${GREEN}✅ Installation Complete!${NC}"
echo -e "${BLUE}================================================${NC}\n"

echo "Configuration:"
echo "  Server Name:      $SERVER_NAME"
echo "  Install Dir:      $INSTALL_DIR"
echo "  Storage Dir:      $STORAGE_DIR"
echo "  LaunchAgent:      $LAUNCHAGENT_LABEL"
echo ""

echo "What's Next:"
echo "  1. Tool is running in background (started by LaunchAgent)"
echo "  2. Web UI: http://localhost:8000"
echo "  3. Logs: $INSTALL_DIR/logs/mactool.log"
echo ""

echo "Useful Commands:"
echo "  # Check status"
echo "  launchctl list | grep $LAUNCHAGENT_LABEL"
echo ""
echo "  # View logs"
echo "  tail -f $INSTALL_DIR/logs/mactool.log"
echo ""
echo "  # Stop tool"
echo "  launchctl unload $LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
echo ""
echo "  # Start tool again"
echo "  launchctl load $LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
echo ""
echo "  # Manual sync"
echo "  $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/main.py --sync"
echo ""

echo -e "${BLUE}================================================${NC}"
echo -e "${YELLOW}⏳ Opening Web UI in 3 seconds...${NC}"
echo -e "${BLUE}================================================${NC}\n"

sleep 3
open "http://localhost:8000" || true

echo -e "${GREEN}Done! Web UI should open automatically.${NC}\n"
