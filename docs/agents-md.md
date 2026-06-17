# Project Instructions with AGENTS.md

Mistral Vibe supports project-specific instructions through **AGENTS.md** files, similar to Claude Code's CLAUDE.md. These files provide context and guidelines to the AI agent when working within your project.

## Quick Start

Create an `AGENTS.md` file in your project root with instructions for the AI:

```markdown
# My Project Guidelines

## Commands
- `npm install` - Install dependencies
- `npm run dev` - Start development server
- `npm test` - Run tests

## Project Structure
- `src/` - Source code
- `tests/` - Test files
- `public/` - Static assets

## Coding Standards
- Use TypeScript
- Follow ESLint rules
- Prettier for formatting
```

Then start Vibe in your project directory. The agent will automatically read and follow these instructions.

## Automatic Setup with `/init`

Run the `/init` command to analyze your codebase and set up an AGENTS.md file:

```
> /init
```

`/init` works in two steps:

1. A quick **deterministic scan** collects high-signal facts — languages,
   frameworks, build/test/run/lint commands, package managers, dev environments,
   and any monorepo sub-projects.
2. Those facts are handed to **the agent as a normal turn**, which verifies them
   against the actual repo and writes (or improves an existing) AGENTS.md itself.

Because the agent authors the file, the output is repo-aware rather than
template-shaped: it confirms commands actually work, reads conventions from the
code, and explains them. The turn is visible — you can watch, interrupt, or
rewind it like any other.

If an AGENTS.md already exists, `/init` asks the agent to improve it in place
rather than overwrite it.

### What the scan detects

The scan recognizes the following out of the box and passes it to the agent as
hints. Anything not listed is still discovered by the agent when it reads the
repo — the scan only seeds the starting map.

Languages are ranked by file count so the primary language leads, and trivial
stray files (a lone `.css` in a Python repo) are dropped as noise.

**Monorepo-aware.** The analyzer reads every manifest in the tree
(`package.json`, `composer.json`, `Gemfile`, `pyproject.toml`, `go.mod`,
`Cargo.toml`, …), not just the root one, and infers each nested project's stack
from its dependencies. So a Turborepo with a Next.js app in `apps/web` and a
NestJS API in `apps/api`, a Rails API under `backend/` with a React SPA in
`frontend/`, or a Bedrock app under `site/` with a Sage theme several levels
deeper are all surfaced under a **Sub-projects** section, with the managing
orchestrator noted. Run `/init` inside a sub-project for stack-specific commands.

| Category | Detected |
|----------|----------|
| **Languages** | Python, JavaScript, TypeScript (incl. `.tsx`/`.jsx`), Rust, Go, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin, SCSS, Sass, CSS, HTML, SQL, Blade, Twig, Shell, Docker |
| **Frameworks (Python)** | Django, Flask, FastAPI, Pydantic, SQLAlchemy, PyTest |
| **Frameworks (JS/TS)** | React, Vue, Angular, Next.js, Nuxt, Astro, Remix, Gatsby, Svelte, Solid, Express, NestJS |
| **Frameworks (PHP)** | Laravel, Symfony, WordPress, Bedrock, Sage, Acorn |
| **Frameworks (Ruby)** | Ruby on Rails, Sinatra |
| **Monorepo tools** | Turborepo, Nx, Lerna, Rush, pnpm/npm/yarn workspaces, Cargo workspaces, Go workspaces, uv workspaces |
| **Dev environments** | Lando, DDEV, Vagrant, Trellis, wp-env, Dev Container, Docker Compose |
| **Package managers / build** | uv, pip, poetry, pipenv, npm, pnpm, yarn, cargo, go, composer, cmake, maven, gradle, make |

## Instruction Hierarchy

Mistral Vibe uses a **hierarchical instruction system** where instructions can come from multiple sources. When instructions conflict, they are resolved in this order (lowest number wins):

1. **Critical instructions** - Never overridable (safety rules, etc.)
2. **User messages** - Your current conversation with the agent
3. **Repo AGENTS.md files** - All AGENTS.md files from your current directory up to the repository root
4. **User AGENTS.md** - Global instructions from `~/.vibe/AGENTS.md`
5. **System prompts** - Default AI behavior and configuration
6. **Skills / MCP output** - Results from skills and tools
7. **External data** - Fetched content (treated as data, not instructions)

**Key insight:** More specific instructions override more general ones. A project's AGENTS.md will override user-level AGENTS.md, and instructions in a subdirectory's AGENTS.md will override the root AGENTS.md for that subdirectory.

## File Placement

AGENTS.md files can be placed in several locations, each serving a different purpose:

| Location | Scope | Purpose |
|----------|-------|---------|
| `./AGENTS.md` | Project root | Instructions for the entire project |
| `./subdir/AGENTS.md` | Subdirectory | Instructions for that directory and its children |
| `~/.vibe/AGENTS.md` | User-level | Default instructions for all your projects |
| `.vibe/AGENTS.md` | Project root (alternative) | Same as `./AGENTS.md` but keeps config organized |

### Multiple AGENTS.md Files

Vibe supports **multiple AGENTS.md files** in a single project. When working in a directory, Vibe will:

1. Find **all AGENTS.md files** from your current directory up to the repository root
2. Load them **outermost first** (repository root AGENTS.md loads first)
3. Apply **priority to closer files** (directory-specific AGENTS.md overrides general ones)

**Example:**
```
my-project/
├── AGENTS.md                # General project instructions
├── src/
│   └── AGENTS.md            # Backend-specific instructions
└── frontend/
    └── AGENTS.md         # Frontend-specific instructions
```

When working in `frontend/`, the agent will use:
- `my-project/AGENTS.md` (general rules)
- `my-project/frontend/AGENTS.md` (frontend-specific rules that override general ones)

## AGENTS.md vs Skills vs Agent Profiles

Understanding how AGENTS.md fits into Vibe's customization model:

| Component | Location | Purpose | Scope |
|-----------|----------|---------|-------|
| **AGENTS.md** | Project directories | **Project-specific instructions** | Context for AI behavior |
| **Skills** | `~/.vibe/skills/` | **Reusable workflows & slash commands** | Custom capabilities |
| **Agent Profiles** | `~/.vibe/agents/` | **Tool permissions & behavior** | Agent configuration |
| **Prompts** | `~/.vibe/prompts/` | **System instructions** | AI thinking style |

### How They Work Together

- **AGENTS.md** provides **context** ("this project uses Python and Django")
- **Skills** provide **capabilities** ("add a new Django model" command)
- **Agent Profiles** control **permissions** ("this agent can only read files")
- **Prompts** define **personality** ("be concise" vs "be thorough")

**For most users:** AGENTS.md is all you need to get started with project-specific customization.

## What to Include in AGENTS.md

### Essential Sections

#### Project Overview
```markdown
# Project Name

Brief description of what this project does.

Version: 1.0.0
```

#### Commands
```markdown
## Commands

### Build
- `npm install` - Install dependencies
- `npm run build` - Build for production

### Test
- `npm test` - Run all tests
- `npm run test:unit` - Run unit tests only

### Development
- `npm run dev` - Start development server
- `npm run lint` - Run linter
```

#### Project Structure
```markdown
## Project Structure

- `src/` - Main source code
- `src/api/` - API endpoints
- `src/models/` - Data models
- `tests/` - Test files
- `public/` - Static assets
- `config/` - Configuration files
```

#### Coding Standards
```markdown
## Coding Standards

- Use TypeScript for all new code
- Follow ESLint configuration in `.eslintrc.json`
- Use Prettier for code formatting
- All functions must have JSDoc comments
- Tests required for all new features
```

#### Framework/Tool Specific
```markdown
## Django Project

- Use Django REST Framework for API endpoints
- Models go in `models.py` files within each app
- Views go in `views.py` files
- Serializers in `serializers.py`
- Use `djangorestframework` for API views
```

#### Environment Setup
```markdown
## Environment Setup

1. Copy `.env.example` to `.env`
2. Install dependencies: `pip install -r requirements.txt`
3. Run migrations: `python manage.py migrate`
4. Start server: `python manage.py runserver`

## Environment Variables

- `DATABASE_URL` - Database connection string
- `SECRET_KEY` - Django secret key
- `DEBUG` - Set to "True" for development
```

#### AI Agent Guidelines
```markdown
## AI Agent Instructions

When working with this codebase:

- Follow existing code style and patterns
- Add type hints for all Python functions
- Write tests for new functionality
- Use the project's build and test commands
- Respect the project structure
- Document new functions and classes
```

## Best Practices

### Be Specific
```markdown
# Good ✅
- Use `black .` for Python formatting
- Run `pytest` before committing

# Bad ❌
- Format code nicely
- Test your code
```

### Use Code Examples
```markdown
## API Development

When creating new endpoints:

```python
# Good pattern
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
```
```

### Document Architectural Decisions
```markdown
## Architecture

- **Event-driven architecture** - Use Redis for event publishing
- **Service pattern** - Business logic in `services/` directory
- **Repository pattern** - Database access through repository classes
```

