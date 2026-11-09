#!/bin/sh
# Drives agent-ping against a scratch tmux server (never the running one). Exits non-zero on failure.
here=$(cd "$(dirname "$0")" && pwd)
ap="$here/agent-ping"
L=agent-ping-test
tmp=$(mktemp -d)
export HOME="$tmp" XDG_STATE_HOME="$tmp/state"
# mise shims under a fake HOME look versions up online on every call; use system tools.
PATH=$(printf %s "$PATH" | tr : '\n' | grep -v /mise/shims | paste -sd: -)
map="$tmp/state/agent-ping/panes.tsv"
unset TMUX TMUX_PANE
t() { tmux -L "$L" "$@"; }
t kill-server 2>/dev/null
trap 't kill-server 2>/dev/null; [ -f "$tmp/sleep.pid" ] && kill "$(cat "$tmp/sleep.pid")"; rm -rf "$tmp"' EXIT
fail=0
check() { # check NAME GOT WANT(ERE)
	if printf '%s\n' "$2" | grep -Eq "^($3)\$"; then echo "ok   $1"; else echo "FAIL $1: got '$2', want '$3'"; fail=1; fi
}

# Stub agents so restore's send-keys runs something harmless and visible.
mkdir -p "$tmp/bin"
for a in claude codex; do printf '#!/bin/sh\necho "STUB %s $*"\n' $a >"$tmp/bin/$a"; chmod +x "$tmp/bin/$a"; done
sh="env PATH=$tmp/bin:/usr/bin:/bin PS1=$ sh"
t -f /dev/null new-session -d -s s -x 120 -y 30 "$sh"
t new-window -d -t s:1 "$sh"
t new-window -d -t s:2 "$sh"
t new-window -d -t s:3 "sleep 600"
sock=$(t display -p '#{socket_path}')
p0=$(t display -p -t s:0.0 '#{pane_id}'); p1=$(t display -p -t s:1.0 '#{pane_id}')
p2=$(t display -p -t s:2.0 '#{pane_id}'); p3=$(t display -p -t s:3.0 '#{pane_id}')

out="$tmp/stdout"
hook() { # hook PANE AGENT JSON
	printf '%s' "$3" | TMUX="$sock,0,0" TMUX_PANE="$1" "$ap" "$2" >>"$out"
}
w() { t show -wqv -t "$1" @agent_waiting; }
n() { t show -gqv @agents_waiting; }
c='"session_id":"id-claude","cwd":"/tmp","transcript_path":"/x"'
x='"session_id":"id-codex","cwd":"/tmp","turn_id":"t1","model":"m"'

hook "$p0" claude "{$c,\"hook_event_name\":\"Notification\",\"notification_type\":\"permission_prompt\",\"message\":\"m\"}"
check "claude permission marks" "$(w "$p0")" '[0-9]+\|permission'
check "count 1" "$(n)" 1
check "pane session" "$(t show -pqv -t "$p0" @agent_session)" 'claude:id-claude'
hook "$p0" claude "{$c,\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"go on\"}"
check "prompt clears" "$(w "$p0")" ''
check "count 0" "$(n)" 0
hook "$p0" claude "{$c,\"hook_event_name\":\"Notification\",\"notification_type\":\"auth_success\",\"message\":\"m\"}"
check "other notification ignored" "$(w "$p0")" ''
hook "$p0" claude "{$c,\"hook_event_name\":\"Notification\",\"notification_type\":\"elicitation_dialog\",\"message\":\"m\"}"
check "elicitation is a question" "$(w "$p0")" '[0-9]+\|question'
hook "$p0" claude "{$c,\"hook_event_name\":\"Stop\",\"stop_hook_active\":false}"
check "stop marks done" "$(w "$p0")" '[0-9]+\|done'
hook "$p0" claude "{$c,\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"keepalive: reply \\\"ok\\\" only\"}"
check "keepalive keeps mark" "$(w "$p0")" '[0-9]+\|done'
t set -w -t "$p0" @agent_waiting "1|done"
hook "$p0" claude "{$c,\"hook_event_name\":\"Stop\",\"stop_hook_active\":false}"
check "keepalive stop is a no-op" "$(w "$p0")" '1\|done'
check "keepalive record cleared" "$(t show -pqv -t "$p0" @agent_keepalive)" ''
hook "$p0" claude "{$c,\"hook_event_name\":\"Stop\",\"stop_hook_active\":false}"
check "next stop marks again" "$(w "$p0")" '[0-9]{5,}\|done'

