# StockAssistant 后端

> A locally runnable reconstruction of an upstream terminal AI CLI source snapshot, rebranded and adapted as the StockAssistant analysis engine.

StockAssistant packages the exposed `src/` snapshot together with the build
scaffolding that the published artifact did not include, so the CLI can be
started with Bun for architecture study, experimentation, and defensive
security research.

The local project has been rebranded for StockAssistant; the rename does not
change the provenance of the source snapshot described at the bottom of this file.

---

## Status

| Check | Result |
|---|---|
| `--version` | `2.1.330 (StockAssistant)` |
| `--help` | Full option and subcommand list |
| `mcp list` | Works |
| `-p` streaming query | Works against the configured model endpoint |
| Tool loop | Bash `tool_use` executes and returns its result |
| Interactive REPL | Works in a real PTY (web terminal) |

End-to-end checks run against the endpoint configured in `.env`.

---

## Quick start

```bash
bun install

bun run start -- --version      # fast path
bun run start -- --help
bun run start -- -p "你好"       # non-interactive
bun run start                   # interactive REPL
```

The same commands work on Windows. To run the web terminal backend instead of
the CLI, use `bun run serve` (or `.\start_server.ps1`).

### Web terminal backend

[terminal/server.ts](terminal/server.ts) exposes the interactive CLI as
server-side PTY sessions:

- `ws://127.0.0.1:8015/terminal/ws?session=<id>` — attach to a session; one
  session = one CLI process, and disconnecting does not kill it
- `GET/POST/DELETE /sessions` — list, create, delete sessions
- `GET /sessions/{id}/output` and `GET /sessions/export` — export output
- `GET /sessions/{id}/transcript` — structured transcript of the session
  (turns, thinking, tool calls, answers), parsed from the CLI's own JSONL file
- `GET /sessions/{id}/transcript/stream` — the same transcript as an SSE
  stream, pushed whenever the CLI writes to its transcript
- `GET /health` — health check
- Sessions (metadata + bounded output buffer) are persisted in SQLite
  (`data/terminal-sessions.db`); on re-attach the CLI resumes via `--resume`
  when its saved session file exists
- Set `INVEST_TERMINAL_DB` to override the SQLite path
- `.env`, `.stock` MCP config, hooks and the agent/skill/command system prompt
  are injected by [terminal/wiring.ts](terminal/wiring.ts)

The Angular UI embeds this terminal with xterm.js and lists sessions in the left
sidebar (see the repository README). The main view renders the structured
transcript; the raw terminal stays available behind a toggle.

`start` runs:

```bash
bun run --preload ./compat/preload.ts ./src/entrypoints/cli.tsx
```

### Model configuration

Model provider settings live in the project-local `.env`, which Bun loads
automatically from the repository root. Copy the template once and fill in
your credentials:

```bash
cp .env.example .env
```

Keys are namespaced with `INVEST_` (e.g. `INVEST_ANTHROPIC_BASE_URL`) so they
cannot collide with variables exported by a shell or another StockAssistant
install. [compat/preload.ts](compat/preload.ts) translates them to the
upstream `ANTHROPIC_*` / `STOCK_*` names before the CLI starts, and does
so unconditionally — the project `.env` therefore wins over stale
`ANTHROPIC_*` values inherited from a parent process.

`.env` is gitignored; only `.env.example` is committed, so credentials are
never stored in this repository. The template sets
`INVEST_STOCK_PROVIDER_MANAGED_BY_HOST=1`, which makes the CLI ignore
the provider-routing `env` block in `~/.stock/settings.json` — this project
is self-contained and does not depend on your global StockAssistant
configuration. Remove that line if you want the global settings to apply
again.

Notes:

- Bun does not override `INVEST_*` variables already exported in your shell;
  export one to override a single value for a run.

See **Known limitations** below for remaining gaps.

---

## Repository layout

