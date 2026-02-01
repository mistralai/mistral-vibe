# Aider RepoMap Technical Implementation: Complete Analysis Prompt

## CONTEXT & PURPOSE

You are analyzing the technical implementation of **Aider's RepoMap system** - a sophisticated graph-based context selection algorithm used in the AI pair programming tool Aider. This system intelligently extracts and ranks the most relevant code symbols from a repository to include in the LLM context window.

The goal: Select 1-2K tokens of the most relevant code from potentially massive repositories, prioritizing symbols based on:
- Dependency relationships between files
- Symbol importance (well-named, frequently used)
- Current chat context (which files the user is editing)
- User mentions and hints

---

## ARCHITECTURE OVERVIEW

Aider's RepoMap pipeline consists of **5 major phases**:

1. **Tag Extraction** - Parse code with Tree-sitter and extract symbol definitions/references
2. **Graph Construction** - Build a dependency graph representing how files reference each other's symbols
3. **Personalization** - Score files based on chat context, mentions, and importance
4. **PageRank Ranking** - Apply PageRank to compute importance of files and definitions
5. **Context Selection** - Binary search to select top-ranked definitions within token budget

Each phase is tightly integrated with caching, error handling, and performance optimization.

---

## PHASE 1: TAG EXTRACTION WITH TREE-SITTER

### 1.1 Core Data Structure

Tags are represented as lightweight namedtuples capturing symbol metadata:

```python
Tag = namedtuple("Tag", "rel_fname fname line name kind".split())
```

**Fields:**
- `rel_fname` (str): Relative file path for display (e.g., "src/utils.py")
- `fname` (str): Absolute file path (e.g., "/repo/src/utils.py")
- `line` (int): Line number where symbol is defined (-1 for references without line info)
- `name` (str): Symbol identifier (e.g., "process_data", "MyClass", "CONSTANT")
- `kind` (str): Either "def" (definition) or "ref" (reference)

### 1.2 Extraction Process

The `get_tags_raw()` method is the core extraction engine:

```
INPUT: fname (file path), rel_fname (relative path)
OUTPUT: Generator of Tag objects

ALGORITHM:
1. Map file extension to language (e.g., ".py" → "python")
2. Load Tree-sitter language parser and language rules
3. Load language-specific query file (e.g., "python-tags.scm")
4. Read source code from disk
5. Parse code into Abstract Syntax Tree (AST) using Tree-sitter
6. Execute Tree-sitter query on AST to extract tagged nodes
7. For each captured node:
   - If tag starts with "name.definition." → kind = "def"
   - If tag starts with "name.reference." → kind = "ref"
   - Otherwise skip (not a symbol definition or reference)
8. Yield Tag namedtuple with extracted metadata
```

**Key Implementation Details:**

- Language detection uses `filename_to_lang()` from grep_ast library
- Tree-sitter parser produces byte-indexed AST nodes
- Query captures are keyed by capture name (e.g., "name.definition.function")
- Node text is decoded from UTF-8 bytes: `node.text.decode("utf-8")`
- Line numbers come from `node.start_point[0]`

**Example Flow for Python File:**

```
File: my_module.py

def process_data(items):     ← Tree-sitter identifies this as function_definition
    return [x * 2 for x in items]

result = process_data([1, 2]) ← Tree-sitter identifies this as call expression
```

Extracted tags:
1. Tag(rel_fname="my_module.py", fname="/path/my_module.py", line=1, name="process_data", kind="def")
2. Tag(rel_fname="my_module.py", fname="/path/my_module.py", line=4, name="process_data", kind="ref")

### 1.3 Caching Layer

Tags are cached to avoid re-parsing unchanged files:

```
CACHE STRATEGY:
- Type: SQLite-backed diskcache.Cache (or fallback to dict on error)
- Location: {repo_root}/.aider.tags.cache.v{VERSION}/
- Key: Absolute file path (fname)
- Value: {"mtime": file_modification_time, "data": [Tag, Tag, ...]}
- Invalidation: On file modification time change

CACHE LOOKUP PROCESS:
1. Get current file modification time
2. Check TAGS_CACHE[fname]
3. If exists and mtime matches → return cached data
4. If miss or mtime differs → extract tags, update cache, return

ERROR HANDLING:
- SQLite operational/database errors → fall back to in-memory dict
- File not found → return empty tag list
- Language not supported → return None
- Parse errors → print warning, return None
```

**Why SQLite caching?**
- Persistent: Survives process restarts
- Scalable: Handles thousands of files
- Transactional: Atomic updates
- Fallback: Dict cache on error prevents crashes

### 1.4 Backfilling References for Definition-Only Languages

Some languages (C++, C) provide definitions via Tree-sitter but not references. Aider backfills references using Pygments lexer tokenization:

```
IF in extracted tags:
  - "ref" exists → STOP (language provides references)
  - "def" exists → CONTINUE (fill in refs)
  - neither exists → STOP

BACKFILL ALGORITHM:
1. Use Pygments to tokenize source code
2. Extract all Name tokens (identifiers)
3. For each token, yield Tag with:
   - kind="ref"
   - line=-1 (no precise line information)
   - name=token text
```

This ensures even C/C++ code gets reference information for graph construction.

---

## PHASE 2: TREE-SITTER QUERY STRUCTURE (tags.scm)

### 2.1 Query Language Overview

Tree-sitter queries use S-expression syntax to match AST patterns. A `.scm` file contains multiple query patterns that extract specific symbol types.

**Basic Syntax:**
- `(node_type ...)` - Match AST node by type
- `field: value` - Match node field
- `@capture_name` - Capture matched node
- `[...]` - Alternatives (OR)
- `(...)+ ` - One or more
- `(...)* ` - Zero or more
- `.` - Sequence marker for adjacency
- `#predicate?` - Conditional assertion

