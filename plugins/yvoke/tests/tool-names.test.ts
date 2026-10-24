import { test, expect, describe } from 'claude-code/testing'
import { DEFAULT_KB_TOOLS, allowedServerTools, builtinTool, isComputeTool, isWebTool, qualifyTool, serverToolPrefix } from '../src/tool-names'

// tool-policy.md: the engine names a server's tools `mcp__<server>__<tool>`, with the server's name
// normalized. The rule was read from Claude Code 2.1.304: every character outside [a-zA-Z0-9_-] becomes `_`;
// for a claude.ai connector, runs of `_` then collapse and a leading or trailing `_` is dropped.

test("the plugin's own server entry", () => {
  expect(serverToolPrefix('plugin:yvoke:yvoke')).toBe('mcp__plugin_yvoke_yvoke__')
})

test('a server configured under its bare name', () => {
  expect(serverToolPrefix('yvoke')).toBe('mcp__yvoke__')
})

test('a claude.ai connector', () => {
  expect(serverToolPrefix('claude.ai yvoke')).toBe('mcp__claude_ai_yvoke__')
})

test('a claude.ai connector: runs of underscores collapse and the ends are trimmed', () => {
  expect(serverToolPrefix('claude.ai  Yvoke (prod)')).toBe('mcp__claude_ai_Yvoke_prod__')
})

test('only a claude.ai connector collapses underscores', () => {
  expect(serverToolPrefix('my  server')).toBe('mcp__my__server__')
})

// tool-policy.md: a playbook names its tools bare (`search_corpus`) or under whatever prefix was current
// when it was written: yvoke-desktop's `mcp__yvoke__`, or the older `mcp__oim__`. Ported from
// yvoke-desktop's `qualifyTool` tests, with the prefix supplied at run time instead of a fixed
// `MCP_TOOL_PREFIX`.

describe('builtinTool', () => {
  test('names the built-in in its own spelling, whatever the case', () => {
    expect(builtinTool('WebSearch')).toBe('WebSearch')
    expect(builtinTool('webfetch')).toBe('WebFetch')
    expect(builtinTool('TOOLSEARCH')).toBe('ToolSearch')
    expect(builtinTool('askuserquestion')).toBe('AskUserQuestion')
  })

  test('sees through a prefix and surrounding spaces', () => {
    expect(builtinTool('mcp__oim__WebSearch')).toBe('WebSearch')
    expect(builtinTool('mcp__custom_server__AskUserQuestion')).toBe('AskUserQuestion')
    expect(builtinTool('  ToolSearch ')).toBe('ToolSearch')
  })

  test('a server tool is not a built-in', () => {
    expect(builtinTool('search_corpus')).toBeUndefined()
    expect(builtinTool('mcp__yvoke__search_corpus')).toBeUndefined()
    expect(builtinTool('WebSearchExtra')).toBeUndefined()
  })
})

for (const prefix of ['mcp__plugin_yvoke_yvoke__', 'mcp__claude_ai_yvoke__']) {
  describe(`qualifyTool with ${prefix}`, () => {
    test('prefixes a bare tool name', () => {
      expect(qualifyTool('search_corpus', prefix)).toBe(`${prefix}search_corpus`)
    })

    test('leaves an already-current name alone', () => {
      expect(qualifyTool(`${prefix}search_corpus`, prefix)).toBe(`${prefix}search_corpus`)
    })

    test("re-namespaces yvoke-desktop's prefix and an older alias instead of double-prefixing", () => {
      expect(qualifyTool('mcp__yvoke__search_corpus', prefix)).toBe(`${prefix}search_corpus`)
      expect(qualifyTool('mcp__oim__search_corpus', prefix)).toBe(`${prefix}search_corpus`)
      expect(qualifyTool('mcp__custom_server__search_corpus', prefix)).toBe(`${prefix}search_corpus`)
    })

    test('produces exactly one prefix for every input spelling', () => {
      for (const input of [
        'search_corpus',
        `${prefix}search_corpus`,
        'mcp__yvoke__search_corpus',
        'mcp__oim__search_corpus',
        'mcp__legacy__search_corpus',
        '  search_corpus  ',
      ]) {
        expect(qualifyTool(input, prefix).match(/mcp__/g)).toHaveLength(1)
        expect(qualifyTool(input, prefix)).toBe(`${prefix}search_corpus`)
      }
    })

    test('never prefixes a built-in, and gives its own spelling', () => {
      expect(qualifyTool('WebSearch', prefix)).toBe('WebSearch')
      expect(qualifyTool('webfetch', prefix)).toBe('WebFetch')
      expect(qualifyTool('ToolSearch', prefix)).toBe('ToolSearch')
      expect(qualifyTool('askuserquestion', prefix)).toBe('AskUserQuestion')
      expect(qualifyTool('mcp__oim__WebSearch', prefix)).toBe('WebSearch')
    })

    test('still prefixes a server tool whose name merely resembles a built-in', () => {
      expect(qualifyTool('web_search_corpus', prefix)).toBe(`${prefix}web_search_corpus`)
      expect(qualifyTool('WebSearchExtra', prefix)).toBe(`${prefix}WebSearchExtra`)
    })

    test('ask_clarifying_question is a server tool, not AskUserQuestion: the gate catches the call', () => {
      expect(qualifyTool('ask_clarifying_question', prefix)).toBe(`${prefix}ask_clarifying_question`)
      expect(qualifyTool('mcp__yvoke__ask_clarifying_question', prefix)).toBe(`${prefix}ask_clarifying_question`)
      expect(builtinTool('ask_clarifying_question')).toBeUndefined()
    })

    test('trims before stripping a prefix, so a padded prefixed name is read like the plain one', () => {
      expect(qualifyTool(' mcp__oim__WebSearch', prefix)).toBe('WebSearch')
      expect(qualifyTool(' mcp__oim__search_corpus ', prefix)).toBe(`${prefix}search_corpus`)
    })
  })
}

