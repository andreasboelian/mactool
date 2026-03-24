#!/bin/bash
# EBM Mactool Installation Script
# Installiert das Tool vollständig auf macOS mit Autostart
# Safe to re-run: updates existing config without overwriting secrets

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
GITHUB_REPO="andreasboelian/mactool"

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}    EBM Mactool — Installation Script${NC}"
echo -e "${BLUE}================================================${NC}\n"

# Detect if this is an update or fresh install
IS_UPDATE=false
if [ -f "$INSTALL_DIR/config.json" ]; then
    IS_UPDATE=true
    echo -e "${YELLOW}Existing installation detected. Running in UPDATE mode.${NC}"
    echo -e "${YELLOW}Config.json will be preserved and updated (not overwritten).${NC}\n"
fi

# 1. macOS Version Check
echo -e "${YELLOW}[1/14] Checking macOS version...${NC}"
MACOS_VERSION=$(sw_vers -productVersion | cut -d. -f1,2)
REQUIRED_VERSION="10.13"

if [[ ! $(printf '%s\n' "$REQUIRED_VERSION" "$MACOS_VERSION" | sort -V | head -n1) == "$REQUIRED_VERSION" ]]; then
    echo -e "${RED}✗ macOS $REQUIRED_VERSION+ required (current: $MACOS_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ macOS version OK ($MACOS_VERSION)${NC}\n"

# 2. Check/Install Homebrew
echo -e "${YELLOW}[2/14] Checking Homebrew...${NC}"
if ! command -v brew &> /dev/null; then
    echo "  Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
echo -e "${GREEN}✓ Homebrew ready${NC}\n"

# 3. Check/Install Python 3.12
echo -e "${YELLOW}[3/14] Checking Python 3.11+...${NC}"