### 2.2 Python Query Example

```scheme
(class_definition
  name: (identifier) @name.definition.class) @definition.class

(function_definition
  name: (identifier) @name.definition.function) @definition.function

(call
  function: [
      (identifier) @name.reference.call
      (attribute
        attribute: (identifier) @name.reference.call)
  ]) @reference.call
```

**Pattern Breakdown:**

**Pattern 1: Class Definitions**
```
(class_definition
  name: (identifier) @name.definition.class) @definition.class
```
- Matches: `class MyClass:` AST nodes
- Captures: The identifier node (class name) with tag "name.definition.class"
- Outer capture: The entire class_definition node with tag "definition.class"

**Pattern 2: Function Definitions**
```
(function_definition
  name: (identifier) @name.definition.function) @definition.function
```
- Matches: `def my_func():` AST nodes
- Captures: The identifier node (function name) with tag "name.definition.function"

**Pattern 3: Function/Method Calls**
```
(call
  function: [
      (identifier) @name.reference.call
      (attribute
        attribute: (identifier) @name.reference.call)
  ]) @reference.call
```
- Matches: `foo()` (direct call) or `obj.method()` (attribute access call)
- Captures: The called identifier with tag "name.reference.call"
- `[...]` provides two alternatives:
  - Direct identifier: `foo()` → captures "foo"
  - Attribute access: `obj.method()` → captures "method"

### 2.3 JavaScript Query Example - Advanced Features

```scheme
(
  (comment)* @doc
  .
  (method_definition
    name: (property_identifier) @name.definition.method) @definition.method
  (#not-eq? @name.definition.method "constructor")
  (#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
  (#select-adjacent! @doc @definition.method)
)
```

**Advanced Features Demonstrated:**

**1. Docstring Capture**
```
(comment)* @doc
.
(method_definition...)
```
- `(comment)*` - Capture zero or more preceding comments
- `.` - Adjacency marker ensuring comments are right before method
- Allows capturing method documentation alongside definition

**2. Predicates**
```
(#not-eq? @name.definition.method "constructor")
```
- `#not-eq?` predicate filters out matches where captured node equals "constructor"
- Other predicates: `#match?`, `#not-match?`, `#eq?`

**3. Directives**
```
(#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
```
- `#strip!` removes regex pattern from captured text
- Cleans comment markers (`*`, `/`, whitespace) from doc capture

**4. Selection Directives**
```
(#select-adjacent! @doc @definition.method)
```
- Associates multiple captures (doc and method definition)
- Used for post-processing relationships

### 2.4 Capture Tag Naming Convention

All capture tags follow a hierarchical naming pattern understood by `get_tags_raw()`:

```
name.definition.{type}     → Symbol definition
  Example: name.definition.function, name.definition.class, name.definition.variable

name.reference.{type}      → Symbol reference/usage
  Example: name.reference.call, name.reference.class

definition.{type}          → Entire definition context (optional)
  Example: definition.function, definition.class

reference.{type}           → Entire reference context (optional)
  Example: reference.call, reference.class
```

**Parsing Logic in get_tags_raw():**

```python
if tag.startswith("name.definition."):
    kind = "def"
    # Extract kind type: "function", "class", etc.
elif tag.startswith("name.reference."):
    kind = "ref"
else:
    continue  # Skip non-name captures
```

Only `name.definition.*` and `name.reference.*` tags are used; outer context tags are ignored.

### 2.5 Common Query Patterns by Language

**Python:**
- Class definitions: `class_definition`
- Function definitions: `function_definition`
- Function calls: `call` with `function` field
- Attribute access: `.` operator

**JavaScript:**
- Class declarations: `class_declaration`, `class` (expression)
- Method definitions: `method_definition`
- Function declarations: `function_declaration`
- Function expressions: `function` (in assignments)
- Arrow functions: `arrow_function`
- Function calls: `call_expression`
- Constructor calls: `new_expression`
- Comments: `comment` nodes (multiple per pattern)

**Go:**
- Type declarations: `type_declaration`
- Function declarations: `function_declaration`
- Method receivers: `receiver` field
- Function calls: `call_expression`

**C++:**
- Class definitions: `class_specifier`
- Function definitions: `function_definition`
- Type declarations: `type_definition`
- References: Not provided by Tree-sitter (requires Pygments backfill)

---

## PHASE 3: DEPENDENCY GRAPH CONSTRUCTION

### 3.1 Data Collection

Before building the graph, the system collects all symbol definitions and references across the repository:

```
COLLECT PHASE:
For each file in {chat_fnames} ∪ {other_fnames}:
  1. Get tags = extract_tags(file)
  2. For each Tag in tags:
     - If kind == "def":
       defines[tag.name].add(file)
       definitions[(file, tag.name)].add(tag)
     - Else (kind == "ref"):
       references[tag.name].append(file)

RESULT STRUCTURES:

defines: Dict[identifier, Set[file]]
  Example: defines["process_data"] = {"utils.py", "helpers.py"}
  Meaning: Two files define a symbol named "process_data"

references: Dict[identifier, List[file]]
  Example: references["process_data"] = ["main.py", "main.py", "workflow.py"]
  Meaning: "main.py" references "process_data" twice, "workflow.py" once
  Note: Duplicates preserved for frequency counting

definitions: Dict[(file, identifier), Set[Tag]]
  Example: definitions[("utils.py", "process_data")] = {Tag(...)}
  Meaning: Exact tag info for specific definition

personalization: Dict[file, float]
  Example: personalization["main.py"] = 5.0
  Meaning: "main.py" has importance score 5.0 (see 3.2)
```