### Include Common Workflows
```markdown
## Common Workflows

### Adding a new feature
1. Create migration: `python manage.py makemigrations`
2. Apply migration: `python manage.py migrate`
3. Add tests in `tests/` directory
4. Update documentation

### Debugging
- Check logs: `tail -f logs/app.log`
- Run tests: `pytest tests/test_feature.py -v`
```

## Advanced Usage

### Multiple Projects

For users working with multiple projects:

1. **User-level AGENTS.md** (`~/.vibe/AGENTS.md`) - Instructions that apply to all your projects
   ```markdown
   # My Default Preferences
   
   - Use TypeScript when possible
   - Prefer functional components in React
   - Always add error handling
   ```

2. **Project-specific AGENTS.md** - Instructions specific to each project (overrides user-level)

### Team Collaboration

AGENTS.md files are perfect for team collaboration:

- **Commit to version control** - Share project conventions with your team
- **Onboard new developers** - Document project-specific workflows
- **Maintain consistency** - Ensure all team members follow the same patterns

**Example team AGENTS.md:**
```markdown
# Team Development Guidelines

## Code Review Process
- All PRs must pass CI checks
- Require at least 2 approvals for merges
- Use conventional commit messages

## Branch Strategy
- `main` - Production releases only
- `develop` - Integration branch
- `feature/*` - New features
- `fix/*` - Bug fixes
```

## Migration from Claude Code

If you're coming from Claude Code, here's how to migrate:

| Claude Code | Mistral Vibe |
|-------------|--------------|
| `CLAUDE.md` | `AGENTS.md` |
| Project root | Project root or `.vibe/AGENTS.md` |
| Instructions apply to project | Same behavior |
| Multiple CLAUDE.md files | Multiple AGENTS.md files supported |

**Simple migration:**
1. Rename `CLAUDE.md` to `AGENTS.md`
2. Place in project root
3. Start using Vibe - it will automatically pick up the instructions

## Troubleshooting

### AGENTS.md Not Working?

1. **Check file location** - Must be in project root or a parent directory
2. **Check file name** - Must be exactly `AGENTS.md` (case-sensitive)
3. **Check file content** - Must have non-empty content
4. **Check working directory** - Vibe loads AGENTS.md files from the current directory up to repo root

### Verify AGENTS.md is Loaded

Ask the agent:
```
What project instructions are you currently following?
```

The agent should summarize the instructions from your AGENTS.md files.

### Common Issues

- **File not found** - AGENTS.md must be in the current directory or a parent directory
- **Permission issues** - Vibe can only read AGENTS.md files in trusted directories
- **Empty content** - Empty AGENTS.md files are ignored
- **Wrong encoding** - Use UTF-8 encoding for AGENTS.md files

## Examples

### Python Project
```markdown
# Python Data Analysis Project

## Setup
- Python 3.12+
- `pip install -r requirements.txt`

## Commands
- `pytest` - Run tests
- `ruff check .` - Lint code
- `mypy .` - Type checking
- `jupyter notebook` - Start notebook

## Structure
- `src/` - Main code
- `tests/` - Tests
- `notebooks/` - Jupyter notebooks
- `data/` - Data files (in .gitignore)

## Standards
- Use type hints
- Follow PEP 8
- Use pandas for data manipulation
- Type check with mypy
```

### Node.js Project
```markdown
# Full-Stack JavaScript Project

## Tech Stack
- Node.js 18+
- Express.js backend
- React frontend
- PostgreSQL database

## Commands
- `npm install` - Install dependencies
- `npm run dev` - Run both frontend and backend
- `npm run build` - Production build
- `npm test` - Run all tests

## Structure
- `backend/` - Express server
- `frontend/` - React app
- `database/` - Database schemas and migrations

## Standards
- Use ESLint and Prettier
- TypeScript for new code
- Jest for testing
```

### Go Project
```markdown
# Go Microservice

## Setup
- Go 1.21+
- `go mod download` - Download dependencies

## Commands
- `go build` - Build binary
- `go test ./...` - Run all tests
- `golangci-lint run` - Lint code

## Structure
- `cmd/` - Entry points
- `internal/` - Private code
- `pkg/` - Library code
- `api/` - API definitions

## Standards
- Use gofmt for formatting
- Add proper error handling
- Document exported functions
```

## Summary

AGENTS.md files provide a **powerful, flexible way** to give Mistral Vibe project-specific context and instructions. They work similarly to Claude Code's CLAUDE.md files and are automatically loaded by the agent when working in your project.

**For most users:** Creating an AGENTS.md file is the simplest way to get started with customizing Vibe's behavior for your specific project needs.

**Use `/init` command** to automatically generate a comprehensive AGENTS.md file based on your codebase analysis.