# Find a suitable Python 3.11+ — check multiple locations
find_python() {
    # Check common names and Homebrew paths
    for cmd in \
        python3.12 \
        python3.13 \
        python3.11 \
        python3 \
        /opt/homebrew/bin/python3.12 \
        /opt/homebrew/bin/python3.13 \
        /opt/homebrew/bin/python3.11 \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3.12 \
        /usr/local/bin/python3 \
    ; do
        if command -v "$cmd" &> /dev/null || [ -x "$cmd" ]; then
            if "$cmd" -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON3_CMD=$(find_python) || PYTHON3_CMD=""

if [ -z "$PYTHON3_CMD" ]; then
    echo "  No Python 3.11+ found. Installing Python 3.12 via Homebrew..."
    brew install python@3.12
    brew link python@3.12 --force 2>/dev/null || true
    # Re-search after install
    PYTHON3_CMD=$(find_python) || PYTHON3_CMD=""
fi

if [ -z "$PYTHON3_CMD" ]; then
    echo -e "${RED}✗ Python 3.11+ not found after install${NC}"
    echo "  Try manually: brew install python@3.12 && brew link python@3.12 --force"
    echo "  Then re-run this script."
    exit 1
fi

FINAL_VERSION=$($PYTHON3_CMD --version 2>&1 | cut -d' ' -f2)
echo -e "${GREEN}✓ Python $FINAL_VERSION ready${NC}\n"

# 4. Create Installation Directory
echo -e "${YELLOW}[4/14] Creating installation directory...${NC}"
mkdir -p "$INSTALL_DIR"
echo -e "${GREEN}✓ Directory: $INSTALL_DIR${NC}\n"

# 5. Copy Mactool Files
echo -e "${YELLOW}[5/14] Copying mactool files...${NC}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Skip copy if running from install dir (source == destination)
if [ "$(cd "$SCRIPT_DIR" && pwd -P)" != "$(cd "$INSTALL_DIR" && pwd -P)" ]; then
    cp "$SCRIPT_DIR"/*.py "$INSTALL_DIR/"
    cp "$SCRIPT_DIR"/requirements.txt "$INSTALL_DIR/"
    cp "$SCRIPT_DIR"/config.json.example "$INSTALL_DIR/"
    cp "$SCRIPT_DIR"/.gitignore "$INSTALL_DIR/" 2>/dev/null || true
    echo -e "${GREEN}✓ Files copied${NC}\n"
else
    echo -e "${GREEN}✓ Running from install dir, skip copy${NC}\n"
fi
mkdir -p "$INSTALL_DIR/logs"

# 6. Create Python Virtual Environment
echo -e "${YELLOW}[6/14] Creating Python virtual environment...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    $PYTHON3_CMD -m venv "$INSTALL_DIR/venv"
    echo -e "${GREEN}✓ venv created${NC}\n"
else
    echo -e "${GREEN}✓ venv already exists${NC}\n"
fi

# 7. Install Python Dependencies
echo -e "${YELLOW}[7/14] Installing Python dependencies...${NC}"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}✓ Dependencies installed${NC}\n"

# 8. Configuration
echo -e "${YELLOW}[8/14] Configuring mactool...${NC}\n"

if [ "$IS_UPDATE" = true ]; then
    # UPDATE MODE: Add missing fields to existing config.json
    echo "  Updating existing config.json with new fields..."

    "$INSTALL_DIR/venv/bin/python3" -c "
import json

config_path = '$INSTALL_DIR/config.json'
with open(config_path) as f:
    config = json.load(f)

changed = False

# Add github_repo if missing
if 'github_repo' not in config:
    config['github_repo'] = '$GITHUB_REPO'
    changed = True
    print('  + Added github_repo: $GITHUB_REPO')

# Add webhook_url if missing
if 'webhook_url' not in config:
    config['webhook_url'] = ''
    changed = True
    print('  + Added webhook_url (empty — set in Web UI or config.json)')

# Add any other new fields with defaults
defaults = {
    'device_check_interval_hours': 1,
    'bot_check_interval_minutes': 5,
    'log_level': 'INFO',
}
for key, default in defaults.items():
    if key not in config:
        config[key] = default
        changed = True
        print(f'  + Added {key}: {default}')

if changed:
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print('  Config updated successfully')
else:
    print('  Config already up to date')
"
    echo -e "${GREEN}✓ Config updated (secrets preserved)${NC}\n"

else
    # FRESH INSTALL: Ask for all settings
    read -p "  Enter server name (e.g., mac04): " SERVER_NAME
    SERVER_NAME=${SERVER_NAME:-mac04}

    read -sp "  Enter Supabase Service Role Key (will be hidden): " SUPABASE_KEY
    echo ""

    if [ -z "$SUPABASE_KEY" ]; then
        echo -e "${RED}✗ Supabase key is required!${NC}"
        exit 1
    fi

    read -p "  Enter webhook URL (leave empty to skip): " WEBHOOK_URL

    echo -e "${GREEN}✓ Configuration collected${NC}\n"

    # 9. Generate config.json
    echo -e "${YELLOW}[9/14] Generating config.json...${NC}"
    cat > "$INSTALL_DIR/config.json" <<EOF
{
  "server_name": "$SERVER_NAME",
  "sync_times": ["09:00", "14:30"],
  "blacklist": [],
  "supabase_url": "https://fxreaveeihaawkusmybi.supabase.co",
  "supabase_key": "$SUPABASE_KEY",
  "bot_app_path": "/Applications/botapp.app/Contents/MacOS/BotApp",
  "adb_path": "adb",
  "sqlite_db_path": "~/Desktop/GramBotStorage/super.db",
  "webhook_url": "$WEBHOOK_URL",
  "device_check_interval_hours": 1,
  "bot_check_interval_minutes": 5,
  "log_level": "INFO",
  "github_repo": "$GITHUB_REPO"
}
EOF
    chmod 600 "$INSTALL_DIR/config.json"
    echo -e "${GREEN}✓ config.json created (mode 600)${NC}\n"
fi

# 10. Create GramBotStorage Directory
echo -e "${YELLOW}[10/14] Creating GramBotStorage directory...${NC}"
mkdir -p "$STORAGE_DIR"
echo -e "${GREEN}✓ Directory: $STORAGE_DIR${NC}\n"

# 11. Initialize Git for self-update
echo -e "${YELLOW}[11/14] Setting up Git for self-update...${NC}"
cd "$INSTALL_DIR"

if [ ! -d "$INSTALL_DIR/.git" ]; then
    git init -q
    git remote add origin "https://github.com/$GITHUB_REPO.git" 2>/dev/null || \
        git remote set-url origin "https://github.com/$GITHUB_REPO.git"
    echo -e "${GREEN}✓ Git initialized (repo: $GITHUB_REPO)${NC}\n"
else
    git remote set-url origin "https://github.com/$GITHUB_REPO.git" 2>/dev/null || true
    echo -e "${GREEN}✓ Git already initialized, remote updated${NC}\n"
fi

# Try to fetch and set HEAD (non-fatal if no network)
git fetch origin main -q 2>/dev/null && \
    git reset --hard origin/main -q 2>/dev/null && \
    echo -e "${GREEN}✓ Synced to latest from GitHub${NC}\n" || \
    echo -e "${YELLOW}⚠ Could not fetch from GitHub (will work after first push)${NC}\n"

# 12. Create LaunchAgent Plist
echo -e "${YELLOW}[12/14] Creating LaunchAgent plist...${NC}"
mkdir -p "$LAUNCHAGENT_DIR"

PYTHON_PATH="$INSTALL_DIR/venv/bin/python3"

cat > "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ebm.mactool</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$INSTALL_DIR/main.py</string>
        <string>--web-ui</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/logs/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/logs/stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLISTEOF

chmod 644 "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
echo -e "${GREEN}✓ Plist created${NC}\n"

# 13. Load LaunchAgent
echo -e "${YELLOW}[13/14] Loading LaunchAgent...${NC}"
launchctl unload "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist" 2>/dev/null || true
sleep 1
launchctl load "$LAUNCHAGENT_DIR/$LAUNCHAGENT_LABEL.plist"
sleep 2
echo -e "${GREEN}✓ LaunchAgent loaded${NC}\n"

# 14. Final Status
echo -e "${YELLOW}[14/14] Verifying installation...${NC}\n"

if launchctl list | grep -q "$LAUNCHAGENT_LABEL"; then
    echo -e "${GREEN}✓ LaunchAgent is running${NC}"
else
    echo -e "${YELLOW}⚠ LaunchAgent status: pending (check in a moment)${NC}"
fi

echo ""
echo -e "${BLUE}================================================${NC}"
if [ "$IS_UPDATE" = true ]; then
    echo -e "${GREEN}✅ Update Complete!${NC}"
else
    echo -e "${GREEN}✅ Installation Complete!${NC}"
fi
echo -e "${BLUE}================================================${NC}\n"

echo "Configuration:"
echo "  Install Dir:      $INSTALL_DIR"
echo "  GitHub Repo:      $GITHUB_REPO"
echo "  LaunchAgent:      $LAUNCHAGENT_LABEL"
echo ""
echo "Web UI: http://localhost:8000"
echo "Logs:   $INSTALL_DIR/logs/mactool.log"
echo ""
echo "Future updates: Click 'Update' button in Web UI"
echo ""

echo -e "${BLUE}================================================${NC}"
echo -e "${YELLOW}⏳ Opening Web UI in 3 seconds...${NC}"
echo -e "${BLUE}================================================${NC}\n"

sleep 3
open "http://localhost:8000" || true

echo -e "${GREEN}Done!${NC}\n"