### 3.2 Personalization Scoring

Files are assigned importance scores representing how relevant they are to the current task:

```
PERSONALIZATION ALGORITHM:

base_score = 100 / len(all_files)
  Purpose: Normalize scores by repository size
  Example: 50 files → base_score = 2.0 per file

For each file:
  score = 0.0
  
  # Chat files: currently being edited
  if file in chat_files:
    score += base_score
  
  # Mentioned files: explicitly referenced by user
  if relative_path(file) in mentioned_fnames:
    score = max(score, base_score)  # Avoid double-counting
  
  # Mentioned identifiers: file path matches identifier
  path_parts = path_components(file)
  path_parts.add(basename_with_ext)
  path_parts.add(basename_without_ext)
  
  if path_parts ∩ mentioned_idents ≠ ∅:
    score += base_score
  
  # Store only non-zero scores
  if score > 0:
    personalization[file] = score
```

**Example Scenario:**

```
Repository: 10 files
base_score = 100 / 10 = 10.0

chat_files = ["main.py"]
mentioned_fnames = {"src/utils.py"}
mentioned_idents = {"config", "setup"}

Scoring:

main.py:
  - In chat_files? Yes → +10.0
  - In mentioned_fnames? No
  - Path matches mentioned_idents? No ("main" ≠ "config", "setup")
  - Final: 10.0

src/utils.py:
  - In chat_files? No
  - In mentioned_fnames? Yes ("src/utils.py") → max(0, 10) = 10.0
  - Path matches mentioned_idents? No
  - Final: 10.0

setup.py:
  - In chat_files? No
  - In mentioned_fnames? No
  - Path matches mentioned_idents? Yes ("setup" in basename_without_ext)
  - Final: 10.0

personalization = {
  "main.py": 10.0,
  "src/utils.py": 10.0,
  "setup.py": 10.0,
}
```

### 3.3 Graph Construction

A directed MultiDiGraph is built where:
- **Nodes**: Files in the repository
- **Edges**: References between files for specific symbols
- **Edge weights**: Importance of the reference based on symbol properties

```
GRAPH ALGORITHM:

G = MultiDiGraph()  # Allow multiple edges between same nodes

# Step 1: Add self-edges for definitions without references
For each identifier with only definitions (no references):
  For each file that defines it:
    G.add_edge(file, file, weight=0.1, ident=identifier)
    Purpose: Help defs-only languages, weak self-loops

# Step 2: Build edges for symbols with both defs and refs
For each identifier in (defines.keys() ∩ references.keys()):
  
  definers = files that define this identifier
  weight_multiplier = 1.0
  
  # Adjust multiplier based on symbol properties
  is_snake_case = "_" in identifier AND has letters
  is_kebab_case = "-" in identifier AND has letters
  is_camel_case = has uppercase AND lowercase letters
  
  if identifier in mentioned_idents:
    weight_multiplier *= 10                  # 10x boost
  
  if (snake OR kebab OR camel) AND len(identifier) ≥ 8:
    weight_multiplier *= 10                  # 10x boost for well-named symbols
  
  if identifier.startswith("_"):
    weight_multiplier *= 0.1                 # 10x penalty for private symbols
  
  if len(definers) > 5:
    weight_multiplier *= 0.1                 # 10x penalty for widely-defined symbols
  
  # Count references per referencer
  ref_counts = Counter(references[identifier])
  
  For each (referencer, count) in ref_counts.items():
    For each definer in definers:
      
      final_multiplier = weight_multiplier
      
      if referencer in chat_files:
        final_multiplier *= 50               # 50x boost if referencing file is in chat
      
      # Dampen high-frequency references with square root
      dampened_count = sqrt(count)
      
      edge_weight = final_multiplier * dampened_count
      
      G.add_edge(
        referencer,                          # Source: file doing the referencing
        definer,                             # Target: file defining the symbol
        weight=edge_weight,
        ident=identifier
      )
```

**Weight Examples:**

```
Scenario 1: Simple reference
- identifier = "process_data" (12 chars, camel_case)
- reference location: main.py → utils.py
- reference count: 1 time
- main.py in chat? Yes

weight_multiplier = 1.0
  * 10 (camel_case AND len ≥ 8)
  * 50 (referencer in chat)
  = 500.0

dampened_count = sqrt(1) = 1.0
edge_weight = 500.0 * 1.0 = 500.0

---

Scenario 2: Common private symbol
- identifier = "_internal_helper" (16 chars)
- reference location: utils.py → helpers.py
- reference count: 5 times
- utils.py in chat? No

weight_multiplier = 1.0
  * 0.1 (starts with underscore)
  = 0.1

dampened_count = sqrt(5) ≈ 2.236
edge_weight = 0.1 * 2.236 ≈ 0.224

---

Scenario 3: Widely-used utility
- identifier = "format" (6 chars)
- defined in: [utils.py, helpers.py, common.py, lib.py, tools.py, core.py] (6 files)
- reference location: main.py → helpers.py
- reference count: 3 times
- main.py in chat? No

weight_multiplier = 1.0
  * 0.1 (len(definers) > 5)
  = 0.1

dampened_count = sqrt(3) ≈ 1.732
edge_weight = 0.1 * 1.732 ≈ 0.173
```

**Intuition Behind Weights:**

- **Chat context multiplier (×50)**: References within actively edited files are very important
- **Well-named multiplier (×10)**: Good symbol names → more intentional dependencies
- **Mentioned identifier multiplier (×10)**: User explicitly called out this symbol
- **Private symbol penalty (×0.1)**: Internal implementation details are less relevant
- **Common symbol penalty (×0.1)**: Widely-used utilities are less discriminative
- **Square root dampening**: Prevents super-frequent references from dominating

