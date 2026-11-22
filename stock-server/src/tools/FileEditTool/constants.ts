// In its own file to avoid circular dependencies
export const FILE_EDIT_TOOL_NAME = 'Edit'

// Permission pattern for granting session-level access to the project's .stock/ folder
export const STOCK_FOLDER_PERMISSION_PATTERN = '/.stock/**'

// Permission pattern for granting session-level access to the global ~/.stock/ folder
export const GLOBAL_STOCK_FOLDER_PERMISSION_PATTERN = '~/.stock/**'

export const FILE_UNEXPECTEDLY_MODIFIED_ERROR =
  'File has been unexpectedly modified. Read it again before attempting to write it.'