// tool-policy.md: the tools a playbook's declaration grants on the Yvoke server. Ported from yvoke-desktop's
// `buildAllowedTools`, `isWebTool` and `isComputeTool` and their cases in tests/policy.test.ts. Web and
// compute names are dropped here; PLAN.md Milestone 2 grants them under their own rules.

describe('isWebTool', () => {
  test('the two web built-ins, in any case, bare or behind a prefix', () => {
    for (const tool of ['WebSearch', 'webfetch', 'mcp__oim__WebSearch', ' WebFetch ']) expect(isWebTool(tool)).toBe(true)
  })

  test('other built-ins and server tools are not web tools', () => {
    for (const tool of ['ToolSearch', 'AskUserQuestion', 'search_corpus', 'web_search_corpus']) expect(isWebTool(tool)).toBe(false)
  })
})

describe('isComputeTool', () => {
  test('execute_code and the retired compute tools, in any case, bare or behind a prefix', () => {
    for (const tool of [
      'execute_code',
      'Execute_Code',
      'calculate',
      'statistics',
      'date_diff',
      'mcp__yvoke__calculate',
      'mcp__compute__execute_code',
      'mcp__compute__anything',
      ' execute_code ',
    ]) {
      expect(isComputeTool(tool)).toBe(true)
    }
  })

  test('server tools and built-ins are not compute tools', () => {
    for (const tool of ['search_corpus', 'execute_code_now', 'mcp__yvoke__search_corpus', 'WebSearch', '']) {
      expect(isComputeTool(tool)).toBe(false)
    }
  })
})

test('the default set is the desktop’s, search_corpus included', () => {
  // The desktop's copy once lost search_corpus, which refused plain chat its main tool.
  expect(DEFAULT_KB_TOOLS).toEqual([
    'query_json_objects',
    'get_section',
    'get_graph_neighbors',
    'verify_citations',
    'list_documents',
    'get_toc',
    'search_graph_entities',
    'get_json_schema',
    'search_corpus',
    'ask_clarifying_question',
  ])
})

for (const prefix of ['mcp__plugin_yvoke_yvoke__', 'mcp__claude_ai_yvoke__']) {
  describe(`allowedServerTools with ${prefix}`, () => {
    test('a declaration grants exactly its tools, each under this session’s prefix', () => {
      expect(allowedServerTools(['search_corpus', 'mcp__yvoke__get_toc', 'mcp__oim__get_section'], prefix)).toEqual([
        `${prefix}search_corpus`,
        `${prefix}get_toc`,
        `${prefix}get_section`,
      ])
    })

    test('declared web and compute tools are dropped, never qualified as server tools', () => {
      const declared = ['search_corpus', 'WebSearch', 'webfetch', 'execute_code', 'calculate', 'statistics', 'date_diff',
        'mcp__yvoke__calculate', 'mcp__compute__execute_code']
      expect(allowedServerTools(declared, prefix)).toEqual([`${prefix}search_corpus`])
    })

    test('the always-granted built-ins are not server tools either', () => {
      expect(allowedServerTools(['ToolSearch', 'AskUserQuestion', 'search_corpus'], prefix)).toEqual([`${prefix}search_corpus`])
    })

    test('a declaration of only web and compute tools grants no server tool', () => {
      expect(allowedServerTools(['WebSearch', 'execute_code'], prefix)).toEqual([])
    })

    test('an empty declaration grants no server tool', () => {
      expect(allowedServerTools([], prefix)).toEqual([])
    })

    test('no declaration grants the default set', () => {
      expect(allowedServerTools(null, prefix)).toEqual(DEFAULT_KB_TOOLS.map((tool) => `${prefix}${tool}`))
    })
  })
}
