/**
 * Replacement for the private `url-handler-napi` package.
 * Deep-link waiting is unsupported; callers treat null as "no event".
 */
export async function waitForUrlEvent(_timeoutMs?: number): Promise<string | null> {
  return null
}
