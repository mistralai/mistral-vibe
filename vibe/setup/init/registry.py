"""Single source of truth for what the ``/init`` analyzer recognises.

This module holds only *data* — the language/framework/dev-environment tables the
analyzer scans for and the per-language guidance the generator emits. Keeping it
separate from the detection logic (``analyzer.py``) and the markdown rendering
(``generator.py``) means adding support for a new language or framework is a
one-file edit, and the tables stay in sync with the reference table in
``docs/agents-md.md``.
"""

from __future__ import annotations

# Map a file extension -> language. Drives the count-based language scan in
# ``analyzer._scan_files``. Note ``.tsx``/``.jsx`` are listed explicitly:
# ``fnmatch("App.tsx", "*.ts")`` is False, so glob-based detection missed
# TSX/JSX-only projects entirely.
LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".cs": "C#",
    ".csproj": "C#",
    ".sln": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".css": "CSS",
    ".html": "HTML",
    ".htm": "HTML",
    ".sql": "SQL",
    ".twig": "Twig",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
}

# Marker filenames that guarantee a language even when few/no source files are
# present (a ``pyproject.toml`` with all code under an excluded dir, etc.).
LANGUAGE_MARKERS: dict[str, str] = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "requirements.txt": "Python",
    "Pipfile": "Python",
    "poetry.lock": "Python",
    "package.json": "JavaScript",
    "tsconfig.json": "TypeScript",
    "jsconfig.json": "TypeScript",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "build.gradle.kts": "Java",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "Package.swift": "Swift",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker",
    "docker-compose.yaml": "Docker",
}

# File extensions that signal a JS/TS UI framework. These supplement (and back
# up) package.json-based detection, which misses framework files outside a
# declared dependency (e.g. a component library or a sub-package).
FRAMEWORK_BY_EXT: dict[str, str] = {
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".astro": "Astro",
}

# Local development environment tooling, keyed by a marker file/dir relative to
# a project root. Searched a few levels deep so a monorepo with the app in a
# subdirectory (e.g. ``site/``) still matches. Herd and Valet are intentionally
# absent: they are global, machine-level tools that leave no project marker.
DEV_ENV_FILE_MARKERS: dict[str, str] = {
    "Lando": ".lando.yml",
    "Vagrant": "Vagrantfile",
    "wp-env": ".wp-env.json",
}
DEV_ENV_DIR_MARKERS: dict[str, str] = {
    "DDEV": ".ddev",
    "Dev Container": ".devcontainer",
    # Trellis (Roots' Ansible-based provisioning/VM stack for Bedrock sites).
    "Trellis": "trellis",
}

# Per-language workflow guidance emitted under "## Development Guidelines".
LANGUAGE_GUIDELINES: dict[str, list[str]] = {
    "python": [
        "- Run tests before committing: `pytest`",
        "- Format code with: `ruff format .`",
        "- Lint with: `ruff check --fix .`",
        "- Type check with: `mypy .`",
    ],
    "javascript": [
        "- Install dependencies: `npm install`",
        "- Run tests: `npm test`",
        "- Lint: `npm run lint`",
    ],
    "rust": [
        "- Build: `cargo build`",
        "- Test: `cargo test`",
        "- Lint: `cargo clippy`",
        "- Format: `cargo fmt`",
    ],
    "go": [
        "- Build: `go build ./...`",
        "- Test: `go test ./...`",
        "- Format: `gofmt -w .`",
    ],
    "php": [
        "- Install dependencies: `composer install`",
        "- Run tests: `vendor/bin/phpunit`",
        "- Check coding standards: `vendor/bin/phpcs`",
        "- Auto-fix coding standards: `vendor/bin/phpcbf`",
    ],
    "c": [
        "- Configure build: `cmake -B build`",
        "- Build: `cmake --build build`",
        "- Run tests: `ctest --test-dir build`",
    ],
    "java": [
        "- Build: `mvn package` (or `./gradlew build`)",
        "- Run tests: `mvn test` (or `./gradlew test`)",
    ],
    "ruby": [
        "- Install dependencies: `bundle install`",
        "- Run tests: `bundle exec rspec` (or `rake test`)",
        "- Lint: `bundle exec rubocop`",
    ],
}
# TypeScript shares the Node.js workflow; C++ shares the CMake workflow.
LANGUAGE_GUIDELINES["typescript"] = LANGUAGE_GUIDELINES["javascript"]
LANGUAGE_GUIDELINES["c++"] = LANGUAGE_GUIDELINES["c"]

# A detected framework is the strongest signal of the primary language — e.g. a
# WordPress theme is alphabetically "JavaScript, PHP" but is really a PHP
# project. Used to promote the framework's language ahead of the ranked list
# when picking which language's guidance to emit.
FRAMEWORK_LANGUAGE: dict[str, str] = {
    "wordpress": "php",
    "laravel": "php",
    "symfony": "php",
    "bedrock": "php",
    "sage": "php",
    "acorn": "php",
    "django": "python",
    "flask": "python",
    "fastapi": "python",
    "react": "javascript",
    "vue": "javascript",
    "angular": "javascript",
    "next.js": "javascript",
    "express": "javascript",
    "nestjs": "javascript",
    "ruby on rails": "ruby",
    "sinatra": "ruby",
}
