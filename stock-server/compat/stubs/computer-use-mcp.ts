/**
 * Replacement for the private `@ant/computer-use-mcp` package.
 *
 * Computer-use tools are platform/entitlement gated. These stubs keep module
 * linking working and report "no tools / no server" if a code path is reached.
 */
export const DEFAULT_GRANT_FLAGS: Record<string, boolean> = {}

export const API_RESIZE_PARAMS = { maxWidth: 1280, maxHeight: 800 }

export function targetImageSize(
  width: number,
  height: number,
  _params?: unknown,
): { width: number; height: number } {
  return { width, height }
}

export function bindSessionContext(_context?: unknown): Record<string, unknown> {
  return {}
}

export function buildComputerUseTools(..._args: unknown[]): unknown[] {
  return []
}

export function createComputerUseMcpServer(
  ..._args: unknown[]
): { connect: () => Promise<void>; close: () => Promise<void> } {
  return {
    connect: async () => {},
    close: async () => {},
  }
}
