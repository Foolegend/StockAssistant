// Content for the stock-api bundled skill.
// Each .md file is inlined as a string at build time via Bun's text loader.

import csharpStockAssistantApi from './stock-api/csharp/stock-api.md'
import curlExamples from './stock-api/curl/examples.md'
import goStockAssistantApi from './stock-api/go/stock-api.md'
import javaStockAssistantApi from './stock-api/java/stock-api.md'
import phpStockAssistantApi from './stock-api/php/stock-api.md'
import pythonAgentSdkPatterns from './stock-api/python/agent-sdk/patterns.md'
import pythonAgentSdkReadme from './stock-api/python/agent-sdk/README.md'
import pythonStockAssistantApiBatches from './stock-api/python/stock-api/batches.md'
import pythonStockAssistantApiFilesApi from './stock-api/python/stock-api/files-api.md'
import pythonStockAssistantApiReadme from './stock-api/python/stock-api/README.md'
import pythonStockAssistantApiStreaming from './stock-api/python/stock-api/streaming.md'
import pythonStockAssistantApiToolUse from './stock-api/python/stock-api/tool-use.md'
import rubyStockAssistantApi from './stock-api/ruby/stock-api.md'
import skillPrompt from './stock-api/SKILL.md'
import sharedErrorCodes from './stock-api/shared/error-codes.md'
import sharedLiveSources from './stock-api/shared/live-sources.md'
import sharedModels from './stock-api/shared/models.md'
import sharedPromptCaching from './stock-api/shared/prompt-caching.md'
import sharedToolUseConcepts from './stock-api/shared/tool-use-concepts.md'
import typescriptAgentSdkPatterns from './stock-api/typescript/agent-sdk/patterns.md'
import typescriptAgentSdkReadme from './stock-api/typescript/agent-sdk/README.md'
import typescriptStockAssistantApiBatches from './stock-api/typescript/stock-api/batches.md'
import typescriptStockAssistantApiFilesApi from './stock-api/typescript/stock-api/files-api.md'
import typescriptStockAssistantApiReadme from './stock-api/typescript/stock-api/README.md'
import typescriptStockAssistantApiStreaming from './stock-api/typescript/stock-api/streaming.md'
import typescriptStockAssistantApiToolUse from './stock-api/typescript/stock-api/tool-use.md'

// @[MODEL LAUNCH]: Update the model IDs/names below. These are substituted into {{VAR}}
// placeholders in the .md files at runtime before the skill prompt is sent.
// After updating these constants, manually update the two files that still hardcode models:
//   - stock-api/SKILL.md (Current Models pricing table)
//   - stock-api/shared/models.md (full model catalog with legacy versions and alias mappings)
export const SKILL_MODEL_VARS = {
  OPUS_ID: 'claude-opus-4-6',
  OPUS_NAME: 'StockAssistant Opus 4.6',
  SONNET_ID: 'claude-sonnet-4-6',
  SONNET_NAME: 'StockAssistant Sonnet 4.6',
  HAIKU_ID: 'claude-haiku-4-5',
  HAIKU_NAME: 'StockAssistant Haiku 4.5',
  // Previous Sonnet ID — used in "do not append date suffixes" example in SKILL.md.
  PREV_SONNET_ID: 'claude-sonnet-4-5',
} satisfies Record<string, string>

export const SKILL_PROMPT: string = skillPrompt

export const SKILL_FILES: Record<string, string> = {
  'csharp/stock-api.md': csharpStockAssistantApi,
  'curl/examples.md': curlExamples,
  'go/stock-api.md': goStockAssistantApi,
  'java/stock-api.md': javaStockAssistantApi,
  'php/stock-api.md': phpStockAssistantApi,
  'python/agent-sdk/README.md': pythonAgentSdkReadme,
  'python/agent-sdk/patterns.md': pythonAgentSdkPatterns,
  'python/stock-api/README.md': pythonStockAssistantApiReadme,
  'python/stock-api/batches.md': pythonStockAssistantApiBatches,
  'python/stock-api/files-api.md': pythonStockAssistantApiFilesApi,
  'python/stock-api/streaming.md': pythonStockAssistantApiStreaming,
  'python/stock-api/tool-use.md': pythonStockAssistantApiToolUse,
  'ruby/stock-api.md': rubyStockAssistantApi,
  'shared/error-codes.md': sharedErrorCodes,
  'shared/live-sources.md': sharedLiveSources,
  'shared/models.md': sharedModels,
  'shared/prompt-caching.md': sharedPromptCaching,
  'shared/tool-use-concepts.md': sharedToolUseConcepts,
  'typescript/agent-sdk/README.md': typescriptAgentSdkReadme,
  'typescript/agent-sdk/patterns.md': typescriptAgentSdkPatterns,
  'typescript/stock-api/README.md': typescriptStockAssistantApiReadme,
  'typescript/stock-api/batches.md': typescriptStockAssistantApiBatches,
  'typescript/stock-api/files-api.md': typescriptStockAssistantApiFilesApi,
  'typescript/stock-api/streaming.md': typescriptStockAssistantApiStreaming,
  'typescript/stock-api/tool-use.md': typescriptStockAssistantApiToolUse,
}
