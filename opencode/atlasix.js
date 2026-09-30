// atlasix plugin for OpenCode — https://github.com/acidsugarx/atlasix
// Install: atlasix plugin opencode   (copies this file into ~/.config/opencode/plugins/)
// Requires the `atlasix` CLI on PATH (uv tool install git+https://github.com/acidsugarx/atlasix)
import { tool } from "@opencode-ai/plugin"

const ATLASIX = "atlasix"

async function run(ctx, args) {
  const { $ } = ctx
  try {
    const out = await $`${ATLASIX} ${args}`.quiet()
    return out.stdout || out.stderr || "(no output)"
  } catch (e) {
    return `[atlasix error] ${e.stderr || e.stdout || e}`
  }
}

export const AtlasixPlugin = async (ctx) => {
  let rebuildTimer = undefined


  return {
    // ---- freshness guard: toast + background rebuild when the index goes stale
    "tool.execute.after": async (input, output) => {
      if (!["edit", "write", "patch"].includes(input.tool)) return
      // debounce: at most one background rebuild per 30s of editing
      if (rebuildTimer) clearTimeout(rebuildTimer)
      rebuildTimer = setTimeout(async () => {
        rebuildTimer = undefined
        try {
          await $`${ATLASIX} build --no-vectors --json`.quiet()
        } catch {
          // atlasix not installed / no index here — stay silent
        }
      }, 30_000)
    },

    // ---- custom tools: the atlasix core as first-class opencode tools
    tool: {
      atlasix_orient: tool({
        description:
          "Repo orientation via atlasix: entity kinds, unresolved refs, duplicate clusters. Call before changing code in an unfamiliar repo.",
        args: { symbol: tool.schema.string().optional() },
        async execute(args, context) {
          if (args.symbol) {
            return run({ $ }, `who-uses ${args.symbol}`)
          }
          return run({ $ }, `profile`)
        },
      }),

      atlasix_who_uses: tool({
        description:
          "Reverse references: everything that references the given name-or-path (extends/includes/needs/uses_var), 2 hops. Use to gauge impact before editing.",
        args: { target: tool.schema.string() },
        async execute(args, context) {
          return run({ $ }, `who-uses ${args.target}`)
        },
      }),

      atlasix_search: tool({
        description:
          "Hybrid BM25+vector semantic search over repo entities and text units (RU/EN). Returns path:line citations.",
        args: { text: tool.schema.string(), top: tool.schema.number().optional() },
        async execute(args, context) {
          return run({ $ }, `search "${args.text}" --top ${args.top ?? 5}`)
        },
      }),

      atlasix_duplicates: tool({
        description: "Copy-paste clusters of text units (e.g. identical CI job scripts).",
        args: {},
        async execute(args, context) {
          return run({ $ }, `duplicates`)
        },
      }),

      atlasix_lint: tool({
        description:
          "Run declarative lint rules over the index. Errors mean broken references (e.g. extends to a non-included block).",
        args: {},
        async execute(args, context) {
          return run({ $ }, `lint`)
        },
      }),
    },
  }
}
