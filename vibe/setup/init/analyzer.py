from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import fnmatch
import os
from pathlib import Path
import re

from vibe.core.utils.io import read_safe


@dataclass
class CodebaseAnalysis:
    """Analysis results from a codebase scan."""
    
    # Build system detection
    build_commands: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    run_commands: list[str] = field(default_factory=list)
    lint_commands: list[str] = field(default_factory=list)
    
    # Package management
    package_managers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    dev_dependencies: list[str] = field(default_factory=list)
    
    # Language/framework detection
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    
    # Project structure
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    
    # Code standards
    coding_standards: list[str] = field(default_factory=list)
    naming_conventions: list[str] = field(default_factory=list)
    
    # Architectural patterns
    architecture: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    
    # Workflows
    workflows: list[str] = field(default_factory=list)
    git_workflows: list[str] = field(default_factory=list)
    
    # Entry points
    entry_points: list[str] = field(default_factory=list)
    main_modules: list[str] = field(default_factory=list)
    
    # Environment setup
    env_vars: list[str] = field(default_factory=list)
    setup_steps: list[str] = field(default_factory=list)
    
    # File patterns
    file_patterns: list[str] = field(default_factory=list)
    ignore_patterns: list[str] = field(default_factory=list)
    
    # Project metadata
    project_name: str = ""
    project_description: str = ""
    project_version: str = ""
    
    # Repository info
    repo_root: Path | None = None
    has_git: bool = False
    has_vcs: bool = False


@dataclass
class AnalysisConfig:
    """Configuration for codebase analysis."""

    max_depth: int = 4
    max_files: int = 1000
    exclude_dirs: set[str] = field(default_factory=lambda: {
        ".git", ".svn", ".hg", "node_modules", ".venv", "venv", ".env",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".vscode", ".idea",
        "build", "dist", "target", "out", "bin", "obj", "logs",
        "vendor", ".next", ".nuxt", ".svelte-kit", ".gradle", ".tox",
        "coverage", "Pods", ".cargo", "vendor-bin",
    })
    exclude_files: set[str] = field(default_factory=lambda: {
        "*.log", "*.pyc", "*.swp", "*.swo", ".DS_Store", "Thumbs.db"
    })


def _find_files(
    root: Path,
    pattern: str,
    config: AnalysisConfig,
    limit: int | None = None,
) -> list[Path]:
    """Find files matching a glob pattern, pruning excluded dirs and respecting depth/limits.

    This is a bounded replacement for ``Path.rglob`` that honors ``config.exclude_dirs``
    (so we never descend into ``node_modules``, ``.venv``, ``vendor`` etc.), ``max_depth``
    and ``max_files``. Without this, language/framework detection picks up files from
    dependencies and the scan can be very slow on large repos.
    """
    matches: list[Path] = []
    cap = limit if limit is not None else config.max_files
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        # Prune excluded directories in-place so os.walk skips them entirely.
        dirnames[:] = [d for d in dirnames if d not in config.exclude_dirs]
        if depth >= config.max_depth:
            dirnames[:] = []
        for filename in filenames:
            if fnmatch.fnmatch(filename, pattern):
                matches.append(Path(dirpath) / filename)
                if cap is not None and len(matches) >= cap:
                    return matches
    return matches


async def analyze_codebase(
    root: Path,
    config: AnalysisConfig | None = None,
) -> CodebaseAnalysis:
    """Analyze a codebase to discover conventions, commands, and structure.
    
    Args:
        root: The root directory to analyze
        config: Optional analysis configuration
        
    Returns:
        CodebaseAnalysis with discovered information
    """
    analysis_config = config or AnalysisConfig()
    analysis = CodebaseAnalysis()
    
    # Resolve root path
    root = root.resolve()
    analysis.repo_root = root
    
    # Run analysis tasks concurrently
    tasks = [
        _detect_vcs(root, analysis),
        _detect_project_metadata(root, analysis),
        _detect_build_system(root, analysis),
        _detect_language_framework(root, analysis, analysis_config),
        _detect_structure(root, analysis, analysis_config),
        _detect_entry_points(root, analysis, analysis_config),
        _detect_env_setup(root, analysis, analysis_config),
        _detect_code_standards(root, analysis, analysis_config),
    ]
    
    await asyncio.gather(*tasks)

    # Deduplicate while preserving the order/sorting done by each detector.
    # (list(set(...)) would randomize ordering and make output non-deterministic.)
    analysis.build_commands = list(dict.fromkeys(analysis.build_commands))
    analysis.test_commands = list(dict.fromkeys(analysis.test_commands))
    analysis.languages = list(dict.fromkeys(analysis.languages))
    analysis.frameworks = list(dict.fromkeys(analysis.frameworks))

    return analysis


