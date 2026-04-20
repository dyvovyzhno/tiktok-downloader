#!/bin/bash
#
# start-tiktok-downloader.sh — lifecycle manager for the TikTok downloader bot.
#
# Default invocation (no flags): keep-alive. If the bot is not running, build
# the binary if it is missing and start it. This is what cron runs.
#
# Flags:
#   -d   deploy: git pull, sync venv with requirements.txt, rebuild binary,
#        restart bot. Safe to run at any time.
#   -r   hard reset: kill, wipe dist/build/venv, recreate everything from
#        scratch, then start. Use when deps changed in incompatible ways or
#        the venv is broken.
#   -s   stop: kill the running binary.
#   -h   print this help.
#
# In all cases the cron entries are ensured (unless they are explicitly
# disabled with a comment prefix starting with "#").

set -e

cd "$(dirname "$0")"

BINARY="dist/tiktok-downloader"
VENV="venv"

print_usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

require_token() {
    if ! grep -q "^API_TOKEN" .env 2>/dev/null; then
        echo "API_TOKEN not set in .env" >&2
        exit 1
    fi
}

is_running() {
    pgrep -f "$BINARY" >/dev/null 2>&1
}

stop_bot() {
    if is_running; then
        killall tiktok-downloader 2>/dev/null || true
        echo "bot stopped"
    else
        echo "bot was not running"
    fi
}

ensure_venv() {
    if [ ! -d "$VENV" ]; then
        python3 -m venv "$VENV"
        echo "venv created"
    fi
}

install_requirements() {
    ensure_venv
    # shellcheck source=/dev/null
    . "$VENV/bin/activate"
    pip install -q -r requirements.txt
    echo "requirements installed"
}

build_binary() {
    # shellcheck source=/dev/null
    . "$VENV/bin/activate"
    rm -rf dist build
    pyinstaller main.py --onefile --name tiktok-downloader \
        --collect-submodules opentelemetry \
        >/dev/null
    chmod a+x "$BINARY"
    echo "binary built"
}

start_bot() {
    if is_running; then
        echo "already running"
        return
    fi
    if [ ! -x "$BINARY" ]; then
        install_requirements
        build_binary
    fi
    nohup "./$BINARY" >/dev/null 2>&1 &
    disown
    echo "bot started"
}

setup_cron() {
    # Do not re-add cron entries if any line (commented or not) mentions
    # the script path — this lets a human disable cron by prefixing lines
    # with `#` without the script silently re-adding them.
    local script_path
    script_path=$(readlink -f "$0")
    if crontab -l 2>/dev/null | grep -qF "$(basename "$script_path")"; then
        return
    fi
    {
        crontab -l 2>/dev/null || true
        echo "*/15 * * * * $script_path"
        echo "@reboot $script_path"
    } | crontab -
    echo "cron installed"
}

deploy() {
    echo "== deploy =="
    git pull --ff-only
    stop_bot
    install_requirements
    build_binary
    start_bot
}

hard_reset() {
    echo "== hard reset =="
    stop_bot
    rm -rf dist build "$VENV"
    install_requirements
    build_binary
    start_bot
}

action=""
while getopts "drsh" flag; do
    case "${flag}" in
        d) action="deploy" ;;
        r) action="reset" ;;
        s) action="stop" ;;
        h) print_usage; exit 0 ;;
        *) print_usage; exit 1 ;;
    esac
done

require_token

case "$action" in
    deploy) deploy ;;
    reset)  hard_reset ;;
    stop)   stop_bot ;;
    *)      start_bot ;;
esac

setup_cron
