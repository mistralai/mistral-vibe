# AST-Grep Tool

## Description

The `ast_grep` tool allows you to search and rewrite code using Abstract Syntax Tree (AST) patterns. This is particularly useful for:

- **Precise code searches**: Find specific code structures rather than just text patterns
- **Safe refactoring**: Rewrite code while preserving syntax correctness
- **Multi-language support**: Works with Rust, Python, JavaScript, and many other languages
- **Complex pattern matching**: Match function signatures, control structures, and more

## When to Use

Use `ast_grep` when you need to:

1. **Find specific code patterns** that would be hard to express with regular expressions
2. **Refactor code safely** while maintaining syntax correctness
3. **Analyze code structure** across multiple files
4. **Apply consistent changes** to similar code patterns

## Basic Usage

### Search for patterns

```python
# Find all function definitions in Rust files
ast_grep(pattern="fn $FUNC($$) -> $$ { $$ }", path=".", lang="rust")

# Find all if statements in Python files
ast_grep(pattern="if $COND: $$", path=".", lang="python")
```

### Rewrite code

```python
# Rename a function in Rust
ast_grep(
    pattern="fn old_name($$) -> $$ { $$ }",
    rewrite="fn new_name($1) -> $2 { $3 }",
    path=".",
    lang="rust"
)

# Replace a specific pattern
ast_grep(
    pattern="let x = 5;",
    rewrite="let x = 10;",
    path="src/main.rs",
    lang="rust"
)
```

## Advanced Features

### Selectors

Use selectors to match specific parts of patterns:

```python
ast_grep(
    pattern="fn $FUNC($$) -> $$ { $$ }",
    selector="function_item",
    path=".",
    lang="rust"
)
```

### Debugging

Enable debug mode to see the parsed AST:

```python
ast_grep(
    pattern="fn add(a: i32, b: i32) -> i32",
    debug_query=True,
    path=".",
    lang="rust"
)
```

## Language Support

Common languages supported:
- `rust` - Rust code
- `python` - Python code
- `javascript` - JavaScript code
- `typescript` - TypeScript code
- `java` - Java code
- `c` - C code
- `cpp` - C++ code
- `go` - Go code

For a full list, see: https://ast-grep.github.io/reference/languages.html

## Best Practices

1. **Start simple**: Begin with basic patterns and gradually add complexity
2. **Test patterns**: Use `debug_query` to verify your pattern matches what you expect
3. **Use variables**: Capture parts of patterns with `$VAR` for flexible matching
4. **Be specific**: Include language specification for better results
5. **Check results**: Always verify the changes before applying them

## Examples

### Find all public functions in Rust

```python
ast_grep(pattern="pub fn $FUNC($$) -> $$ { $$ }", path=".", lang="rust")
```

### Replace println! with logging

```python
ast_grep(
    pattern="println!($$)",
    rewrite="log::info!($1)",
    path=".",
    lang="rust"
)
```

### Find async functions

```python
ast_grep(pattern="async fn $FUNC($$) -> $$ { $$ }", path=".", lang="rust")
```

## Error Handling

If ast-grep fails:

1. Check your pattern syntax
2. Verify the language is supported
3. Ensure the file paths are correct
4. Use `debug_query` to troubleshoot pattern issues