async def _detect_vcs(root: Path, analysis: CodebaseAnalysis) -> None:
    """Detect version control system."""
    # Check for Git
    git_dir = root / ".git"
    if git_dir.exists():
        analysis.has_git = True
        analysis.has_vcs = True
        
        # Detect Git workflows
        try:
            # Check for common Git workflow files
            for pattern in [".github/workflows/*.yml", ".github/workflows/*.yaml", ".gitlab-ci.yml", ".gitignore"]:
                if list(root.glob(pattern)):
                    analysis.git_workflows.append(pattern)
        except (OSError, PermissionError):
            pass


async def _detect_project_metadata(root: Path, analysis: CodebaseAnalysis) -> None:  # pylint: disable=too-many-branches
    """Detect project metadata from common files."""
    # Check for package.json
    package_json = root / "package.json"
    if package_json.exists():
        try:
            import json
            with open(package_json, encoding="utf-8") as f:
                data = json.load(f)
                analysis.project_name = data.get("name", "")
                analysis.project_description = data.get("description", "")
                analysis.project_version = data.get("version", "")
        except (json.JSONDecodeError, OSError, ImportError):
            pass
    
    # Check for pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = read_safe(pyproject).text
            # Extract basic info with regex (avoid full TOML parsing)
            if name_match := re.search(r'name\s*=\s*["\']([^"\']+)["\']', content):
                analysis.project_name = name_match.group(1)
            if desc_match := re.search(r'description\s*=\s*["\']([^"\']+)["\']', content):
                analysis.project_description = desc_match.group(1)
            if version_match := re.search(r'version\s*=\s*["\']([^"\']+)["\']', content):
                analysis.project_version = version_match.group(1)
        except (OSError, UnicodeDecodeError):
            pass
    
    # Check for Cargo.toml (Rust)
    cargo_toml = root / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = read_safe(cargo_toml).text
            if name_match := re.search(r'^name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE):
                analysis.project_name = name_match.group(1)
            if version_match := re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE):
                analysis.project_version = version_match.group(1)
        except (OSError, UnicodeDecodeError):
            pass

    # Check for composer.json (PHP) — only fill fields not already set.
    composer_json = root / "composer.json"
    if composer_json.exists():
        try:
            import json
            with open(composer_json, encoding="utf-8") as f:
                data = json.load(f)
            if not analysis.project_name:
                analysis.project_name = data.get("name", "")
            if not analysis.project_description:
                analysis.project_description = data.get("description", "")
            if not analysis.project_version:
                analysis.project_version = data.get("version", "")
        except (json.JSONDecodeError, OSError, ImportError):
            pass

    # Check for go.mod module name (Go).
    go_mod = root / "go.mod"
    if go_mod.exists() and not analysis.project_name:
        try:
            content = read_safe(go_mod).text
            if module_match := re.search(r'^module\s+(\S+)', content, re.MULTILINE):
                analysis.project_name = module_match.group(1).rsplit("/", 1)[-1]
        except (OSError, UnicodeDecodeError):
            pass

    # Check for README files for project description
    for readme in ["README.md", "README.txt", "readme.md"]:
        readme_path = root / readme
        if readme_path.exists():
            try:
                content = read_safe(readme_path).text
                # First non-empty line as potential description (strip markdown heading markers)
                if not analysis.project_description:
                    first_line = content.split('\n')[0].strip().lstrip('#').strip()
                    if first_line and first_line.lower() != analysis.project_name.lower():
                        analysis.project_description = first_line[:200]  # Limit length
            except (OSError, UnicodeDecodeError):
                pass
            break