---

## PHASE 4: PAGERANK ALGORITHM

### 4.1 Overview

PageRank computes the "importance" of each file in the dependency graph. A file is important if:
1. Many important files reference it (incoming edges from high-rank nodes)
2. It's in the personalization set (user context)

```
PAGERANK FORMULA (simplified):

rank(v) = (1 - d) / N + d * Σ(rank(u) * weight(u→v)) / Σ(weight(u→w))
          └─────────────────┘   └────────────────────────────────────┘
          Base score           Incoming rank from predecessors

Where:
- v: node (file) being scored
- d: damping factor (typically 0.85)
- N: number of nodes
- u: predecessor node
- weight(u→v): edge weight from u to v
- Σ(weight(u→w)): total outgoing weight from u
```

### 4.2 NetworkX Implementation

```python
if personalization:
    pers_args = dict(
        personalization=personalization,
        dangling=personalization
    )
else:
    pers_args = dict()

ranked = nx.pagerank(G, weight="weight", **pers_args)
```

**Parameters:**
- `G`: Directed graph with weighted edges
- `weight="weight"`: Use edge weight field for importance calculation
- `personalization`: Dict of node → score for teleportation (biases toward personalized nodes)
- `dangling`: Dict of sink node → score (for nodes with no outgoing edges)

**Personalization Effect:**

When personalization is provided, PageRank biases the random walk:
- Normal iteration: Follow edges with probability proportional to weight
- Teleportation: Jump to personalized node with probability (1 - d)

Result: Highly personalized nodes get higher ranks, and nodes they reference also rank higher.

**Example Iteration (simplified):**

```
Graph:
  main.py → utils.py (weight=500)
  utils.py → helpers.py (weight=100)

personalization = {"main.py": 10.0}

Initial ranks: all = 1/3

Iteration 1:
  rank(main.py) = (1 - 0.85) / 3 + 0.85 * 10.0
                ≈ 8.5

  rank(utils.py) = (1 - 0.85) / 3 + 0.85 * (8.5 * 500 / 500)
                 ≈ 0.05 + 0.85 * 8.5
                 ≈ 7.27

  rank(helpers.py) = (1 - 0.85) / 3 + 0.85 * (7.27 * 100 / 100)
                   ≈ 0.05 + 0.85 * 7.27
                   ≈ 6.24

(Continues iterating until convergence)
```

### 4.3 Distributing Rank to Definitions

After PageRank, file ranks are distributed to individual (file, identifier) pairs:

```
DISTRIBUTION ALGORITHM:

ranked_definitions = Dict[(file, ident), float]

For each source_file in G.nodes:
  
  source_rank = ranked[source_file]  # PageRank score
  
  # Calculate total outgoing weight from this file
  total_out_weight = Σ(weight for all outgoing edges)
  
  For each (source_file, dest_file, edge_data) in outgoing edges:
    
    # Distribute source rank proportionally to each edge
    edge_rank = source_rank * edge_data["weight"] / total_out_weight
    
    ident = edge_data["ident"]
    
    # Accumulate rank for this (dest_file, ident) pair
    ranked_definitions[(dest_file, ident)] += edge_rank

# Sort by rank (descending), then by identifier (tie-breaking)
ranked_definitions_sorted = sorted(
  ranked_definitions.items(),
  reverse=True,
  key=lambda x: (x[1], x[0])
)
```

**Distribution Intuition:**

If file A has rank 1.0 and references two symbols:
- Symbol X with edge weight 10.0
- Symbol Y with edge weight 20.0
- Total weight: 30.0

Distribution:
- X gets: 1.0 * 10.0 / 30.0 ≈ 0.333
- Y gets: 1.0 * 20.0 / 30.0 ≈ 0.667

File ranks "flow through" references proportionally based on edge weights.

### 4.4 Result Structure

```
ranked_definitions = [
  ((file1, ident1), 0.856),    ← Highest ranked (file, symbol) pair
  ((file2, ident2), 0.742),
  ((file1, ident3), 0.689),
  ((file3, ident4), 0.567),
  ...,
]
```

This sorted list is used in Phase 5 for context selection.

---

## PHASE 5: CONTEXT SELECTION WITH BINARY SEARCH

### 5.1 Strategy Overview

After ranking all (file, identifier) pairs, the system needs to select exactly which ones to include in the context window. The constraint: **maximum token count**.

A naive approach would include all top-ranked items, but this might:
- Over-estimate token usage (return too little)
- Under-estimate token usage (return too much)

Solution: **Binary search** to find the optimal cut-off point.

```
GOAL: Find N such that:
  - Select top N (file, ident) pairs
  - Render them as code context
  - Token count is within 15% of max_map_tokens
  - If multiple N satisfy this, choose the one yielding most tokens ≤ max
```

### 5.2 Binary Search Algorithm

