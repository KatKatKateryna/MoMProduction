#!/usr/bin/env bash
# Sets up the MoM map viewer on a fresh Linux server.
# Installs: miniconda + mom-map env, pygeoapi behind gunicorn, nginx as front-end.
# Usage: ./setup.sh [--url http://YOUR_IP_OR_DOMAIN]

set -e
set -o pipefail

############################
# CONFIG
############################

REPO_URL="https://github.com/KatKatKateryna/MoMProduction.git"
REPO_BRANCH="interactive-map"
REPO_DIR="$HOME/MoMProduction"
MODULE_DIR="$REPO_DIR/map_viewer"
DATA_DIR="$REPO_DIR/data"

if command -v conda >/dev/null 2>&1; then
    CONDA_DIR="$(conda info --base)"
else
    CONDA_DIR="$HOME/miniconda3"
fi
CONDA_ENV_NAME="mom-map"
MINICONDA_INSTALLER="Miniconda3-latest-Linux-x86_64.sh"
MINICONDA_URL="https://repo.anaconda.com/miniconda/$MINICONDA_INSTALLER"

PYGEOAPI_PORT=5000
SERVICE_NAME="mom-map"
NGINX_SITE="mom-map"
LOG_DIR="/var/log/mom-map"

############################
# ARGUMENTS
############################