async def _detect_build_system(root: Path, analysis: CodebaseAnalysis) -> None:  # pylint: disable=too-many-branches,too-many-statements
    """Detect build systems and their commands."""
    # Python-specific
    pyproject = root / "pyproject.toml"
    setup_py = root / "setup.py"
    requirements = root / "requirements.txt"

    if pyproject.exists() or setup_py.exists():
        has_uv = (root / "uv.lock").exists()
        if has_uv:
            analysis.package_managers.append("uv")
            analysis.build_commands.append("uv sync")
            analysis.test_commands.append("uv run pytest")
        else:
            analysis.package_managers.append("pip")
            analysis.build_commands.append("pip install -e .")
            analysis.test_commands.append("pytest")

        pyproject_content = ""
        if pyproject.exists():
            try:
                pyproject_content = read_safe(pyproject).text
            except (OSError, UnicodeDecodeError):
                pass

        has_ruff = "[tool.ruff" in pyproject_content or (root / "ruff.toml").exists()
        has_black = "[tool.black" in pyproject_content or (root / ".black.toml").exists()
        has_mypy = "[tool.mypy" in pyproject_content or (root / "mypy.ini").exists() or (root / ".mypy.ini").exists()

        if has_ruff:
            analysis.lint_commands.append("ruff check .")
            analysis.lint_commands.append("ruff format .")
        elif has_black:
            analysis.lint_commands.append("black .")
            if "[tool.isort" in pyproject_content:
                analysis.lint_commands.append("isort .")

        if has_mypy:
            analysis.lint_commands.append("mypy .")

    if requirements.exists() and not (pyproject.exists() or setup_py.exists()):
        analysis.package_managers.append("pip")
        analysis.build_commands.append("pip install -r requirements.txt")

    # Node.js — detect actual package manager
    package_json = root / "package.json"
    node_pm = "npm"
    if package_json.exists():
        if (root / "pnpm-lock.yaml").exists():
            node_pm = "pnpm"
        elif (root / "yarn.lock").exists():
            node_pm = "yarn"
        analysis.package_managers.append(node_pm)

        analysis.build_commands.append(f"{node_pm} install")
        analysis.test_commands.append(f"{node_pm} test")
        analysis.lint_commands.append(f"{node_pm} run lint")
        analysis.build_commands.append(f"{node_pm} run build")
    
    # Rust/Cargo
    cargo_toml = root / "Cargo.toml"
    if cargo_toml.exists():
        analysis.package_managers.append("cargo")
        analysis.build_commands.append("cargo build")
        analysis.build_commands.append("cargo build --release")
        analysis.test_commands.append("cargo test")
        analysis.lint_commands.append("cargo clippy")
        analysis.lint_commands.append("cargo fmt")
    
    # Go
    go_mod = root / "go.mod"
    if go_mod.exists():
        analysis.package_managers.append("go")
        analysis.build_commands.append("go build")
        analysis.build_commands.append("go build ./...")
        analysis.test_commands.append("go test ./...")
        analysis.lint_commands.append("golangci-lint run")
        analysis.lint_commands.append("gofmt -w .")

    # PHP/Composer
    composer_json = root / "composer.json"
    if composer_json.exists():
        analysis.package_managers.append("composer")
        analysis.build_commands.append("composer install")
        try:
            import json
            with open(composer_json, encoding="utf-8") as f:
                data = json.load(f)
            # Named composer scripts
            for script_name in data.get("scripts", {}):
                cmd = f"composer {script_name}"
                low = script_name.lower()
                if "test" in low:
                    analysis.test_commands.append(cmd)
                elif "lint" in low or "cs" in low or "stan" in low or "analy" in low:
                    analysis.lint_commands.append(cmd)
                elif "build" in low:
                    analysis.build_commands.append(cmd)
            # Tooling inferred from dependencies
            deps = {**data.get("require", {}), **data.get("require-dev", {})}
            if any("phpunit" in d for d in deps):
                analysis.test_commands.append("vendor/bin/phpunit")
            if any("pestphp" in d for d in deps):
                analysis.test_commands.append("vendor/bin/pest")
            if any("squizlabs/php_codesniffer" in d for d in deps):
                analysis.lint_commands.append("vendor/bin/phpcs")
                analysis.lint_commands.append("vendor/bin/phpcbf")
            if any("friendsofphp/php-cs-fixer" in d for d in deps):
                analysis.lint_commands.append("vendor/bin/php-cs-fixer fix")
            if any("phpstan" in d for d in deps):
                analysis.lint_commands.append("vendor/bin/phpstan analyse")
        except (json.JSONDecodeError, OSError, ImportError):
            pass

    # Standalone PHPUnit config (no composer dependency declared)
    if ((root / "phpunit.xml").exists() or (root / "phpunit.xml.dist").exists()) \
            and "vendor/bin/phpunit" not in analysis.test_commands:
        analysis.test_commands.append("vendor/bin/phpunit")

    # CMake (C/C++)
    if (root / "CMakeLists.txt").exists():
        analysis.package_managers.append("cmake")
        analysis.build_commands.append("cmake -B build")
        analysis.build_commands.append("cmake --build build")
        analysis.test_commands.append("ctest --test-dir build")

    # Java — Maven
    if (root / "pom.xml").exists():
        analysis.package_managers.append("maven")
        analysis.build_commands.append("mvn package")
        analysis.test_commands.append("mvn test")

    # Java/Kotlin — Gradle
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        analysis.package_managers.append("gradle")
        gradle = "./gradlew" if (root / "gradlew").exists() else "gradle"
        analysis.build_commands.append(f"{gradle} build")
        analysis.test_commands.append(f"{gradle} test")

    # JavaScript/TypeScript — extract named scripts using detected package manager
    if package_json.exists():
        try:
            import json
            with open(package_json, encoding="utf-8") as f:
                data = json.load(f)
                scripts = data.get("scripts", {})
                for script_name in scripts:
                    full_cmd = f"{node_pm} run {script_name}"
                    if "build" in script_name.lower():
                        analysis.build_commands.append(full_cmd)
                    elif "test" in script_name.lower():
                        analysis.test_commands.append(full_cmd)
                    elif "lint" in script_name.lower():
                        analysis.lint_commands.append(full_cmd)
                    elif script_name in {"start", "dev"}:
                        analysis.run_commands.append(full_cmd)
        except (json.JSONDecodeError, OSError, ImportError):
            pass
    
    # Makefile
    makefile = root / "Makefile"
    if makefile.exists():
        try:
            content = read_safe(makefile).text
            # Extract targets
            targets = re.findall(r'^([a-zA-Z_-]+):', content, re.MULTILINE)
            for target in targets:
                cmd = f"make {target}"
                if target in {"build", "all"}:
                    analysis.build_commands.append(cmd)
                elif target in {"test", "check"}:
                    analysis.test_commands.append(cmd)
                elif target in {"run", "start"}:
                    analysis.run_commands.append(cmd)
                elif target in {"lint", "format"}:
                    analysis.lint_commands.append(cmd)
        except (OSError, UnicodeDecodeError):
            pass
    
    # Sort and deduplicate
    for attr in ["build_commands", "test_commands", "run_commands", "lint_commands"]:
        commands = getattr(analysis, attr)
        commands.sort()
        setattr(analysis, attr, list(dict.fromkeys(commands)))  # Preserve order, remove dupes


