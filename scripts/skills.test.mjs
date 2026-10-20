// Tests for the skill stub generator (P1-04): node --test scripts/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  readPlaybook,
  readArea,
  selectPlaybooks,
  basePromptArgs,
  stubDescription,
  renderStub,
  planSkills,
  DESCRIPTION_LIMIT,
} from './skills.mjs'

const served = JSON.parse(readFileSync(new URL('./fixtures/catalogue.json', import.meta.url), 'utf8'))
const servedPlaybook = (name) => served.playbooks.find((p) => p.name === name)
const oim = readArea(served.areas[0])

// --- Reading the catalogue (ported from yvoke-desktop's promptMapping.test.ts) ---------------------

// The desktop's regression was reading `meta` where MCP put `_meta`. Here the fields sit flat on each
// list_playbooks entry; this pins where each one is read from, against a live answer.
test('reads every field from where list_playbooks puts it', () => {
  assert.deepEqual(readPlaybook(servedPlaybook('oim-db-history')), {
    name: 'oim-db-history',
    title: 'OIM Database History',
    description: servedPlaybook('oim-db-history').description,
    targetAgent: 'specialist',
    prototype: false,
    area: 'OIM',
    systemPrompt: 'oim-structured',
  })
  assert.equal(readPlaybook(servedPlaybook('oim-full-browsing')).prototype, true)
})

test('ignores fields of the wrong type rather than passing them on', () => {
  const p = readPlaybook({ name: 'x', title: 7, description: ['a'], prototype: 'true', targetAgent: 7, area: 1, systemPrompt: {} })
  assert.deepEqual(p, { name: 'x', title: 'x', description: '', targetAgent: null, prototype: false, area: null, systemPrompt: null })
})

test('a missing, blank or padded area or system prompt is read as null or trimmed, never ""', () => {
  assert.equal(readPlaybook({ name: 'a' }).area, null)
  assert.equal(readPlaybook({ name: 'a', area: '  ' }).area, null)
  assert.equal(readPlaybook({ name: 'a', systemPrompt: '' }).systemPrompt, null)
  assert.equal(readPlaybook({ name: 'a', systemPrompt: '  oim-structured ' }).systemPrompt, 'oim-structured')
})

test('reads an area: its default system prompt and its chat prompts', () => {
  assert.deepEqual(oim, { name: 'OIM', defaultSystemPrompt: null, systemPrompts: ['default-chat', 'oim-browsing', 'oim-field-web', 'oim-structured'] })
  assert.deepEqual(readArea({ name: 'X', defaultSystemPrompt: 'p', systemPrompts: 'p' }), { name: 'X', defaultSystemPrompt: 'p', systemPrompts: [] })
})

// --- Which playbooks get a stub (ported from yvoke-desktop's playbooks.test.ts) --------------------

const pb = (name, targetAgent = 'specialist', prototype = false, area = 'OIM') =>
  ({ name, title: name, description: '', targetAgent, prototype, area, systemPrompt: null })

test('keeps specialist and unlabelled playbooks', () => {
  const kept = selectPlaybooks([pb('oim-full'), pb('plain', null)]).map((p) => p.name)
  assert.deepEqual(kept, ['oim-full', 'plain'])
})

test('leaves out orchestrator and reviewer playbooks, even though the server already does', () => {
  const kept = selectPlaybooks([pb('oim-orchestrator', 'orchestrator'), pb('oim-orchestrator-reviewer', 'reviewer'), pb('oim-full')])
  assert.deepEqual(kept.map((p) => p.name), ['oim-full'])
})

test('leaves out prototype playbooks', () => {
  const kept = selectPlaybooks(served.playbooks.map(readPlaybook)).map((p) => p.name)
  assert.deepEqual(kept, ['oim-db-history', 'oim-full'])
})

test('keeps every area: nothing assumes a single area (D-15)', () => {
  const kept = selectPlaybooks([pb('ping-full', 'specialist', false, 'PingID'), pb('oim-full')]).map((p) => p.name)
  assert.deepEqual(kept, ['oim-full', 'ping-full'])
})

// --- Which base instructions the stub names (P1-07's rule) ----------------------------------------

const ping = { name: 'PingID', defaultSystemPrompt: 'ping-chat', systemPrompts: ['ping-chat'] }
const bare = { name: 'Bare', defaultSystemPrompt: null, systemPrompts: [] }

test('names the playbook\'s own system prompt when its area lists it', () => {
  assert.deepEqual(basePromptArgs({ ...pb('a'), systemPrompt: 'oim-structured' }, [oim]), { name: 'oim-structured' })
})

test('names its own prompt when only another area lists it, as findChatPrompt looks globally', () => {
  assert.deepEqual(basePromptArgs({ ...pb('a', 'specialist', false, 'Bare'), systemPrompt: 'ping-chat' }, [bare, ping]), { name: 'ping-chat' })
})

test('falls back to the area when its own prompt is no chat prompt anywhere', () => {
  assert.deepEqual(basePromptArgs({ ...pb('a', 'specialist', false, 'PingID'), systemPrompt: 'gone' }, [ping]), { area: 'PingID' })
})

test('passes the area when the playbook names no prompt and the area has a default', () => {
  assert.deepEqual(basePromptArgs(pb('a', 'specialist', false, 'PingID'), [ping]), { area: 'PingID' })
})

test('passes nothing (the active default) when neither the playbook nor its area names a prompt', () => {
  assert.deepEqual(basePromptArgs(pb('a'), [oim]), {})
  assert.deepEqual(basePromptArgs(pb('a', 'specialist', false, null), [oim]), {})
  assert.deepEqual(basePromptArgs(pb('a', 'specialist', false, 'Unknown'), [oim]), {})
})