```
INPUT:
  ranked_tags: Sorted list of (file, ident) tuples
  max_map_tokens: Target token count (e.g., 1024)
  chat_rel_fnames: Set of files in current chat

OUTPUT:
  best_tree: Rendered context string

ALGORITHM:

num_tags = len(ranked_tags)
lower_bound = 0
upper_bound = num_tags
best_tree = None
best_tree_tokens = 0

# Initial guess: 1/25th of max_map_tokens in file count
middle = min(max_map_tokens // 25, num_tags)

WHILE lower_bound ≤ upper_bound:
  
  # Render context for top 'middle' tags
  tree = to_tree(ranked_tags[:middle], chat_rel_fnames)
  
  # Count tokens in rendered context
  num_tokens = token_count(tree)
  
  # Calculate error percentage
  pct_error = |num_tokens - max_map_tokens| / max_map_tokens
  error_threshold = 0.15  # 15% tolerance
  
  # Decide if this is acceptable
  is_acceptable = (
    (num_tokens ≤ max_map_tokens AND num_tokens > best_tree_tokens)
    OR
    pct_error < error_threshold
  )
  
  IF is_acceptable:
    best_tree = tree
    best_tree_tokens = num_tokens
    
    # Exit early if within 15% tolerance
    IF pct_error < error_threshold:
      BREAK
  
  # Adjust binary search bounds
  IF num_tokens < max_map_tokens:
    lower_bound = middle + 1      # Need more tokens
  ELSE:
    upper_bound = middle - 1      # Too many tokens
  
  # Compute new midpoint
  middle = (lower_bound + upper_bound) // 2

RETURN best_tree
```

**Search Termination Conditions:**

1. **Convergence**: `lower_bound > upper_bound` (search space exhausted)
2. **Good-enough**: Percentage error < 15% (fast exit)
3. **Implicit bound**: Maximum iterations ≈ log₂(num_tags)

### 5.3 Token Counting Strategy

Token counting is expensive (calls the LLM), so Aider uses **adaptive sampling**:

```
FUNCTION token_count(text):

IF len(text) < 200 characters:
  # Small text: accurate count
  RETURN main_model.token_count(text)

ELSE:
  # Large text: sample and extrapolate
  lines = split_by_newline(text)
  num_lines = len(lines)
  
  # Sample approximately 1% of lines
  sample_step = max(num_lines // 100, 1)
  sample_lines = lines[::sample_step]
  sample_text = join(sample_lines)
  
  # Get actual token count of sample
  sample_tokens = main_model.token_count(sample_text)
  
  # Extrapolate to full text
  estimated_tokens = sample_tokens * (len(text) / len(sample_text))
  
  RETURN estimated_tokens
```

**Why Sampling?**
- Full counting: O(N) LLM API calls (expensive)
- Sampling: O(1) LLM API calls (1% of lines)
- Error bound: Usually ±5% for programming code (regular token distribution)

**Sampling Formula:**

```
sample_ratio = sample_tokens / sample_text_length
estimated_full_tokens = sample_ratio * full_text_length
```

Works because token-to-character ratio is fairly consistent in code.

### 5.4 Tree Rendering

The selected (file, ident) pairs are rendered as formatted code with context:

```
FUNCTION to_tree(ranked_tags, chat_rel_fnames):

OUTPUT = ""
current_file = None
lines_of_interest = []

For each tag in sorted(ranked_tags):
  
  tag_file = tag[0]
  
  # Skip files already in chat (user sees them elsewhere)
  IF tag_file in chat_rel_fnames:
    CONTINUE
  
  # Render new file section when filename changes
  IF tag_file ≠ current_file:
    
    # Flush previous file
    IF lines_of_interest is not None:
      OUTPUT += "\n"
      OUTPUT += current_file + ":\n"
      OUTPUT += render_tree_with_context(
        absolute_path, 
        tag_file, 
        lines_of_interest
      )
    ELSE IF current_file exists:
      OUTPUT += "\n" + current_file + "\n"
    
    # Start new file
    IF tag is a Tag:
      lines_of_interest = []
      absolute_path = tag.fname
    current_file = tag_file
  
  # Add line number to context lines
  IF lines_of_interest is not None:
    lines_of_interest.append(tag.line)

# Flush final file
IF lines_of_interest is not None:
  OUTPUT += "\n"
  OUTPUT += current_file + ":\n"
  OUTPUT += render_tree_with_context(...)

# Truncate long lines (e.g., minified JS)
OUTPUT = join([line[:100] for line in split(OUTPUT, "\n")])

RETURN OUTPUT
```

**Output Format Example:**

```
src/utils.py:
  def process_data(items):
      return [x * 2 for x in items]

src/helpers.py:
  class DataProcessor:
      def __init__(self):
          self.cache = {}

core/main.py
```

**Line Rendering Strategy:**

Uses `grep_ast.TreeContext` to render code snippets with surrounding context:

```
FUNCTION render_tree_with_context(fname, rel_fname, lines_of_interest):

# Create TreeContext for AST-aware rendering
context = TreeContext(
  filename=rel_fname,
  code=source_code,
  color=False,              # No ANSI color codes
  line_number=False,        # No line numbers in output
  child_context=False,      # Don't show child scope context
  last_line=False,          # Don't show last line of functions
  margin=0,                 # No extra surrounding lines
  mark_lois=False,          # Don't mark lines of interest
  loi_pad=0,                # No padding around marked lines
  show_top_of_file_parent_scope=False,
)

# Add lines to render
context.lines_of_interest = set(lines_of_interest)
context.add_context()

# Format and return
RETURN context.format()
```

This extracts and formats relevant code sections with minimal boilerplate.

---

## COMPLETE DATA FLOW DIAGRAM

