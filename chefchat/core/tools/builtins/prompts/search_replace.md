Use `search_replace` to make targeted changes to files using SEARCH/REPLACE blocks. This tool finds exact text matches and replaces them.

Arguments:
- `file_path`: The path to the file to modify
- `content`: The SEARCH/REPLACE blocks defining the changes

The content format is:

```
<<<<<<< SEARCH
[exact text to find in the file]
=======
[exact text to replace it with]
>>>>>>> REPLACE
```

You can include multiple SEARCH/REPLACE blocks to make multiple changes to the same file:

```
<<<<<<< SEARCH
def old_function():
    return "old value"
=======
def new_function():
    return "new value"
>>>>>>> REPLACE

<<<<<<< SEARCH
import os
=======
import os
import sys
>>>>>>> REPLACE
```

IMPORTANT:

- The SEARCH text must match EXACTLY (including whitespace, indentation, and line endings)
- The SEARCH text must appear exactly once in the file - if it appears multiple times, the tool will error
- Use at least 5 equals signs (=====) between SEARCH and REPLACE sections
- The tool will provide detailed error messages showing context if search text is not found
- Each search/replace block is applied in order, so later blocks see the results of earlier ones
- Be careful with escape sequences in string literals - use \n not \\n for newlines in code
