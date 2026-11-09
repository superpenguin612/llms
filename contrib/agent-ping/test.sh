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
trap 't kill-server 2>/dev/null; kill $(cat "$tmp"/sleep*.pid 2>/dev/null) 2>/dev/null; rm -rf "$tmp"' EXIT
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
check "elicitation is an ask" "$(w "$p0")" '[0-9]+\|ask'
check "asks counted apart" "$(t show -gqv @agents_asking) $(n)" '1 0'
hook "$p0" claude "{$c,\"hook_event_name\":\"Notification\",\"notification_type\":\"permission_prompt\",\"message\":\"m\"}"
check "permission keeps an ask" "$(w "$p0")" '[0-9]+\|ask'
hook "$p0" claude "{$c,\"hook_event_name\":\"Stop\",\"stop_hook_active\":false}"
check "stop keeps an ask" "$(w "$p0")" '[0-9]+\|ask'
hook "$p0" claude "{$c,\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"answer\"}"
check "prompt clears an ask" "$(w "$p0")" ''
check "prompt clears the text" "$(t show -wqv -t "$p0" @agent_text)" ''
check "prompt starts the turn" "$(t show -pqv -t "$p0" @agent_turn_start)" '[0-9]{5,}'
hook "$p0" claude "{$c,\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"AskUserQuestion\",\"tool_input\":{\"questions\":[{\"question\":\"Q1?\"},{\"question\":\"Q2?\"}]}}"
check "dialog is an ask" "$(w "$p0")" '[0-9]+\|ask'
check "dialog text kept" "$(t show -wqv -t "$p0" @agent_text)" 'Q1\? \(\+1 more\)'
hook "$p0" claude "{$c,\"hook_event_name\":\"Notification\",\"notification_type\":\"permission_prompt\",\"message\":\"perm\"}"
check "permission keeps the ask text" "$(t show -wqv -t "$p0" @agent_text)" 'Q1\? \(\+1 more\)'
hook "$p0" claude "{$c,\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"a\"}"
hook "$p0" claude "{$c,\"hook_event_name\":\"Notification\",\"notification_type\":\"idle_prompt\",\"message\":\"m\"}"
check "idle marks" "$(w "$p0")" '[0-9]+\|idle'
TMUX="$sock,0,0" TMUX_PANE="$p0" CLAUDECODE=1 "$ap" ask "need a hand" >>"$out" </dev/null
check "ask command marks" "$(w "$p0")" '[0-9]+\|ask'
hook "$p0" claude "{$c,\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"a\"}"
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
check "session end clears the text" "$(t show -wqv -t "$p0" @agent_text)" ''
check "count 1 after end" "$(n)" 1

hook "$p2" claude '{"session_id":"id-quit","hook_event_name":"Stop"}'
hook "$p3" claude '{"session_id":"id-busy","hook_event_name":"Stop"}'
check "map rows" "$(cut -f1-5 "$map" | sort | tr '\t\n' ' ')" 's 0 0 claude id-claude s 1 0 codex id-codex s 2 0 claude id-quit s 3 0 claude id-busy '
hook "$p2" claude '{"session_id":"id-quit","hook_event_name":"SessionEnd","reason":"prompt_input_exit"}'
check "quit drops row" "$(grep -c id-quit "$map")" 0

printf '{"session_id":"z","hook_event_name":"Stop"}' | TMUX="$sock,0,0" "$ap" claude >>"$out"
check "outside tmux does nothing" "$(grep -c '	z$' "$map")" 0
check "stdout empty" "$(wc -c <"$out" | tr -d ' ')" 0

# Two clients: a plain one (OSC 9) and one that answers tmux's XTVERSION query as iTerm2.
{ sleep 600 & echo $! >"$tmp/sleep.pid"; wait; } | script -qc "TERM=xterm tmux -L $L attach -t s" /dev/null >"$tmp/client.out" 2>&1 &  # stdin open: EOF would type ^D into the pane
{ sleep 0.5; printf '\033P>|iTerm2 3.7.6\033\\'; sleep 600 & echo $! >"$tmp/sleep2.pid"; wait; } | script -qc "TERM=xterm tmux -L $L attach -t s" /dev/null >"$tmp/iterm.out" 2>&1 &
i=0; while [ "$(t list-clients -F '#{client_termtype}' | grep -c iTerm2)" = 0 ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
check "iTerm2 client seen" "$(t list-clients -F '#{client_termtype}' | grep -c iTerm2)" 1
t select-window -t s:0
hook "$p0" claude "{$c,\"hook_event_name\":\"Stop\"}"
check "focused pane not marked" "$(w "$p0")" ''
hook "$p2" claude "{$c,\"hook_event_name\":\"UserPromptSubmit\",\"prompt\":\"a\"}"
hook "$p2" claude "{$c,\"hook_event_name\":\"Stop\",\"last_assistant_message\":\"short\"}"
check "unfocused window still marked" "$(w "$p2")" '[0-9]+\|done'
check "short turn: no banner" "$(grep -c 'short' "$tmp/client.out")" 0
t set -p -t "$p2" @agent_turn_start $(($(date +%s) - 200))
hook "$p2" claude "{$c,\"hook_event_name\":\"Stop\",\"last_assistant_message\":\"\\nAll green.\\nmore\"}"
check "long turn: OSC 9 banner" "$(grep -c "$(printf '\033]9;Claude done · s:2 · 3m: All green.\007')" "$tmp/client.out")" '[1-9]'
pl=$(grep -ao 'Custom=id=agent-ping:[A-Za-z0-9+/=]*' "$tmp/iterm.out" | tail -1 | cut -d: -f2 | base64 -d)
check "long turn: iTerm2 payload" "$(printf '%s' "$pl" | jq -r '[.title, .body, .group] | join("|")')" 'Claude done · s:2 · 3m\|All green\.\|s:2'
check "iTerm2 client gets no OSC 9" "$(grep -c "$(printf '\033]9;')" "$tmp/iterm.out")" 0
check "flash window set" "$(t show -gqv @agent_flash_win)" "$(t display -p -t "$p2" '#{window_id}')"
sleep 5.3
check "flash window clears after 5 s" "$(t show -gqv @agent_flash_win)" ''

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
