/**
 * Native module replacement for `color-diff-napi`.
 *
 * The snapshot ships a pure-TypeScript port of the Rust module under
 * src/native-ts/color-diff. Re-export it so callers keep the native API.
 */
export {
  ColorDiff,
  ColorFile,
  getSyntaxTheme,
  getNativeModule,
} from '../../src/native-ts/color-diff/index.ts'
export type { SyntaxTheme } from '../../src/native-ts/color-diff/index.ts'