// --- The stub's text ------------------------------------------------------------------------------

test('renders the stub for oim-db-history exactly', () => {
  const p = readPlaybook(servedPlaybook('oim-db-history'))
  const expected = [
    '---',
    'name: oim-db-history',
    `description: ${JSON.stringify(`Yvoke playbook (OIM): ${p.description}`)}`,
    'disable-model-invocation: true',
    '---',
    '',
    '<!-- Generated by scripts/generate-skills.mjs from the Yvoke server. Do not edit; rerun the script. -->',
    '',
    '# OIM Database History',
    '',
    'This is the Yvoke playbook `oim-db-history` in the area OIM. Its instructions are kept on the Yvoke server, not in this file. Before you answer anything:',
    '',
    '1. Call the Yvoke connector\'s `get_system_prompt` tool with `name` set to `oim-structured`. Its text is your base instructions.',
    '2. Call the Yvoke connector\'s `get_playbook` tool with `name` set to `oim-db-history`, and follow the `text` it returns. Where the two disagree, the playbook wins.',
    '3. If either call fails or returns a line starting with `ERROR:`, tell the user the Yvoke server could not be reached and stop. Do not answer from general knowledge.',
    '',
  ].join('\n')
  assert.equal(renderStub(p, [oim]), expected)
})

test('words the first step for an area default and for the active default', () => {
  const withArea = renderStub(pb('ping-full', 'specialist', false, 'PingID'), [ping])
  assert.match(withArea, /`get_system_prompt` tool with `area` set to `PingID`\. Its text is/)
  const plain = renderStub(pb('plain', 'specialist', false, null), [])
  assert.match(plain, /`get_system_prompt` tool with no arguments\. Its text is/)
  assert.match(plain, /This is the Yvoke playbook `plain`\. Its instructions/)
  assert.match(plain, /description: "Yvoke playbook: plain"/, 'an empty description falls back to the title')
})

test('cuts a long description at a word boundary to the 1,024-character limit', () => {
  const d = stubDescription(readPlaybook(servedPlaybook('oim-full')))
  assert.equal(DESCRIPTION_LIMIT, 1024)
  assert.ok(d.length <= 1024, `${d.length} characters`)
  assert.ok(d.length > 1000, 'cut close to the limit, not far below it')
  assert.ok(d.endsWith('…'))
  assert.ok(!/\s…$/.test(d), 'no space before the ellipsis')
  const full = `Yvoke playbook (OIM): ${servedPlaybook('oim-full').description}`
  assert.ok(full.startsWith(d.slice(0, -1)), 'a prefix of the served text')
  assert.match(full.slice(d.length - 1), /^[\s,;:—-]/, 'cut where a word ends, trailing punctuation dropped')
})

test('a description that fits is kept whole, with line breaks collapsed', () => {
  assert.equal(stubDescription({ ...pb('a'), description: 'One\n\nTwo  three\tfour.' }), 'Yvoke playbook (OIM): One Two three four.')
})

test('replaces angle brackets, which claude.ai refuses in skill frontmatter', () => {
  const d = stubDescription({ ...pb('a'), description: 'TargetSystem | <Connector> | PersonAutoFullSync' })
  assert.equal(d, 'Yvoke playbook (OIM): TargetSystem | (Connector) | PersonAutoFullSync')
})

// JSON is a subset of YAML 1.2, so a JSON-quoted scalar reads back unchanged in any YAML parser.
test('quotes, backslashes and YAML indicators survive in the frontmatter', () => {
  const description = 'Say "hi": `QER\\ITShop\\*` # not a comment, - [x] {y} & *z'
  const stub = renderStub({ ...pb('a'), description }, [])
  const line = stub.split('\n').find((l) => l.startsWith('description: '))
  assert.equal(JSON.parse(line.slice('description: '.length)), `Yvoke playbook (OIM): ${description}`)
})

// --- The whole set ----------------------------------------------------------------------------------

test('plans one SKILL.md per selected playbook, in name order', () => {
  const files = planSkills(served)
  assert.deepEqual([...files.keys()], ['oim-db-history', 'oim-full'])
  assert.equal(files.get('oim-full'), renderStub(readPlaybook(servedPlaybook('oim-full')), [oim]))
})

test('refuses a playbook name that is not a valid skill name, naming it', () => {
  for (const bad of ['OIM-Full', 'oim full', 'oim--full', '-oim', 'oim_full', 'a'.repeat(65), '../x']) {
    assert.throws(() => planSkills({ playbooks: [{ name: bad }, { name: 'ok' }], areas: [] }), (e) => e.message.includes(JSON.stringify(bad)))
  }
  assert.doesNotThrow(() => planSkills({ playbooks: [{ name: 'a'.repeat(64) }], areas: [] }))
})

test('refuses two playbooks with the same name, naming it', () => {
  assert.throws(() => planSkills({ playbooks: [{ name: 'dup' }, { name: 'dup', area: 'X' }], areas: [] }), /"dup"/)
})

test('refuses a catalogue with no playbook to generate, so a broken server never empties skills/', () => {
  assert.throws(() => planSkills({ playbooks: [], areas: [] }), /no playbooks/)
  assert.throws(() => planSkills({ playbooks: [{ name: 'p', prototype: true }], areas: [] }), /no playbooks/)
  assert.throws(() => planSkills({ playbooks: 'oops', areas: [] }), /not a list/)
})
