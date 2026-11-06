import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const NOW = Date.parse('2026-11-04T16:00:00Z')
const HOME = '/h'
const DIR = `${HOME}/.local/state/claude-compactions`
const DAY = 24 * 60 * 60 * 1000

const seed = (on: On) => {
  const removed: string[] = []
  const commands: string[] = []
  mock.clock(on, { now: NOW })
  mock.env(on, { HOME })
  on('command.register', (_, e) => {
    commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('fs.list', (_, e) => ({
    value:
      e.path === DIR
        ? [
            { name: 'old.md', kind: 'file', size: 1, mtimeMs: NOW - 31 * DAY, isLink: false },
            { name: 'recent.md', kind: 'file', size: 1, mtimeMs: NOW - DAY, isLink: false },
          ]
        : [],
  }))
  on('process.run', (_, e) => {
    removed.push(...e.argv)
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  return { removed, commands }
}

test('pruning removes compaction summaries older than 30 days', async ($, on) => {
  const { removed, commands } = seed(on)
  await $.session.start({ cwd: '/w', surface: 'terminal', isInteractive: true })
  expect(commands).toContain('away')
  expect(removed).toContain(`${DIR}/old.md`)
  expect(removed).not.toContain(`${DIR}/recent.md`)
})
