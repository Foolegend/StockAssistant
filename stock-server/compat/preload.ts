/**
 * Preload for running the source snapshot directly with Bun.
 *
 * Defines the build-time `MACRO` object that the bundler normally injects
 * (see `bun build --define MACRO.VERSION=...` in the upstream build).
 *
 * Real values for the private feedback/changelog channels are unknown; the
 * placeholders keep paths that read them from crashing.
 */

/**
 * Project-local environment mapping.
 *
 * The CLI only understands the upstream variable names (`ANTHROPIC_*`,
 * `STOCK_*`), so a project `.env` that namespaces its keys with
 * `INVEST_` would otherwise be ignored. Translate them here, before any
 * application code runs.
 *
 * Assignment is unconditional: the project `.env` is the source of truth for
 * this checkout, so it must win over stale `ANTHROPIC_*` values inherited
 * from a parent process. Values from `~/.stock/settings.json` are filtered
 * out separately via `STOCK_PROVIDER_MANAGED_BY_HOST`.
 *
 * Shell overrides still work: Bun lets an exported `INVEST_*` variable take
 * precedence over the same key in `.env`.
 */
const PROJECT_ENV_KEYS = [
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_API_KEY',
  'ANTHROPIC_AUTH_TOKEN',
  'ANTHROPIC_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_SMALL_FAST_MODEL',
  'STOCK_AUTO_MODE_SERVER',
  'STOCK_AUTO_COMPACT_WINDOW',
  'STOCK_PROVIDER_MANAGED_BY_HOST',
] as const;

for (const key of PROJECT_ENV_KEYS) {
  const value = process.env[`INVEST_${key}`];
  if (value !== undefined) process.env[key] = value;
}

const VERSION = process.env.STOCK_VERSION ?? '2.1.330';

;(globalThis as any).MACRO = {
  VERSION,
  PACKAGE_URL: 'https://www.npmjs.com/package/@anthropic-ai/claude-code',
  NATIVE_PACKAGE_URL: 'https://www.npmjs.com/package/@anthropic-ai/claude-code',
  FEEDBACK_CHANNEL: 'https://github.com/anthropics/claude-code/issues',
  BUILD_TIME: new Date().toISOString(),
  VERSION_CHANGELOG: '',
  ISSUES_EXPLAINER: 'https://github.com/anthropics/claude-code/issues',
};
