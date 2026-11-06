import type { Register } from 'claude-code'

import { register as registerPane } from './register'
import { bandOrder, duration, look, pending, rowLine, shortModel, widths } from './delegate'
import { PANE, TITLE, follow, loadRecords, rowOf, writeView } from './rows'

const MAX_ROWS = 5

// The engine loads one hooks module per plugin (`hooks.json` `modules` refuses a second
// entry, hook-load-failed past it), so this module is the one: it registers the pane's
// hooks through ./register.tsx and adds the band's on top.
export const register: Register = (on, options) => {
  registerPane(on, options)
  registerBand(on)
}

const registerBand: Register = on => {
  // The pane's hook holds `session.start` plain; this one names what the band is for: a
  // session no person sits at (-p, the SDK) never shows the band, so its clock is noise.
  on('session.start', { isInteractive: true }, async ($, e, next) => {
    // The band redraws when told to: once every two seconds, so a run's state and spinner
    // stay current.
    // shortcut: each tick re-reads the run folders and live runs' session logs; cache by file size and mtime if the band shows in profiles or with many runs.
    $.clock.every(2000, () => $.ui.invalidate('ui.render'))
    return next(e)
  })

  // One row per live or uncleaned run the pane would list, so its state is visible without
  // opening the pane; a digit presses it while the band holds the focus.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // Surveys hold the band: a hook passes.
    if (e.props.hasSurvey) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    const now = await $.clock.now()
    const records = await loadRecords($)
    const rows = bandOrder(await Promise.all(pending(records).map(r => rowOf($, r, now))))
    // Nothing to show: pass, so the band takes no room.
    if (rows.length === 0) return next(e)

    const shown = rows.slice(0, MAX_ROWS)
    // One column set for all rows, cut to what the band holds.
    const w = widths(
      shown.map(row => {
        const lk = look(row.state, row.live, row.act.stalled, row.act.spin)
        return { task: row.rec.task, word: lk.word, model: shortModel(row.model), elapsed: duration(row.elapsed) }
      }),
    )

    return (
      <Box flexDirection="column">
        {shown.map((row, i) => {
          const lk = look(row.state, row.live, row.act.stalled, row.act.spin)
          // A live run shows what its worker is doing; a finished one, result.md's first line.
          const line = rowLine(
            {
              icon: lk.icon,
              task: row.rec.task,
              word: lk.word,
              model: shortModel(row.model),
              elapsed: duration(row.elapsed),
              detail: row.live ? row.act.text : (row.result ?? ''),
            },
            e.props.bodyColumns - (String(i + 1).length + 2),
            w,
          )
          // The pieces grouped the way they are colored; joined they are the line as cut.
          // [text, color, dim]: icon and state word in the state's color, the task plain, the rest dim.
          const colored: [string, string, boolean][] = [
            [line.icon, lk.color, false],
            [line.task, '', false],
            [line.word, lk.color, false],
            [line.model, '', true],
            [line.elapsed, '', true],
            [line.detail, '', true],
          ]
          return (
            <Button
              key={`band-${row.rec.task}`}
              plain
              hotkey={String(i + 1)}
              onPress={async () => {
                await writeView($, () => ({ task: row.rec.task }))
                if (!(await $.ui.panes()).some(p => p.id === PANE)) await $.ui.open({ id: PANE, title: TITLE })
                await follow($)
              }}
            >
              {colored
                .filter(([text]) => text)
                .flatMap(([text, color, dim], j) => [
                  ...(j > 0 ? [' '] : []),
                  <Text key={`piece-${row.rec.task}-${j}`} color={color || undefined} dimColor={dim}>
                    {text}
                  </Text>,
                ])}
            </Button>
          )
        })}
        {rows.length > shown.length && (
          <Text dimColor>{`+${rows.length - shown.length} more · /delegate`}</Text>
        )}
      </Box>
    )
  })
}