async def _detect_language_framework(root: Path, analysis: CodebaseAnalysis, config: AnalysisConfig) -> None:  # pylint: disable=too-many-branches,too-many-nested-blocks,too-many-statements
    """Detect languages and frameworks."""
    # Check for language-specific files
    file_indicators: dict[str, list[str]] = {
        "Python": ["*.py", "pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "JavaScript": ["*.js", "*.mjs", "package.json"],
        "TypeScript": ["*.ts", "tsconfig.json"],
        "Rust": ["*.rs", "Cargo.toml"],
        "Go": ["*.go", "go.mod"],
        "Java": ["*.java", "pom.xml", "build.gradle"],
        "C": ["*.c"],
        "C++": ["*.cpp", "*.cc", "*.cxx", "*.hpp"],
        "C#": ["*.cs", "*.sln", "*.csproj"],
        "Ruby": ["*.rb", "Gemfile"],
        "PHP": ["*.php", "composer.json"],
        "Swift": ["*.swift", "Package.swift"],
        "Kotlin": ["*.kt", "*.kts"],
        "SCSS": ["*.scss"],
        "Sass": ["*.sass"],
        "CSS": ["*.css"],
        "Shell": ["*.sh"],
        "Docker": ["Dockerfile", "docker-compose.yml"],
    }

    for lang, indicators in file_indicators.items():
        for indicator in indicators:
            if indicator.startswith("*."):
                try:
                    if _find_files(root, indicator, config, limit=1):
                        analysis.languages.append(lang)
                        break
                except (OSError, PermissionError):
                    pass
            elif (root / indicator).exists():
                analysis.languages.append(lang)
                break
    
    # Framework detection based on languages
    if "Python" in analysis.languages:
        frameworks_to_check = [
            ("Django", ["manage.py", "django"], ["settings.py"]),
            ("Flask", ["app.py", "wsgi.py"], ["flask", "Flask"]),
            ("FastAPI", ["main.py", "app.py"], ["fastapi", "FastAPI", "uvicorn"]),
            ("Pydantic", [], ["pydantic"]),
            ("SQLAlchemy", [], ["sqlalchemy", "SQLAlchemy"]),
            ("PyTest", [], ["pytest", "test_"]),
        ]
        
        for framework, files, pattern_imports in frameworks_to_check:
            if any((root / f).exists() for f in files):
                analysis.frameworks.append(framework)
            else:
                # Check for imports in Python files
                try:
                    py_files = _find_files(root, "*.py", config, limit=50)
                    for py_file in py_files:
                        if py_file.is_file():
                            content = read_safe(py_file).text
                            for imp in pattern_imports:
                                if imp in content:
                                    analysis.frameworks.append(framework)
                                    break
                            if framework in analysis.frameworks:
                                break
                except (OSError, PermissionError):
                    pass
    
    if "JavaScript" in analysis.languages or "TypeScript" in analysis.languages:
        frameworks_to_check = [
            ("React", ["react"]),
            ("Vue", ["vue"]),
            ("Angular", ["@angular/core"]),
            ("Next.js", ["next"]),
            ("Express", ["express"]),
            ("NestJS", ["@nestjs/core"]),
        ]

        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                import json
                with open(pkg_json, encoding="utf-8") as f:
                    data = json.load(f)
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    for framework, dep_names in frameworks_to_check:
                        if any(dep in deps for dep in dep_names):
                            analysis.frameworks.append(framework)
            except (json.JSONDecodeError, OSError, ImportError):
                pass

        for config_file, framework in [("next.config.js", "Next.js"), ("next.config.mjs", "Next.js"),
                                        ("angular.json", "Angular"), ("nest-cli.json", "NestJS")]:
            if (root / config_file).exists() and framework not in analysis.frameworks:
                analysis.frameworks.append(framework)

    if "PHP" in analysis.languages:
        composer_deps: dict[str, str] = {}
        composer_json = root / "composer.json"
        if composer_json.exists():
            try:
                import json
                with open(composer_json, encoding="utf-8") as f:
                    cdata = json.load(f)
                composer_deps = {**cdata.get("require", {}), **cdata.get("require-dev", {})}
            except (json.JSONDecodeError, OSError, ImportError):
                pass

        if (root / "artisan").exists() or any(d.startswith("laravel/") for d in composer_deps):
            analysis.frameworks.append("Laravel")
        if (root / "bin" / "console").exists() or any(d.startswith("symfony/") for d in composer_deps):
            analysis.frameworks.append("Symfony")

        # WordPress: core install, theme (style.css header / functions.php), or known packages.
        is_wordpress = (root / "wp-config.php").exists() or (root / "wp-load.php").exists()
        if not is_wordpress and (root / "style.css").exists():
            try:
                if "Theme Name:" in read_safe(root / "style.css").text:
                    is_wordpress = True
            except (OSError, UnicodeDecodeError):
                pass
        if not is_wordpress and (root / "functions.php").exists():
            is_wordpress = True
        if not is_wordpress and any("roots/" in d or "wpackagist" in d for d in composer_deps):
            is_wordpress = True
        if is_wordpress:
            analysis.frameworks.append("WordPress")

    # Sort and deduplicate
    analysis.languages.sort()
    analysis.languages = list(dict.fromkeys(analysis.languages))
    analysis.frameworks.sort()
    analysis.frameworks = list(dict.fromkeys(analysis.frameworks))


async def _detect_structure(root: Path, analysis: CodebaseAnalysis, config: AnalysisConfig) -> None:
    """Detect project structure."""
    # Common source directories
    source_dirs = ["src", "source", "lib", "app", "server", "backend", "frontend", "client"]
    test_dirs = ["tests", "test", "testing", "spec", "specs", "__tests__"]
    
    for dirname in source_dirs:
        dirpath = root / dirname
        if dirpath.exists() and dirpath.is_dir():
            analysis.source_dirs.append(dirname)
    
    for dirname in test_dirs:
        dirpath = root / dirname
        if dirpath.exists() and dirpath.is_dir():
            analysis.test_dirs.append(dirname)
    
    # Config files
    config_files = [
        ".env", ".env.example", ".env.local",
        "config.yaml", "config.yml", "config.json",
        ".github/workflows/",
        "docker-compose.yml", "Dockerfile",
        "Makefile", "Justfile",
        ".gitignore", ".dockerignore",
    ]
    
    for config_file in config_files:
        path = root / config_file
        if path.exists():
            analysis.config_files.append(config_file)
    
    # File patterns from .gitignore and similar
    gitignore = root / ".gitignore"
    if gitignore.exists():
        try:
            content = read_safe(gitignore).text
            # Extract patterns (non-comment, non-empty lines)
            patterns = [line.strip() for line in content.split('\n') 
                       if line.strip() and not line.startswith('#')]
            analysis.ignore_patterns.extend(patterns[:50])  # Limit
        except (OSError, UnicodeDecodeError):
            pass


async def _detect_entry_points(root: Path, analysis: CodebaseAnalysis, config: AnalysisConfig) -> None:
    """Detect entry points and main modules."""
    common_entry_points = [
        "main.py", "app.py", "index.py", "server.py",
        "cli.py", "__main__.py", "main.js", "index.js",
        "main.ts", "index.ts", "App.tsx", "main.rs",
        "main.go", "index.php", "artisan", "functions.php",
        "main.c", "main.cpp", "Main.java",
    ]

    for entry in common_entry_points:
        path = root / entry
        if path.exists():
            analysis.entry_points.append(entry)

    # Check for package __main__.py files
    try:
        py_files = _find_files(root, "__main__.py", config)
        for py_file in py_files:
            rel_path = py_file.relative_to(root).as_posix()
            if rel_path not in analysis.entry_points:
                analysis.entry_points.append(rel_path)
    except (OSError, PermissionError):
        pass
    
    # Sort
    analysis.entry_points.sort()


async def _detect_env_setup(root: Path, analysis: CodebaseAnalysis, config: AnalysisConfig) -> None:
    """Detect environment setup and common patterns."""
    # Check for .env files
    env_files = [".env", ".env.example", ".env.local", ".env.development", ".env.test"]
    for env_file in env_files:
        path = root / env_file
        if path.exists():
            try:
                content = read_safe(path).text
                # Extract variable names
                for line in content.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        var_name = line.split('=')[0].strip()
                        if var_name and var_name not in analysis.env_vars:
                            analysis.env_vars.append(var_name)
            except (OSError, UnicodeDecodeError):
                pass
    
    # Check for setup instructions in README
    readme = root / "README.md"
    if readme.exists():
        try:
            content = read_safe(readme).text
            # Look for setup sections
            setup_sections = ["Setup", "Installation", "Getting Started", "Development", "Build"]
            for section in setup_sections:
                if section.lower() in content.lower():
                    analysis.setup_steps.append(section)
        except (OSError, UnicodeDecodeError):
            pass
    
    # Sort
    analysis.env_vars.sort()
    analysis.setup_steps.sort()


async def _detect_code_standards(root: Path, analysis: CodebaseAnalysis, config: AnalysisConfig) -> None:
    """Detect coding standards and conventions."""
    # Linters and formatters
    linter_configs = [
        (".eslintrc", "ESLint"),
        (".eslintrc.js", "ESLint"),
        (".eslintrc.json", "ESLint"),
        ("eslint.config.js", "ESLint"),
        ("eslint.config.mjs", "ESLint"),
        (".prettierrc", "Prettier"),
        (".prettierrc.json", "Prettier"),
        ("mypy.ini", "mypy"),
        (".mypy.ini", "mypy"),
        ("ruff.toml", "ruff"),
        (".pylintrc", "pylint"),
        (".stylelintrc", "stylelint"),
        ("tsconfig.json", "TypeScript"),
        (".black.toml", "black"),
        (".php-cs-fixer.php", "PHP-CS-Fixer"),
        (".php-cs-fixer.dist.php", "PHP-CS-Fixer"),
        ("phpcs.xml", "PHP_CodeSniffer"),
        ("phpcs.xml.dist", "PHP_CodeSniffer"),
        (".phpcs.xml", "PHP_CodeSniffer"),
        ("phpstan.neon", "PHPStan"),
        ("phpstan.neon.dist", "PHPStan"),
        (".editorconfig", "EditorConfig"),
    ]

    for config_file, standard in linter_configs:
        path = root / config_file
        if path.exists() and standard not in analysis.coding_standards:
            analysis.coding_standards.append(standard)

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = read_safe(pyproject).text
            if "[tool.ruff" in content and "ruff" not in analysis.coding_standards:
                analysis.coding_standards.append("ruff")
            if "[tool.black" in content and "black" not in analysis.coding_standards:
                analysis.coding_standards.append("black")
            if "[tool.mypy" in content and "mypy" not in analysis.coding_standards:
                analysis.coding_standards.append("mypy")
            if "[tool.pylint" in content and "pylint" not in analysis.coding_standards:
                analysis.coding_standards.append("pylint")
            if "[tool.isort" in content and "isort" not in analysis.coding_standards:
                analysis.coding_standards.append("isort")
        except (OSError, UnicodeDecodeError):
            pass
    
    # Naming conventions from code analysis — sample several Python files (not just
    # the first one, whose contents would otherwise decide all conventions).
    try:
        py_files = _find_files(root, "*.py", config, limit=10)
        for py_file in py_files:
            if py_file.is_file():
                content = read_safe(py_file).text

                # Check for type hints
                if "def " in content and ":" in content:
                    if "-> " in content:
                        analysis.naming_conventions.append("Type hints")
                    if "from typing import " in content:
                        analysis.naming_conventions.append("Modern typing")

                # Check for class naming
                if re.search(r'class\s+[A-Z][a-zA-Z0-9]+', content):
                    analysis.naming_conventions.append("PascalCase classes")

                # Check for function naming
                if re.search(r'def\s+[a-z][a-z0-9_]+', content):
                    analysis.naming_conventions.append("snake_case functions")
    except (OSError, PermissionError):
        pass
    
    # Sort and deduplicate
    analysis.coding_standards.sort()
    analysis.coding_standards = list(dict.fromkeys(analysis.coding_standards))
    analysis.naming_conventions.sort()
    analysis.naming_conventions = list(dict.fromkeys(analysis.naming_conventions))
