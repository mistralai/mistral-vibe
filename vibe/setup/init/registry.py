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

# Manifest files that mark a (sub-)project, scanned across the tree so nested
# projects in a monorepo are discovered (a Next.js app in `apps/web`, a Rails
# API in `backend/`, a Sage theme several levels deep, ...). Each manifest's
# stack is inferred from its declared dependencies; see `analyzer._infer_stack`.
MANIFEST_NAMES: tuple[str, ...] = (
    "package.json",
    "composer.json",
    "Gemfile",
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
)

# Maps a manifest filename to its package manager. Used to bubble up package
# managers discovered in sub-projects when the root has no matching manifest.
MANIFEST_PACKAGE_MANAGER: dict[str, str] = {
    "composer.json": "composer",
    "Gemfile": "bundler",
    "go.mod": "go",
    "Cargo.toml": "cargo",
}

# JS/TS framework keyed by its package.json dependency name. Used to infer the
# stack of every package.json (root and nested), replacing root-only detection.
JS_FRAMEWORK_BY_DEP: dict[str, str] = {
    "next": "Next.js",
    "nuxt": "Nuxt",
    "astro": "Astro",
    "@remix-run/react": "Remix",
    "gatsby": "Gatsby",
    "@angular/core": "Angular",
    "@nestjs/core": "NestJS",
    "express": "Express",
    "react": "React",
    "vue": "Vue",
    "svelte": "Svelte",
    "solid-js": "Solid",
}
# When several JS frameworks are present in one package.json, the meta-framework
# (Next.js, Nuxt, ...) is the project's real identity, so it is preferred over a
# bare UI library (React, Vue) when labelling the sub-project.
JS_FRAMEWORK_PRIORITY: tuple[str, ...] = (
    "Next.js",
    "Nuxt",
    "Astro",
    "Remix",
    "Gatsby",
    "Angular",
    "NestJS",
    "Express",
    "React",
    "Vue",
    "Svelte",
    "Solid",
)

# Monorepo orchestrators, keyed by a root marker file.
MONOREPO_FILE_MARKERS: dict[str, str] = {
    "pnpm-workspace.yaml": "pnpm workspaces",
    "turbo.json": "Turborepo",
    "nx.json": "Nx",
    "lerna.json": "Lerna",
    "go.work": "Go workspaces",
    "rush.json": "Rush",
}
