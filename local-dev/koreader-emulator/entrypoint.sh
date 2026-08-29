#!/bin/bash
set -e
mkdir -p "$HOME"
touch "$HOME/.writable" 2>/dev/null || { echo "FATAL: HOME=$HOME is not writable"; exit 1; }
rm -f "$HOME/.writable"

# SDL needs a valid runtime dir or it warns and can misbehave.
export XDG_RUNTIME_DIR=/tmp/runtime-root
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
export SDL_VIDEODRIVER=x11

# A restarted container keeps its /tmp, so the previous boot's X lock and socket
# survive and Xvfb refuses the display ("Server is already active for display N").
# Restarting is the documented way to pick up a plugin edit, so clearing these is
# part of a normal start, not error recovery.
display_num="${DISPLAY#:}"
display_num="${display_num%%.*}"
rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}"

Xvfb "$DISPLAY" -screen 0 "${SCREEN_W}x${SCREEN_H}x24" -nolisten tcp &
for i in $(seq 1 60); do xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break; sleep 0.25; done
xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || { echo "FATAL: Xvfb never came up"; exit 1; }

# KOReader calls XSetInputFocus during init; without a running WM the window is
# not yet viewable and X returns BadMatch, killing the process. Start fluxbox and
# wait for it to actually own the screen before launching.
fluxbox >/tmp/fluxbox.log 2>&1 &
for i in $(seq 1 60); do
  xprop -display "$DISPLAY" -root _NET_SUPPORTING_WM_CHECK >/dev/null 2>&1 && break
  sleep 0.25
done
# The atom appears before fluxbox has finished mapping/reparenting. Without this
# settle, KOReader's XSetInputFocus during init hits a not-yet-viewable window and
# X returns BadMatch, which kills the process outright.
sleep 2

x11vnc -display "$DISPLAY" -forever -shared -nopw -quiet -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &

echo "noVNC on :6080  screen ${SCREEN_W}x${SCREEN_H}  starting KOReader..."
cd /opt/koreader
exec ./bin/koreader "$@"
