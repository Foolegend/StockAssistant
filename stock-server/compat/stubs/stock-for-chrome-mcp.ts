/**
 * Replacement for the private `@ant/stock-for-chrome-mcp` package.
 * Browser tools are unavailable in the reconstruction.
 */
export const BROWSER_TOOLS: unknown[] = []

export function createStockAssistantForChromeMcpServer(
  ..._args: unknown[]
): { connect: () => Promise<void>; close: () => Promise<void> } {
  return {
    connect: async () => {},
    close: async () => {},
  }
}
