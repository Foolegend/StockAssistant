/**
 * Replacement for the private macOS `modifiers-napi` package.
 * Only used when process.platform === 'darwin'; non-mac returns false before
 * reaching the native module. These no-ops keep the import resolvable.
 */
export function prewarm(): void {}

export function isModifierPressed(_modifier: string): boolean {
  return false
}
