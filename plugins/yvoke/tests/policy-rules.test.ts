import { test, expect, describe } from 'claude-code/testing'
import { isOwnServerCall } from '../src/policy'

// tool-policy.md: the pure rule behind the gate's pass for the mod's own calls to its Yvoke server. The gate
// is tested through the engine in policy.test.ts.

const PREFIX = 'mcp__plugin_yvoke_yvoke__'
const TOOL = `${PREFIX}list_areas`

describe('isOwnServerCall', () => {
  test("a call this plugin made to the Yvoke server's tool", () => {
    expect(isOwnServerCall({ plugin: 'yvoke' }, TOOL, PREFIX)).toBe(true)
  })

  test("the model's call (origin engine) is not the mod's own", () => {
    expect(isOwnServerCall({ plugin: 'engine' }, TOOL, PREFIX)).toBe(false)
  })

  test("another plugin's call to the Yvoke server is not the mod's own", () => {
    expect(isOwnServerCall({ plugin: 'other' }, TOOL, PREFIX)).toBe(false)
    expect(isOwnServerCall({ plugin: 'yvoke2' }, TOOL, PREFIX)).toBe(false)
  })

  test('an origin the engine did not give is not the mod', () => {
    expect(isOwnServerCall(undefined, TOOL, PREFIX)).toBe(false)
  })

  test("the mod's call to any other tool stays under the policy", () => {
    expect(isOwnServerCall({ plugin: 'yvoke' }, 'Bash', PREFIX)).toBe(false)
    expect(isOwnServerCall({ plugin: 'yvoke' }, 'mcp__github__create_issue', PREFIX)).toBe(false)
    expect(isOwnServerCall({ plugin: 'yvoke' }, 'mcp__plugin_yvoke_yvoke2__list_areas', PREFIX)).toBe(false)
  })

  test('the server not connected: nothing is its tool', () => {
    expect(isOwnServerCall({ plugin: 'yvoke' }, TOOL, null)).toBe(false)
  })
})