```
┌──────────────────────────────────────────────────────────────────────────┐
│ INPUT PARAMETERS                                                         │
├──────────────────────────────────────────────────────────────────────────┤
│ chat_fnames:        List of files user is currently editing             │
│ other_fnames:       List of other files in repository                   │
│ mentioned_fnames:   User-mentioned file paths                           │
│ mentioned_idents:   User-mentioned identifiers/concepts                 │
│ max_map_tokens:     Token budget (e.g., 1024)                           │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
         ┌─────────────────────────────────────────────────┐
         │ PHASE 1: TAG EXTRACTION                         │
         ├─────────────────────────────────────────────────┤
         │ For each file in repository:                    │
         │ 1. Check mtime in cache                         │
         │ 2. If miss: Parse with Tree-sitter             │
         │ 3. Apply language-specific .scm query           │
         │ 4. Extract definitions and references           │
         │ 5. Cache tags with mtime                        │
         │ 6. Backfill refs for def-only languages         │
         └─────────────────────────────────────────────────┘
                                    │
                                    ▼
         ┌─────────────────────────────────────────────────┐
         │ DATA STRUCTURES BUILT                           │
         ├─────────────────────────────────────────────────┤
         │ defines:      ident → {files}                  │
         │ references:   ident → [files]                  │
         │ definitions:  (file, ident) → {Tag}            │
         └─────────────────────────────────────────────────┘
                                    │
                                    ▼
         ┌─────────────────────────────────────────────────┐
         │ PHASE 2: PERSONALIZATION                        │
         ├─────────────────────────────────────────────────┤
         │ For each file:                                  │
         │ 1. Base score = 100 / num_files                │
         │ 2. +score if in chat_fnames                     │
         │ 3. +score if in mentioned_fnames               │
         │ 4. +score if path matches mentioned_idents      │
         │ 5. Store non-zero scores                        │
         └─────────────────────────────────────────────────┘
                                    │
                                    ▼
         ┌─────────────────────────────────────────────────┐
         │ PHASE 3: GRAPH CONSTRUCTION                     │
         ├─────────────────────────────────────────────────┤
         │ Create MultiDiGraph where:                       │
         │ - Nodes: files                                  │
         │ - Edges: referencer → definer                   │
         │ - Weights: based on symbol properties           │
         │                                                 │
         │ Weight calculation:                             │
         │ 1. Start with multiplier = 1.0                  │
         │ 2. Apply symbol property modifiers              │
         │ 3. Apply chat context modifier (×50)            │
         │ 4. Dampen frequency with sqrt()                │
         │ 5. Final weight = multiplier × sqrt(refs)       │
         └─────────────────────────────────────────────────┘
                                    │
                                    ▼
         ┌─────────────────────────────────────────────────┐
         │ PHASE 4: PAGERANK RANKING                       │
         ├─────────────────────────────────────────────────┤
         │ 1. Run nx.pagerank(G, weight="weight",          │
         │              personalization=personalization)   │
         │ 2. Get ranked[file] for each file               │
         │ 3. Distribute ranks to (file, ident) pairs      │
         │ 4. Sort pairs by rank (descending)              │
         │ 5. Result: ranked_definitions = sorted list     │
         └─────────────────────────────────────────────────┘
                                    │
                                    ▼
         ┌─────────────────────────────────────────────────┐
         │ PHASE 5: BINARY SEARCH CONTEXT SELECTION        │
         ├─────────────────────────────────────────────────┤
         │ 1. Initialize search bounds                     │
         │ 2. WHILE bounds not converged:                  │
         │    a. Select top N ranked definitions           │
         │    b. Render as code context (tree format)      │
         │    c. Count tokens (with sampling)              │
         │    d. Check if ±15% of target                   │
         │    e. Adjust bounds based on token count        │
         │ 3. Return best tree matching token budget       │
         └─────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────┐
                    │ OUTPUT: repo_map string  │
                    │                          │
                    │ Formatted code context   │
                    │ with most relevant       │
                    │ symbols and their        │
                    │ surrounding code         │
                    │ (1-2K tokens)            │
                    └──────────────────────────┘
```

---

## CONFIGURATION & PARAMETERS

### RepoMap Initialization

```python
RepoMap(
    map_tokens=1024,
    root=None,
    main_model=None,
    io=None,
    repo_content_prefix=None,
    verbose=False,
    max_context_window=None,
    map_mul_no_files=8,
    refresh="auto",
)
```

**Parameters:**

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `map_tokens` | 1024 | Target token count for repo map context |
| `root` | cwd | Repository root directory |
| `main_model` | None | LLM instance for token counting |
| `io` | None | I/O handler for reading files |
| `repo_content_prefix` | None | Template prefix for output (e.g., "## Other files:") |
| `verbose` | False | Print debug info to stdout |
| `max_context_window` | None | LLM context window size (for padding calculations) |
| `map_mul_no_files` | 8 | Token multiplier when no chat files (max 8×target) |
| `refresh` | "auto" | Cache refresh strategy |

### Refresh Strategies

| Strategy | Behavior |
|----------|----------|
| `"auto"` | Cache when processing > 1 second, skip when < 1 second |
| `"always"` | Never use cache, always recompute |
| `"manual"` | Cache permanently until explicit force_refresh |
| `"files"` | Cache based on file dependencies only |

### Cache Configuration

```python
TAGS_CACHE_DIR = f".aider.tags.cache.v{CACHE_VERSION}"

# Cache version increments when tag extraction logic changes
# v3: Standard Tree-sitter integration
# v4: Using TSL (Tree-Sitter Language Pack) instead of individual grammars

# Cache persistence:
# - Location: {repo_root}/.aider.tags.cache.v{VERSION}/
# - Type: SQLite (or fallback dict)
# - Scope: Per-repository (not global)
```

---

## PERFORMANCE CHARACTERISTICS

### Time Complexity

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| Parse single file | O(n) | n = file size in bytes |
| Extract tags per file | O(n) | Linear in AST node count |
| Initial repo scan | O(F × n) | F = num files, n = avg file size |
| Graph construction | O(S × R) | S = unique symbols, R = references |
| PageRank | O(E log E) | E = edges, NetworkX implementation |
| Binary search | O(log N) | N = number of ranked definitions |
| Token counting | O(1) | Samples ~1% of lines |
| **Total first run** | **O(F × n)** | Dominated by parsing |
| **Total subsequent** | **O(log N)** | Cache hits, fast re-ranking |