hook "$p1" codex "{$x,\"hook_event_name\":\"PermissionRequest\",\"permission_mode\":\"default\",\"tool_name\":\"shell\",\"tool_input\":{}}"
check "codex permission marks" "$(w "$p1")" '[0-9]+\|permission'
check "count 2" "$(n)" 2
hook "$p1" codex "{$x,\"hook_event_name\":\"UserPromptSubmit\",\"permission_mode\":\"default\",\"prompt\":\"y\"}"
hook "$p1" codex "{$x,\"hook_event_name\":\"Stop\",\"permission_mode\":\"default\",\"stop_hook_active\":false,\"last_assistant_message\":null}"
check "codex stop marks done" "$(w "$p1")" '[0-9]+\|done'
hook "$p0" claude "{$c,\"hook_event_name\":\"SessionEnd\",\"reason\":\"other\"}"
check "session end clears" "$(w "$p0")" ''
check "count 1 after end" "$(n)" 1

hook "$p2" claude '{"session_id":"id-quit","hook_event_name":"Stop"}'
hook "$p3" claude '{"session_id":"id-busy","hook_event_name":"Stop"}'
check "map rows" "$(cut -f1-5 "$map" | sort | tr '\t\n' ' ')" 's 0 0 claude id-claude s 1 0 codex id-codex s 2 0 claude id-quit s 3 0 claude id-busy '
hook "$p2" claude '{"session_id":"id-quit","hook_event_name":"SessionEnd","reason":"prompt_input_exit"}'
check "quit drops row" "$(grep -c id-quit "$map")" 0

before=$(t show -wqv -t "$p3" @agent_waiting)
printf '{"session_id":"z","hook_event_name":"Stop"}' | TMUX="$sock,0,0" "$ap" claude >>"$out"
check "outside tmux does nothing" "$(grep -c '	z$' "$map")" 0
check "stdout empty" "$(wc -c <"$out" | tr -d ' ')" 0

# A client looking at window 0's active pane: no mark there.
{ sleep 600 & echo $! >"$tmp/sleep.pid"; wait; } | script -qc "TERM=xterm tmux -L $L attach -t s" /dev/null >"$tmp/client.out" 2>&1 &  # stdin open: EOF would type ^D into the pane
i=0; while [ -z "$(t list-clients)" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
t select-window -t s:0
hook "$p0" claude "{$c,\"hook_event_name\":\"Stop\"}"
check "focused pane not marked" "$(w "$p0")" ''
hook "$p2" claude "{$c,\"hook_event_name\":\"Stop\"}"
check "unfocused window still marked" "$(w "$p2")" '[0-9]+\|done'
check "banner on client tty" "$(grep -c "$(printf '\033]9;s:2 done\007')" "$tmp/client.out")" '[1-9]'

s=$(date +%s%N); hook "$p1" codex "{$x,\"hook_event_name\":\"Stop\"}"; ms=$((($(date +%s%N) - s) / 1000000))
check "hook under 100 ms (${ms} ms)" "$([ $ms -lt 100 ] && echo y)" y

TMUX="$sock,0,0" "$ap" restore
sleep 0.5
check "restore resumes claude" "$(t capture-pane -p -t s:0.0 | grep -c 'STUB claude --resume id-claude')" '[1-9]'
check "restore resumes codex" "$(t capture-pane -p -t s:1.0 | grep -c 'STUB codex resume id-codex')" '[1-9]'
check "restore skips busy pane" "$(t display -p -t s:3.0 '#{pane_current_command}')" sleep
hook "$p1" codex "{$x,\"hook_event_name\":\"SessionEnd\",\"reason\":\"other\"}"
check "codex quit drops row" "$(grep -c id-codex "$map")" 0
exit $fail
