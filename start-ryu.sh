#!/bin/bash
#
# start-ryu.sh
#
# Runs the Ryu controller (latarc/ryu:4.34) for the NNTPS course,
# with /ryu bind-mounted so student edits take effect immediately.
#
# Usage:
#   ./start-ryu.sh                      # run simple_switch_13 (default app)
#   ./start-ryu.sh --app <module.path>  # run a specific Ryu app module
#   ./start-ryu.sh --gui                # run simple_switch_13 with the GUI topology viewer
#   ./start-ryu.sh --gui --app <module.path>   # GUI + a specific app
#   ./start-ryu.sh --pull               # just pull/update the image, don't run
#   ./start-ryu.sh --version            # sanity-check the image works at all
#   Example: ./start-ryu.sh --app ryu.app.hello_world

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$SCRIPT_DIR/docker/ryu"
VENDOR_DIR="$SCRIPT_DIR/ryu"

if [ ! -d "$COMPOSE_DIR" ]; then
    echo "Error: expected to find $COMPOSE_DIR — is this script still inside the nntps repo?"
    exit 1
fi

if [ ! -d "$VENDOR_DIR" ]; then
    echo "Error: expected Ryu source at $VENDOR_DIR but it's missing."
    echo "Did the repo clone fully, or did the vendored Ryu source not get committed?"
    exit 1
fi

cd "$COMPOSE_DIR"

# --- Prerequisite check -----------------------------------------------------
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not on your PATH."
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "Error: 'docker compose' (v2, the built-in plugin) was not found."
    exit 1
fi

# --- Parse flags --------------------------------------------------------------
APP="ryu.app.simple_switch_13"
GUI=false
ACTION="run"

while [ "$1" != "" ]; do
    case "$1" in
        --app)
            shift
            APP="$1"
            ;;
        --gui)
            GUI=true
            ;;
        --pull)
            ACTION="pull"
            ;;
        --version)
            ACTION="version"
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--app <module.path>] [--gui] [--pull] [--version]"
            exit 1
            ;;
    esac
    shift
done

# --- Handle simple actions ----------------------------------------------------
if [ "$ACTION" = "pull" ]; then
    echo "Pulling latarc/ryu:4.34..."
    docker compose pull
    exit 0
fi

if [ "$ACTION" = "version" ]; then
    docker compose run --rm \
        -e PYTHONPATH=/ryu_source \
        --entrypoint python3 \
        ryu-controller \
        /ryu_source/bin/ryu-manager --version
    exit 0
fi

# --- Run the controller --------------------------------------------------------
echo "Port note: Ryu here claims host ports 6633, 6653 and 8080."
echo "If Floodlight is still running from another session, stop it first"
echo "(./start-floodlight.sh --stop) or you'll get a port-in-use error."
echo ""

if [ "$GUI" = true ]; then
    echo "Starting Ryu with GUI topology viewer + $APP ..."
    echo "Once it's up, open http://127.0.0.1:8080/ in your browser."
    echo ""
    docker compose run --rm --service-ports \
        --entrypoint python3 \
        ryu-controller \
        -m ryu.cmd.manager \
        --observe-links \
        ryu.app.gui_topology.gui_topology \
        "$APP"
else
    echo "Starting Ryu with $APP ..."
    echo ""
    docker compose run --rm --service-ports \
        --entrypoint ryu-manager \
        ryu-controller \
        --observe-links \
        "$APP"
fi