/**
 * Runtime replacement for Bun's build-time `feature()` from `bun:bundle`.
 *
 * The original CLI is compiled with `bun build --feature=FLAG`, which replaces
 * `feature('FLAG')` with a boolean literal and dead-code-eliminates inactive
 * branches. We run the source directly, so feature flags become runtime env
 * driven instead.
 *
 * Enabled flags come from either:
 *   - CC_FEATURES="A,B,C"
 *   - CC_FEATURE_A=1 for each individual flag
 *
 * All flags default to false, matching an external build.
 */

const enabled = new Set<string>();

function loadEnabled(): void {
  const list = process.env.CC_FEATURES;
  if (list) {
    for (const raw of list.split(',')) {
      const name = raw.trim().toUpperCase();
      if (name) enabled.add(name);
    }
  }
  for (const [key, value] of Object.entries(process.env)) {
    if (!key.startsWith('CC_FEATURE_') || value === undefined) continue;
    if (value === '' || value === '0' || value.toLowerCase() === 'false') continue;
    enabled.add(key.slice('CC_FEATURE_'.length).toUpperCase());
  }
}

loadEnabled();

export function feature(name: string): boolean {
  if (enabled.size === 0) loadEnabled();
  return enabled.has(name);
}

export function listEnabledFeatures(): string[] {
  return [...enabled].sort();
}
