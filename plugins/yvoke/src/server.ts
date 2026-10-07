// The one place the mod talks to the Yvoke server (P1-03). A feature asks for one tool call and gets the
// answer's text, or an error that starts with `Yvoke Backend: ` and is ready to show. No cache, no retry:
// a server that is down gives a clear error (U11). Nothing here logs, so no token or answer reaches
// `$.ui.log`.
//
// The engine refuses a hooks module that passes `$` into a function from another file, so callers hand
// in three closures written where `$` lives:
//
//   const io: ServerIo = {
//     connect: (server) => $.mcp.connect(server),
//     call: (server, tool, args) => $.mcp.call(server, tool, args),
//     sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
//   }
import type { McpConnectResult, McpToolResult } from 'claude-code'

/** The server's key in the plugin's own `.mcp.json` (D-03, D-05). `connect` turns it into the call name. */
export const SERVER = 'yvoke'

/**
 * How long a call may take. Below a hook's 10-second budget, which a `$.clock` wait counts against: a
 * hook that outruns it is skipped without a word.
 */
export const TIMEOUT_MS = 8_000

export type ServerIo = {
  connect: (server: string) => Promise<McpConnectResult>
  call: (server: string, tool: string, args: Record<string, unknown>) => Promise<McpToolResult>
  sleep: (ms: number, signal: AbortSignal) => Promise<void>
}

export type ServerAnswer = { ok: true; text: string } | { ok: false; error: string }

const ORIGIN = 'Yvoke Backend:'
const SIGN_IN_HINT = 'Run /mcp and sign in to yvoke.'
// yvoke-web's tools report their own failures in the body, without the error flag
// (`McpToolUtils.toolError`), as yvoke-desktop's `McpPrompts.callGetSection` found.
const ERROR_BODY = /^\s*(ERROR|Error):/
const TIMED_OUT: unique symbol = Symbol('timed out')

export const callYvoke = async (
  io: ServerIo,
  tool: string,
  args: Record<string, unknown> = {},
  options: { timeoutMs?: number } = {},
): Promise<ServerAnswer> => {
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS
  const timer = new AbortController()
  try {
    const answer = await Promise.race([
      ask(io, tool, args),
      // Ending the timer rejects its wait; that must neither end the race nor go unhandled.
      io.sleep(timeoutMs, timer.signal).then(
        (): typeof TIMED_OUT => TIMED_OUT,
        () => new Promise<never>(() => {}),
      ),
    ])
    if (answer === TIMED_OUT) return failure(`the server did not answer ${tool} within ${timeoutMs / 1000} seconds.`, tool)
    return answer
  } catch (error) {
    return failure(messageOf(error), tool)
  } finally {
    timer.abort()
  }
}

const ask = async (io: ServerIo, tool: string, args: Record<string, unknown>): Promise<ServerAnswer> => {
  const connected = await io.connect(SERVER)
  if (!connected.isConnected) {
    const hint = connected.reason === 'auth' ? ` ${SIGN_IN_HINT}` : ''
    return failure(`${connected.message}${hint}`, tool)
  }
  const result = await io.call(connected.server, tool, args)
  const text = readText(result)
  if (result.isError || ERROR_BODY.test(text)) return failure(text, tool)
  return { ok: true, text }
}

const readText = (result: McpToolResult): string =>
  result.content
    .filter((block) => block.type === 'text')
    .map((block) => unwrapJsonString(block.text ?? ''))
    .filter(Boolean)
    .join('\n\n')

// Some tools answer with a JSON-encoded string; the desktop unwraps it the same way.
const unwrapJsonString = (text: string): string => {
  const trimmed = text.trim()
  if (!(trimmed.startsWith('"') && trimmed.endsWith('"'))) return text
  try {
    const parsed: unknown = JSON.parse(trimmed)
    return typeof parsed === 'string' ? parsed : text
  } catch {
    return text
  }
}

const messageOf = (error: unknown): string => {
  if (error instanceof Error) return error.message
  return typeof error === 'string' ? error : ''
}

const failure = (message: string, tool: string): ServerAnswer => {
  let text = message.trim()
  while (text.startsWith(ORIGIN)) text = text.slice(ORIGIN.length).trim()
  return { ok: false, error: `${ORIGIN} ${text || `the ${tool} call failed.`}` }
}
