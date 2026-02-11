# Contributing to Mistral Vibe

Thank you for your interest in Mistral Vibe! We appreciate your enthusiasm and support.

## Current Status

**Mistral Vibe is in active development** — our team is iterating quickly and making lots of changes under the hood. Because of this pace, we may be slower than usual when reviewing PRs and issues.

**We especially encourage**:

- **Bug reports** – Help us uncover and squash issues
- **Feedback & ideas** – Tell us what works, what doesn't, and what could be even better
- **Documentation improvements** – Suggest clarity improvements or highlight missing pieces

## How to Provide Feedback

### Bug Reports

If you encounter a bug, please open an issue with the following information:

1. **Description**: A clear description of the bug
2. **Steps to Reproduce**: Detailed steps to reproduce the issue
3. **Expected Behavior**: What you expected to happen
4. **Actual Behavior**: What actually happened
5. **Environment**:
   - Python version
   - Operating system
   - Vibe version
6. **Error Messages**: Any error messages or stack traces
7. **Configuration**: Relevant parts of your `config.toml` (redact any sensitive information)

### Feature Requests and Feedback

We'd love to hear your ideas! When submitting feedback or feature request discussions:

1. **Avoid duplicates**: Check opened discussions before creating a new one
2. **Clear Description**: Explain what you'd like to see or improve
3. **Use Case**: Describe your use case and why this would be valuable
4. **Alternatives**: If applicable, mention any alternatives you've considered

## Development Setup

This section is for developers who want to set up the repository for local development, even though we're not currently accepting contributions.

### Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) - Modern Python package manager

### Setup

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd mistral-vibe
   ```

2. Install dependencies:

   ```bash
   uv sync --all-extras
   ```

   This will install both runtime and development dependencies.

3. (Optional) Install pre-commit hooks:

   ```bash
   uv run pre-commit install
   ```

   Pre-commit hooks will automatically run checks before each commit.

### Running Tests

Run all tests:

```bash
uv run pytest
```

Run tests with verbose output:

```bash
uv run pytest -v
```

Run a specific test file:

```bash
uv run pytest tests/test_agent_tool_call.py
```

### Linting and Type Checking

#### Ruff (Linting and Formatting)

Check for linting issues (without fixing):

```bash
uv run ruff check .
```

Auto-fix linting issues:

```bash
uv run ruff check --fix .
```

Format code:

```bash
uv run ruff format .
```

Check formatting without modifying files (useful for CI):

```bash
uv run ruff format --check .
```

#### Pyright (Type Checking)

Run type checking:

```bash
uv run pyright
```

#### Pre-commit Hooks

Run all pre-commit hooks manually:

```bash
uv run pre-commit run --all-files
```

The pre-commit hooks include:

- Ruff (linting and formatting)
- Pyright (type checking)
- Typos (spell checking)
- YAML/TOML validation
- Action validator (for GitHub Actions)

### Code Style

- **Line length**: 88 characters (Black-compatible)
- **Type hints**: Required for all functions and methods
- **Docstrings**: Follow Google-style docstrings
- **Formatting**: Use Ruff for both linting and formatting
- **Type checking**: Use Pyright (configured in `pyproject.toml`)

See `pyproject.toml` for detailed configuration of Ruff and Pyright.

## Code Contributions

While we're not accepting code contributions at the moment, we may open up contributions in the future. When that happens, we'll update this document with:

- Pull request process
- Contribution guidelines
- Review process

## Questions?

If you have questions about using Mistral Vibe, please check the [README](README.md) first. For other inquiries, feel free to open a discussion or issue.

Thank you for helping make Mistral Vibe better! 🙏
