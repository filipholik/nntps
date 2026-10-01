#!/bin/bash
#
# start-floodlight.sh
#
# Builds and starts the Floodlight controller (with the patched web UI)
# for the NNTPS course, in the foreground — logs stream to this terminal
# Ctrl+C stops it. 
#
# Usage:
#   ./start-floodlight.sh             # build (if needed) and run, attached
#   ./start-floodlight.sh --rebuild   # force a clean rebuild, no cache, then run
#   ./start-floodlight.sh --detach    # old behavior: run in background instead
#   ./start-floodlight.sh --stop      # stop a --detach'd container
#   ./start-floodlight.sh --logs      # follow logs of a --detach'd container

set -e

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

# --- Handle standalone flags --------------------------------------------------
case "${1:-}" in
    --stop)
        echo "Stopping Floodlight container..."
        docker compose down
        exit 0
        ;;
    --logs)
        docker compose logs -f
        exit 0
        ;;
esac

# --- Parse remaining flags -------------------------------------------------------
REBUILD=false
DETACH=false

for arg in "$@"; do
    case "$arg" in
        --rebuild) REBUILD=true ;;
        --detach) DETACH=true ;;
        "") ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: $0 [--rebuild] [--detach | --stop | --logs]"
            exit 1
            ;;
    esac
done

# --- Build ---------------------------------------------------------------------
if [ "$REBUILD" = true ]; then
    echo "Rebuilding image from scratch (no cache)..."
    docker compose build --no-cache
else
    echo "Building image (using cache where possible)..."
    docker compose build
fi

echo ""
echo "  REST API:   http://localhost:8082/wm/core/controller/switches/json"
echo "  Web UI:     http://localhost:8082/ui/pages/index.html"
echo "  OpenFlow:   controller listens on port 6653 on the host"
echo ""
echo "Point Mininet at it, e.g.:"
echo "  sudo mn --controller=remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13"
echo ""

# --- Run -------------------------------------------------------------------------
if [ "$DETACH" = true ]; then
    echo "Starting Floodlight in the background..."
    docker compose up -d
    sleep 3
    if docker compose ps --status running | grep -q floodlight; then
        echo "Floodlight is up. To stop it: $0 --stop   To view logs: $0 --logs"
    else
        echo "Something looks off — checking logs:"
        docker compose logs --tail=50
        exit 1
    fi
else
    echo "Starting Floodlight (attached — press Ctrl+C to stop) ..."
    echo ""
    # Ensure the container is cleaned up if the script exits or is interrupted
    trap 'echo ""; echo "Stopping Floodlight..."; docker compose down' EXIT
    docker compose up
fi