PUBLIC_URL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url) PUBLIC_URL="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$PUBLIC_URL" ]]; then
    # Auto-detect public IP
    PUBLIC_IP=$(curl -sf https://api.ipify.org || curl -sf https://ifconfig.me || echo "")
    if [[ -n "$PUBLIC_IP" ]]; then
        PUBLIC_URL="http://${PUBLIC_IP}"
        echo "Auto-detected public URL: $PUBLIC_URL"
    else
        echo "ERROR: Could not detect public IP. Pass --url http://YOUR_IP_OR_DOMAIN"
        exit 1
    fi
fi

echo "Public URL: $PUBLIC_URL"

############################
# SYSTEM PACKAGES
############################

echo "Installing system packages..."
sudo apt update -y
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
    curl \
    git \
    wget \
    nginx

############################
# REPOSITORY
############################

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "Cloning repository..."
    git clone --branch "$REPO_BRANCH" --single-branch --depth 1 "$REPO_URL" "$REPO_DIR"
else
    echo "Repository exists. Updating..."
    cd "$REPO_DIR"
    git fetch origin
    git reset --hard "origin/$REPO_BRANCH"
fi

############################
# MINICONDA
############################

if [ ! -d "$CONDA_DIR" ]; then
    echo "Installing Miniconda..."
    cd "$HOME"
    [ ! -f "$MINICONDA_INSTALLER" ] && wget "$MINICONDA_URL"
    bash "$MINICONDA_INSTALLER" -b -p "$CONDA_DIR"
fi

"$CONDA_DIR/bin/conda" init bash || true
source "$CONDA_DIR/etc/profile.d/conda.sh"
"$CONDA_DIR/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main || true
"$CONDA_DIR/bin/conda" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r   || true

conda config --add channels conda-forge || true
conda config --set channel_priority strict

############################
# CONDA ENV
############################

cd "$MODULE_DIR"

if conda env list | grep -q "^$CONDA_ENV_NAME "; then
    echo "Updating conda env $CONDA_ENV_NAME..."
    conda env update -n "$CONDA_ENV_NAME" -f environment.yml --prune
else
    echo "Creating conda env $CONDA_ENV_NAME..."
    conda env create -n "$CONDA_ENV_NAME" -f environment.yml
fi

conda activate "$CONDA_ENV_NAME"
export MODULE_DIR  # needed by the Python obfuscation snippet below

############################
# JS OBFUSCATION
############################

echo "Installing javascript-obfuscator..."
npm install -g javascript-obfuscator --silent

echo "Building obfuscated map.dist.html..."
# Extract <script> block, obfuscate, recombine into map.dist.html
python3 - <<'PYEOF'
import re, subprocess, tempfile, os, sys

src = open(os.path.join(os.environ['MODULE_DIR'], 'map.html')).read()
m = re.search(r'<script>(.*?)</script>', src, re.DOTALL)
if not m:
    print("ERROR: no <script> block found in map.html", file=sys.stderr)
    sys.exit(1)

with tempfile.NamedTemporaryFile(suffix='.js', mode='w', delete=False) as f:
    f.write(m.group(1))
    src_js = f.name
out_js = src_js.replace('.js', '.obf.js')

subprocess.run([
    'javascript-obfuscator', src_js,
    '--output', out_js,
    '--compact', 'true',
    '--identifier-names-generator', 'hexadecimal',
    '--string-array', 'true',
    '--rotate-string-array', 'true',
    '--shuffle-string-array', 'true',
    '--string-array-encoding', 'base64',
    '--self-defending', 'true',
], check=True)

obf_js = open(out_js).read()
result = re.sub(r'(?s)(<script>).*?(</script>)', r'\1\n' + obf_js.replace('\\', '\\\\') + r'\n\2', src)
dist = os.path.join(os.environ['MODULE_DIR'], 'map.dist.html')
open(dist, 'w').write(result)
os.unlink(src_js)
os.unlink(out_js)
print(f"Written: {dist}")
PYEOF

############################
# PYGEOAPI CONFIG
############################

echo "Writing resolved pygeoapi config..."
mkdir -p "$LOG_DIR"

sed \
    -e "s|__PUBLIC_URL__|${PUBLIC_URL}|g" \
    -e "s|\.\./data|${DATA_DIR}|g" \
    "$MODULE_DIR/pygeoapi-config.yml" \
    > "$MODULE_DIR/pygeoapi-config.resolved.yml"

echo "Generating pygeoapi OpenAPI spec..."
PYGEOAPI_CONFIG="$MODULE_DIR/pygeoapi-config.resolved.yml" \
PYGEOAPI_OPENAPI="$MODULE_DIR/pygeoapi-openapi.yml" \
    "$CONDA_DIR/envs/$CONDA_ENV_NAME/bin/pygeoapi" openapi generate \
        "$MODULE_DIR/pygeoapi-config.resolved.yml" \
        --output-file "$MODULE_DIR/pygeoapi-openapi.yml"

############################
# SYSTEMD SERVICE
############################

echo "Writing systemd service..."
GUNICORN="$CONDA_DIR/envs/$CONDA_ENV_NAME/bin/gunicorn"

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<SERVICE
[Unit]
Description=MoM map viewer — pygeoapi via gunicorn
After=network.target

[Service]
User=${USER}
WorkingDirectory=${MODULE_DIR}
Environment=PYGEOAPI_CONFIG=${MODULE_DIR}/pygeoapi-config.resolved.yml
Environment=PYGEOAPI_OPENAPI=${MODULE_DIR}/pygeoapi-openapi.yml
ExecStart=${GUNICORN} \
    --workers 4 \
    --bind 127.0.0.1:${PYGEOAPI_PORT} \
    --access-logfile ${LOG_DIR}/access.log \
    --error-logfile  ${LOG_DIR}/error.log \
    --log-level warning \
    pygeoapi.flask_app:APP
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable  "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

############################
# NGINX
############################

echo "Configuring nginx..."
sudo tee /etc/nginx/sites-available/${NGINX_SITE} > /dev/null <<NGINX
server {
    listen 80;
    server_name _;

    root ${MODULE_DIR};
    index map.dist.html;

    # Serve the obfuscated map page
    location / {
        try_files \$uri /map.dist.html;
    }

    # Proxy OGC API Features to pygeoapi
    location /collections/ {
        proxy_pass         http://127.0.0.1:${PYGEOAPI_PORT}/collections/;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }
}
NGINX

# Enable site, disable default if still linked
sudo ln -sf /etc/nginx/sites-available/${NGINX_SITE} /etc/nginx/sites-enabled/${NGINX_SITE}
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t
sudo systemctl enable  nginx
sudo systemctl restart nginx

############################
# DONE
############################

echo ""
echo "======================================"
echo "Map viewer setup complete."
echo "Open in browser: ${PUBLIC_URL}"
echo ""
echo "Service management:"
echo "  sudo systemctl status  ${SERVICE_NAME}"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "Logs: ${LOG_DIR}/"
echo "======================================"