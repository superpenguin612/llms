import type { Register } from 'claude-code'

import {
  type Item,
  type Usage,
  duration,
  fitTail,
  fold,
  parseSession,
  pending,
  segmentOf,
  sum,
  totalTokens,
} from './delegate'
import {
  PANE,
  TITLE,
  follow,
  loadRecords,
  readExpanded,
  readFollowing,
  readLog,
  readText,
  readView,
  rowOf,
  toEnd,
  writeExpanded,
  writeView,
  type Row,
} from './rows'

const money = (n: number) => `$${n.toFixed(n < 0.01 ? 4 : 2)}`
const count = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n))
const tokens = (u: Usage) =>
  `in ${count(u.input)} · out ${count(u.output)} · cache read ${count(u.cache_read)} · cache write ${count(u.cache_write)} · total ${count(totalTokens(u))} · ${money(u.cost)}`
const tokLine = (u: Usage) => `${count(totalTokens(u))} tok ${money(u.cost)}`
const ago = (ms: number) => `last activity ${duration(ms / 1000)} ago`

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'delegate', description: 'Show delegate workers and their sessions in a pane' })
    // Redraw once a second while the pane shows: the files are read while drawing.
    // While following a session, stay at its end on every redraw.
    $.clock.every(1000, async () => {
      const panes = await $.ui.panes()
      if (!panes.some(p => p.id === PANE && p.isShown)) return
      $.ui.invalidate('ui.render')
      if ((await readView($)).task && (await readFollowing($))) await toEnd($)
    })
    return next(e)
  })

  // The person scrolling: up stops following, reaching the end again resumes it.

  on('command.run', { command: 'delegate' }, async $ => {
    // /delegate toggles the pane.
    if ((await $.ui.panes()).some(p => p.id === PANE)) {
      await $.ui.close({ id: PANE })
      return { text: 'Delegate pane closed.' }
    }
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    if (!opened.isPlaced) return { text: `Delegate pane not drawn yet: ${opened.reason}` }
    return { text: 'Delegate pane opened. /delegate again or q in the pane closes it.' }
  })

  on('ui.render', { component: 'Pane', requestId: 'delegate' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const now = await $.clock.now()
    const records = await loadRecords($)
    const at = await readView($)
    const rec = records.find(r => r.task === at.task)

    // A live run's activity with its spinner, warning-colored once stalled; a finished run's state.
    const state = (row: Row) => (
      <Text color={row.act.stalled ? 'warning' : row.live ? 'success' : 'subtle'}>
        {row.act.spin && `${row.act.spin} `}
        {row.act.text}
      </Text>
    )

    if (!rec) {
      const rows = await Promise.all(pending(records).map(r => rowOf($, r, now)))
      return (
        <Box flexDirection="column">
          {rows.length === 0 && <Text dimColor>No live or uncleaned delegate runs.</Text>}
          {rows.map(row => (
            <Box key={`row-${row.rec.task}`} flexDirection="column" marginBottom={1}>
              <Button
                key={`open-${row.rec.task}`}
                plain
                onPress={async () => {
                  await writeView($, () => ({ task: row.rec.task }))
                  await follow($)
                }}
              >
                {row.rec.task} {state(row)} <Text dimColor>
                  {row.model} · {duration(row.elapsed)}
                </Text>
              </Button>
              <Text dimColor>
                {tokLine(row.total)}
                {row.live ? ` · ${ago(row.act.idle)}` : ''}
              </Text>
              {row.result && <Text dimColor wrap="truncate-end">{row.result}</Text>}
            </Box>
          ))}
          <Button key="close" hotkey="q" plain dimColor onPress={() => $.ui.close({ id: PANE })}>
            close
          </Button>
        </Box>
      )
    }

    const row = await rowOf($, rec, now)
    const attempts = row.attempts
    const index = Math.min(at.attempt ?? attempts.length - 1, attempts.length - 1)
    const attempt = attempts[index]
    const segment =
      row.live && index === attempts.length - 1
        ? row.current
        : attempt?.session
          ? segmentOf(parseSession(await readLog($, attempt.session)), attempts.map(a => a.session), index)
          : undefined
    const isFollowing = await readFollowing($)
    const brief = (await readText($, `${rec.run}/brief.md`)) ?? ''
    const open = await readExpanded($)
    const { items, dropped } = fitTail(segment?.items ?? [{ kind: 'user', text: brief }], open)
    const usage = attempt?.usage ?? segment?.usage
    const isFirst = !attempts.slice(0, index).some(a => a.session === attempt?.session)

    const back = () => writeView($, () => ({}))
    const toggle = (id: string) => writeExpanded($, list => (list.includes(id) ? list.filter(x => x !== id) : [...list, id]))

    const draw = (item: Item, i: number) => {
      if (item.kind === 'user')
        return (
          <Box key={`item-${i}`} flexDirection="column" marginBottom={1}>
            <Text color="suggestion" bold>
              {isFirst ? 'brief' : 'feedback'}
            </Text>
            <Text>{item.text.trim()}</Text>
          </Box>
        )
      if (item.kind === 'text')
        return (
          <Box key={`item-${i}`} marginBottom={1}>
            <Text>{item.text}</Text>
          </Box>
        )
      if (item.kind === 'error')
        return (
          <Box key={`item-${i}`} marginBottom={1}>
            <Text color="error">error: {item.text}</Text>
          </Box>
        )
      const isOpen = open.includes(item.id)
      const out = item.output === undefined ? undefined : fold(item.output, isOpen)
      const long = fold(item.output ?? '', false).hidden
      return (
        <Box key={`item-${i}`} flexDirection="column" marginBottom={1}>
          <Text>
            <Text color="warning" bold>
              {item.name}
            </Text>{' '}
            {item.args}
            {item.ended !== undefined && <Text dimColor> ({duration((item.ended - item.started) / 1000)})</Text>}
          </Text>
          {out === undefined ? (
            <Text dimColor> … running {duration((now - item.started) / 1000)}</Text>
          ) : (
            <Box flexDirection="column" paddingLeft={2}>
              <Text dimColor color={item.isError ? 'error' : undefined}>
                {out.shown || '(no output)'}
              </Text>
              {long > 0 && (
                <Button key={`fold-${item.id}`} plain dimColor onPress={() => toggle(item.id)}>
                  {isOpen ? 'fold' : `… ${long} more lines`}
                </Button>
              )}
            </Box>
          )}
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        <Box flexDirection="row" gap={1}>
          <Button key="back" hotkey="b" onPress={back}>
            ← all runs (b)
          </Button>
          <Text bold>{rec.task}</Text>
          <Text dimColor>{duration(row.elapsed)}</Text>
        </Box>
        <Text>
          {state(row)}
          <Text dimColor>{row.live ? ` · ${ago(row.act.idle)}` : ''}</Text>
        </Text>
        <Box flexDirection="row" gap={1}>
          <Button key="end" hotkey="e" onPress={() => follow($)}>
            ↓ jump to end (e)
          </Button>
          <Text dimColor>
            {isFollowing ? 'following' : 'paused'} · attempt {tokLine(usage ?? sum([]))} · run {tokLine(row.total)}
          </Text>
        </Box>
        <Text>
          {attempt?.model ?? row.model} · attempt {index + 1}/{Math.max(attempts.length, 1)}
          {attempt ? ` (${attempt.outcome})` : ''}
        </Text>
        {usage && <Text dimColor>attempt: {tokens(usage)}</Text>}
        <Text dimColor>run: {tokens(row.total)}</Text>
        {attempts.length > 1 && (
          <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
            {attempts.map((a, i) => (
              <Button
                key={`attempt-${i}`}
                hotkey={i < 9 ? String(i + 1) : undefined}
                variant={i === index ? 'primary' : undefined}
                label={`${i + 1} ${a.model} ${a.outcome}`}
                onPress={async () => {
                  await writeView($, v => ({ ...v, attempt: i === attempts.length - 1 ? undefined : i }))
                  await follow($)
                }}
              />
            ))}
          </Box>
        )}
        <Box marginTop={1} flexDirection="column">
          {dropped > 0 && <Text dimColor>({dropped} earlier entries not shown)</Text>}
          {items.map(draw)}
          {attempt?.session === undefined && row.live && <Text dimColor>Waiting for pi's session log…</Text>}
        </Box>
        {row.resultMd && (
          <Box key="result" flexDirection="column" marginBottom={1}>
            <Text color="suggestion" bold>
              result.md
            </Text>
            <Text>{row.resultMd.trim()}</Text>
          </Box>
        )}
        {/* Following keeps the view at its end, so its state and the way back are repeated here. */}
        <Box key="status-end" flexDirection="column" marginTop={1}>
          <Text dimColor>{'─'.repeat(40)}</Text>
          <Text>
            {state(row)}
            <Text dimColor>{row.live ? ` · ${ago(row.act.idle)}` : ''}</Text>
          </Text>
          <Text dimColor>
            {isFollowing ? 'following' : 'paused'} · attempt {tokLine(usage ?? sum([]))} · run {tokLine(row.total)}
          </Text>
        </Box>
        <Box flexDirection="row" gap={2}>
          <Button key="back-end" onPress={back}>
            ← all runs (b)
          </Button>
          <Button key="close" hotkey="q" plain dimColor onPress={() => $.ui.close({ id: PANE })}>
            close (q)
          </Button>
        </Box>
      </Box>
    )
  })
}
