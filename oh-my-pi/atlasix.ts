// atlasix freshness hook for oh-my-pi — https://github.com/acidsugarx/atlasix
// Install: atlasix plugin oh-my-pi   (copies into ~/.omp/agent/hooks/pre/)
// Needs the `atlasix` CLI on PATH. `atlasix hook refresh` is instant (debounced
// 30s marker) and spawns a detached `atlasix build --no-vectors` when stale.

const EDIT_TOOLS: Record<string, true> = {
  edit: true,
  write: true,
  patch: true,
  edit_file: true,
  write_file: true,
  apply_patch: true,
}

interface ToolEvent {
  toolName?: unknown
  isError?: unknown
}

interface SessionCtx {
  cwd?: unknown
}

function toolName(event: ToolEvent): string | undefined {
  const candidate = event.toolName
  return typeof candidate === "string" ? candidate : undefined
}

export default function atlasixFreshness(pi: {
  exec: (command: string, opts: Record<string, unknown>) => Promise<unknown>
  on: (event: string, handler: (event: never, ctx?: never) => Promise<unknown>) => void
  logger?: { info?: (message: string) => void }
}): void {
  const refresh = (): void => {
    pi.exec("atlasix hook refresh", {})
      .then(() => pi.logger?.info?.("atlasix: index refresh scheduled"))
      .catch(() => {
        /* atlasix not installed or no project here — stay silent */
      })
  }

  pi.on("tool_result", async (rawEvent: unknown) => {
    const event = (rawEvent ?? {}) as ToolEvent
    if (event.isError) return undefined
    const name = toolName(event)
    if (!name || !EDIT_TOOLS[name]) return undefined
    refresh()
    return undefined
  })

  pi.on("session_start", async (_rawEvent: unknown, rawCtx?: unknown) => {
    const ctx = (rawCtx ?? {}) as SessionCtx
    if (!ctx.cwd) return undefined
    pi.exec("atlasix hook refresh", {}).catch(() => undefined)
    return undefined
  })
}