```text
src/                 StockAssistant source snapshot (~1,900 files, 512k+ lines)
compat/              Runtime replacements for build-time and private modules
lib/                 .stock extension scanner (catalog / frontmatter)
terminal/            PTY + WebSocket session server
../.stock/           Agents, skills, commands, hooks and MCP configs (repo root)
package.json         Dependencies and start scripts
tsconfig.json        Path aliases and JSX configuration
```

### `src/` at a glance

```text
src/
├── main.tsx           CLI parser and React/Ink renderer bootstrap
├── commands.ts        Slash command registry
├── tools.ts           Agent tool registry
├── QueryEngine.ts     LLM query engine (streaming, tools, retries)
├── commands/          Slash command implementations (~50)
├── tools/             Agent tool implementations (~40)
├── components/        Ink UI components (~140)
├── services/          API, MCP, OAuth, LSP, analytics, compaction
├── bridge/            IDE and remote-control bridge
├── coordinator/       Multi-agent coordination
├── plugins/           Plugin system
├── skills/            Skill system
├── entrypoints/       Initialization and CLI entrypoints
├── ink/               Ink renderer wrapper
└── utils/             Shared utilities
```

---

## How the reconstruction works

The published npm artifact was bundled, so the snapshot was missing the
project file, dependencies, some refactored-out modules, and the build-time
constants. Four mechanisms close that gap:

1. **Runtime feature flags.** Bun only provides `bun:bundle` inside
   `bun build`, so 196 imports were rewritten to
   [compat/feature.ts](compat/feature.ts). Enable flags with
   `CC_FEATURES=KAIROS,VOICE_MODE` or `CC_FEATURE_KAIROS=1`.
2. **Build-time macros.** [compat/preload.ts](compat/preload.ts) defines the
   `MACRO` object (`VERSION`, `PACKAGE_URL`, etc.) that `bun build --define`
   normally injects.
3. **Missing modules.** 120 missing runtime modules and 33 text resources are
   present as clearly marked placeholders.
4. **Private and native packages.** `color-diff-napi` uses the snapshot's own
   TypeScript port; image processing falls back to `sharp`; audio, computer-use,
   browser, deep-link, and macOS modifier helpers are stubs.

Two small source patches are required: the Commander option `-d2e` is not a
valid short flag in current Commander, and `bootstrap/state.ts` was missing the
`isReplBridgeActive()` export that other modules import.

---

## Tech stack

| Category | Technology |
|---|---|
| Runtime | Bun |
| Language | TypeScript |
| Terminal UI | React + Ink |
| CLI parsing | Commander.js (extra-typings) |
| Schema validation | Zod v4 |
| Protocols | MCP SDK, LSP |
| API | Anthropic SDK |
| Telemetry | OpenTelemetry + gRPC |
| Feature flags | GrowthBook |

Notable design points: parallel startup prefetch (MDM, keychain, settings),
lazy loading of heavy subsystems, an agent/tool registry with per-tool
permission models, and multi-agent swarms coordinated through `coordinator/`.

---

## Known limitations

- The interactive Ink UI runs in a real PTY (verified through the web terminal).
- Modules absent from the snapshot are universal Proxy placeholders. Disabled
  feature flags normally keep execution out of those branches; enabling a flag
  that needs one of them requires a real implementation.
- Bundled skill documentation (stock-api, verify) is placeholder text.
- Private/native integrations (voice, Computer Use, browser tools, deep links,
  macOS modifiers) are unavailable.
- The dependency list was pruned to runtime-referenced packages. Type-only
  packages (`type-fest`, `vscode-languageserver-*`) were removed, so `tsc` and
  editors will report unresolved type modules even though runtime is unaffected.

---

## Provenance and disclaimer

This repository contains a mirrored `src/` snapshot of an upstream terminal AI
CLI that became publicly accessible on **2026-03-31** through a source map
exposed in its npm distribution. It is maintained for educational, defensive
security, and supply-chain analysis purposes.

- The original upstream source remains the property of **Anthropic**.
- This repository is **not affiliated with, endorsed by, or maintained by
  Anthropic**.
- The `StockAssistant` name refers to this local reconstruction and application,
  not to an Anthropic product.