### Space Complexity

| Component | Space | Notes |
|-----------|-------|-------|
| Tag cache (disk) | O(T) | T = total tag count |
| In-memory tags | O(T) | Only loaded as-needed |
| Graph | O(F + E) | F = files, E = dependency edges |
| PageRank scores | O(F) | One score per file |
| Ranked definitions | O(D) | D = unique (file, ident) pairs |

### Real-World Performance

**Repository size: 50 files, 50K LOC**
- Initial scan: 2-3 seconds (full Tree-sitter parsing)
- Cache hit: 100-200ms (skip parsing, use cached tags)
- Graph construction: 50-100ms (small graph)
- PageRank: 10-20ms (NetworkX)
- Binary search: 5-10 queries × 50-100ms (sampling) = 250-500ms

**Total user-perceived time: 300-700ms with cache**

### Optimizations

| Optimization | Mechanism | Impact |
|--------------|-----------|--------|
| Tag caching | SQLite with mtime | 90% faster for unchanged files |
| Token sampling | 1% of lines | 100× faster token counting |
| Early exit | 15% tolerance | Stops early if "good enough" |
| Graph pruning | Only symbols with defs+refs | 50% smaller graph |
| Weight dampening | sqrt(refs) | Prevents frequency domination |
| Initial guess | middle = max_tokens // 25 | Closer to optimal starting point |

---

## ERROR HANDLING & ROBUSTNESS

### SQLite Cache Errors

```
SCENARIO: Cache corruption, disk full, permissions error

FALLBACK STRATEGY:
1. Catch: sqlite3.OperationalError, sqlite3.DatabaseError, OSError
2. Warn user: "Unable to use tags cache, falling back to memory cache"
3. Replace: self.TAGS_CACHE = dict()  # In-memory dict
4. Continue: Functionality preserved, performance degraded

RECOVERY:
On next successful session:
- Try to recreate cache directory
- Test with write operation
- Fall back to dict if still failing
```

### File Access Errors

```
SCENARIO: File deleted between scan and render

HANDLING:
1. Check: Path.is_file() before processing
2. Warn: "Repo-map can't include {fname}: Has it been deleted?"
3. Skip: Continue with next file, don't crash
4. Track: Store in warned_files set to avoid repeated warnings
```

### Parse Errors

```
SCENARIO: Language not supported, malformed code, Tree-sitter failure

HANDLING:
1. Catch: Exception during Tree-sitter parsing
2. Warn: "Skipping file {fname}: {error}"
3. Return: Empty tag list (not an error, just no tags)
4. Continue: Process next file

BACKFILL FALLBACK:
If Tree-sitter provides no references:
- Try Pygments tokenizer as backup
- Extract all Name tokens as references
- Allows C/C++ (no Tree-sitter refs) to work
```

### Recursion Errors

```
SCENARIO: Circular references in graph, massive graph depth

HANDLING:
In get_repo_map():
1. Catch: RecursionError
2. Warn: "Disabling repo map, git repo too large?"
3. Disable: Set self.max_map_tokens = 0
4. Return: None (disable feature safely)

ROOT CAUSE:
- Huge graph with cycles
- Graph too deep for NetworkX default recursion limit
- Graceful degradation rather than crash
```

---

## ADVANCED TOPICS

### Personalization in PageRank

The `personalization` parameter biases PageRank toward specific nodes:

```
MATHEMATICAL INTERPRETATION:

rank(v) = (1 - d) × personalization[v] / Σ(personalization)
        + d × Σ(rank(u) × weight(u→v)) / Σ(weight(u→w))
          ├─ Teleportation toward personalized nodes
          └─ Smooth probability transition

EFFECT:
- Personalized nodes get higher baseline rank
- Nodes referenced BY personalized nodes also rank higher
- Transitive effect: personalization "spreads" through graph

EXAMPLE:
main.py is personalized (in chat).
main.py references utils.py.
utils.py ranks high even if nothing else references it.
```

### Reference Frequency Dampening

The `sqrt(num_refs)` dampening prevents common utilities from dominating:

```
MOTIVATION:
Without dampening:
- Format function called 100 times → rank flow = 100
- Custom function called 2 times → rank flow = 2
- Ratio: 50× difference despite both being useful

With sqrt dampening:
- Format function: sqrt(100) = 10 → rank flow = 10
- Custom function: sqrt(2) ≈ 1.41 → rank flow = 1.41
- Ratio: 7× difference → more balanced

FORMULA: weight = multiplier × sqrt(num_refs)
EFFECT: High-frequency references are discounted logarithmically
```

### Multi-Diraph vs Directed Graph

Why MultiDiGraph (allows multiple edges between same nodes)?

```
SCENARIO: Two unrelated reasons for A → B

A = "main.py"
B = "utils.py"

Reason 1: main.py calls process_data() defined in utils.py
         Edge weight: 500.0, ident="process_data"

Reason 2: main.py calls helper() defined in utils.py
         Edge weight: 20.0, ident="helper"

MultiDiGraph allows both edges.
Ranks from both edges accumulated:
- ranked_definitions[("utils.py", "process_data")] += 500 × rank(main) / total
- ranked_definitions[("utils.py", "helper")] += 20 × rank(main) / total
```

If using standard DiGraph, would need to merge edges and lose information.

---

## INTEGRATION WITH GREP_AST

RepoMap uses `grep_ast` library for:

1. **Language detection**: `filename_to_lang(fname)`
   - Maps file extension → language name
   - Supports 40+ languages

2. **Tree-sitter interface**: `get_language(lang)`, `get_parser(lang)`
   - Returns language-specific parser
   - Handles parser caching

