/**
 * Replacement for the private `image-processor-napi` package.
 *
 * It is a sharp-compatible native wrapper. We expose the public `sharp`
 * package in its place, which covers the resize/convert call sites, and
 * report the native clipboard helpers as unavailable so callers fall back.
 */
import sharpDefault from 'sharp'

export const sharp = sharpDefault
export default { sharp: sharpDefault }

export function getNativeModule(): Record<string, unknown> | null {
  return null
}
