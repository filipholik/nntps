#!/bin/bash
#
# start-floodlight.sh
#
# Builds and starts the Floodlight controller (with the patched web UI)
# for the NNTPS course. Run this once after cloning the repo, and again
# any time you want to rebuild from scratch.
#
# Usage:
#   ./start-floodlight.sh          # build (if needed) and start
#   ./start-floodlight.sh --rebuild   # force a clean rebuild, no cache
#   ./start-floodlight.sh --stop      # stop the container
#   ./start-floodlight.sh --logs      # follow the controller's logs

set -e

# Resolve the script's own directory so it works no matter where you run it from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$SCRIPT_DIR/docker/floodlight-webui"

if [ ! -d "$COMPOSE_DIR" ]; then
    echo "Error: expected to find $COMPOSE_DIR — is this script still inside the nntps repo?"
    exit 1
fi

cd "$COMPOSE_DIR"

# --- Prerequisite check -----------------------------------------------------
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not on your PATH."
    echo "Install Docker first, then re-run this script."
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "Error: 'docker compose' (v2, the built-in plugin) was not found."
    echo "Make sure you have a recent Docker installation with Compose support."
    exit 1
fi

# --- Handle flags ------------------------------------------------------------
case "$1" in
    --stop)
        echo "Stopping Floodlight container..."
        docker compose down
        exit 0
        ;;
    --logs)
        docker compose logs -f
        exit 0
        ;;
    --rebuild)
        echo "Rebuilding image from scratch (no cache)..."
        docker compose build --no-cache
        ;;
    "")
        echo "Building image (using cache where possible)..."
        docker compose build
        ;;
    *)
        echo "Unknown option: $1"
        echo "Usage: $0 [--rebuild | --stop | --logs]"
        exit 1
        ;;
esac

# --- Start the container ------------------------------------------------------
echo "Starting Floodlight..."
docker compose up -d

# Give it a moment to boot before checking status
sleep 3

if docker compose ps --status running | grep -q floodlight; then
    echo ""
    echo "Floodlight is up."
    echo "  REST API:   http://localhost:8082/wm/core/controller/switches/json"
    echo "  Web UI:     http://localhost:8082/  (or /pages/login.html)"
    echo "  OpenFlow:   controller listens on port 6655 on the host"
    echo ""
    echo "Point Mininet at it, e.g.:"
    echo "  sudo mn --controller=remote,ip=127.0.0.1,port=6655 --switch ovsk,protocols=OpenFlow13 --topo=tree,3"
    echo ""
    echo "To stop it later:  $0 --stop"
    echo "To view logs:      $0 --logs"
else
    echo ""
    echo "Something looks off — the container isn't running. Checking logs:"
    docker compose logs --tail=50
    exit 1
fi