3. **AST rendering**: `TreeContext` class
   - Renders code snippets with context
   - Extracts surrounding scope
   - Handles indentation and formatting

Example integration:

```python
from grep_ast import TreeContext, filename_to_lang
from grep_ast.tsl import get_language, get_parser

# 1. Detect language
lang = filename_to_lang("utils.py")  # → "python"

# 2. Get parser
parser = get_parser(lang)
language = get_language(lang)

# 3. Parse code
tree = parser.parse(bytes(code, "utf-8"))

# 4. Execute query
query = language.query(query_scm)
captures = query.captures(tree.root_node)

# 5. Render context
context = TreeContext(
    rel_fname,
    code,
    color=False,
    line_number=False,
    child_context=False,
    margin=0,
)
context.lines_of_interest = set(interesting_lines)
context.add_context()
rendered = context.format()
```

---

## TESTING & VALIDATION

### Test Scenarios

**Basic Extraction:**
- Single file with definitions and references
- Multiple functions, classes, variables
- Nested structures (inner functions, nested classes)

**Caching:**
- Cache hit on unchanged file
- Cache invalidation on file modification
- Cache recovery from corruption

**Ranking:**
- Files in chat get priority
- Mentioned identifiers boost rank
- Well-named symbols boost rank
- Private symbols demoted

**Graph:**
- Simple dependency chains: A → B → C
- Circular dependencies: A → B → A
- Multi-reference: A references B twice

**Token Selection:**
- Binary search converges
- 15% tolerance satisfied
- No over/under-selection

---

## PRACTICAL USAGE EXAMPLES

### Example 1: Minimal Setup

```python
from aider.repomap import RepoMap

# Create repo map
rm = RepoMap(map_tokens=1024, root="/path/to/repo")

# Get context
repo_map = rm.get_repo_map(
    chat_files=["main.py"],
    other_files=["utils.py", "helpers.py", "config.py"],
)

print(repo_map)
# Output: Formatted code context string
```

### Example 2: With Mentions

```python
repo_map = rm.get_repo_map(
    chat_files=["main.py"],
    other_files=find_all_files("/repo"),
    mentioned_fnames={"src/utils.py", "src/helpers.py"},
    mentioned_idents={"process_data", "validation"},
)
```

### Example 3: Force Refresh

```python
# Bypass cache and recompute
repo_map = rm.get_repo_map(
    chat_files=["main.py"],
    other_files=all_files,
    force_refresh=True,
)
```

### Example 4: Large Context Window

```python
rm = RepoMap(
    map_tokens=4096,
    max_context_window=8192,  # GPT-4 context
    map_mul_no_files=4,       # Allow 4× tokens if no chat files
)

repo_map = rm.get_repo_map(
    chat_files=[],
    other_files=find_all_files("/repo"),
)
# May return up to 16K tokens of context
```

---

## LIMITATIONS & FUTURE IMPROVEMENTS

### Current Limitations

1. **Definition-only languages** (C/C++) require Pygments fallback
2. **Circular dependencies** don't break PageRank but may inflate ranks
3. **Token counting** sampled ~1% (may have ±5% error)
4. **Monorepos** treat entire repo uniformly (could partition)
5. **Performance** still O(F×n) for initial scan
6. **Symbol resolution** pure text-based (no semantic analysis)

### Potential Improvements

1. **Incremental parsing** - Only re-parse changed files (already cached)
2. **Parallel parsing** - Process multiple files concurrently
3. **Semantic analysis** - Use language servers for type-aware ranking
4. **Adaptive sampling** - Adjust token counting strategy by file type
5. **User feedback** - Learn from user edits which symbols matter most
6. **Monorepo support** - Partition by package/module boundaries

---

## REFERENCE IMPLEMENTATION CHECKLIST

If implementing repomap in another language/project:

- [ ] Support 40+ languages via Tree-sitter
- [ ] Implement language-specific `.scm` query files
- [ ] Cache tags with file mtime (handle cache corruption)
- [ ] Extract definitions and references separately
- [ ] Backfill references for definition-only languages
- [ ] Build multi-edge directed graph
- [ ] Implement personalization scoring (chat, mentions)
- [ ] Calculate edge weights (multipliers, dampening)
- [ ] Apply PageRank with personalization vector
- [ ] Distribute file ranks to (file, symbol) pairs
- [ ] Binary search for token count targeting
- [ ] Implement sampling-based token counting
- [ ] Render code context with surrounding AST
- [ ] Handle errors gracefully (fallbacks, warnings)
- [ ] Cache results with keys (files, max_tokens)
- [ ] Provide refresh strategies (auto, always, manual, files)

---

## SUMMARY

Aider's RepoMap is a production-grade system for intelligent context selection in code AI assistants. It combines:

✓ **Tree-sitter parsing** for language-agnostic symbol extraction
✓ **Dependency graphing** to understand code relationships
✓ **PageRank ranking** for importance scoring with personalization
✓ **Binary search optimization** for token budget targeting
✓ **Aggressive caching** for sub-second re-ranking
✓ **Graceful error handling** for robustness

The result: **1-2K tokens of highly relevant code context** that helps LLMs understand and modify codebases effectively.

This is suitable for production use in:
- Code completion assistants
- AI pair programming tools
- Automated code review systems
- Repository understanding/documentation tools
- LLM-based refactoring assistants

The implementation prioritizes:
1. **Accuracy** - Precise symbol extraction via Tree-sitter queries
2. **Relevance** - Dependency-aware ranking with PageRank
3. **Performance** - Efficient caching and sampling
4. **Robustness** - Graceful degradation on errors
5. **Flexibility** - Configuration for different use cases
