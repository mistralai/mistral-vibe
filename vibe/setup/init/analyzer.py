from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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
        "build", "dist", "target", "out", "bin", "obj", "logs"
    })
    exclude_files: set[str] = field(default_factory=lambda: {
        "*.log", "*.pyc", "*.swp", "*.swo", ".DS_Store", "Thumbs.db"
    })


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
        _detect_language_framework(root, analysis),
        _detect_structure(root, analysis, analysis_config),
        _detect_entry_points(root, analysis, analysis_config),
        _detect_env_setup(root, analysis, analysis_config),
        _detect_code_standards(root, analysis, analysis_config),
    ]
    
    await asyncio.gather(*tasks)
    
    # Deduplicate and clean up
    analysis.build_commands = list(set(analysis.build_commands))
    analysis.test_commands = list(set(analysis.test_commands))
    analysis.languages = list(set(analysis.languages))
    analysis.frameworks = list(set(analysis.frameworks))
    
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
            for pattern in ["*.github/workflows/*.yml", ".gitlab-ci.yml", ".gitignore"]:
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
    
    # Check for README files for project description
    for readme in ["README.md", "README.txt", "readme.md"]:
        readme_path = root / readme
        if readme_path.exists():
            try:
                content = read_safe(readme_path).text
                # First non-empty line as potential description
                if not analysis.project_description:
                    first_line = content.split('\n')[0].strip()
                    if first_line and first_line != f"#{analysis.project_name}":
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
        analysis.package_managers.append("pip")
        analysis.package_managers.append("uv")  # Modern alternative
        
        # Check for common Python build commands
        for cmd in ["pip install -e .", "python -m build", "uv sync"]:
            analysis.build_commands.append(cmd)
        
        for cmd in ["pytest", "python -m pytest", "uv run pytest"]:
            analysis.test_commands.append(cmd)
            
        for cmd in ["mypy", "ruff check", "black .", "isort ."]:
            analysis.lint_commands.append(cmd)
    
    if requirements.exists():
        analysis.build_commands.append("pip install -r requirements.txt")
        analysis.build_commands.append("uv pip install -r requirements.txt")
    
    # Node.js/npm
    package_json = root / "package.json"
    if package_json.exists():
        analysis.package_managers.append("npm")
        analysis.package_managers.append("yarn")
        analysis.package_managers.append("pnpm")
        
        for cmd in ["npm install", "yarn install", "pnpm install"]:
            analysis.build_commands.append(cmd)
        
        for cmd in ["npm test", "yarn test", "pnpm test"]:
            analysis.test_commands.append(cmd)
            
        for cmd in ["npm run lint", "yarn lint", "pnpm lint"]:
            analysis.lint_commands.append(cmd)
        
        for cmd in ["npm run build", "yarn build", "pnpm build"]:
            analysis.build_commands.append(cmd)
    
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
    
    # JavaScript/TypeScript tools
    if package_json.exists():
        try:
            import json
            with open(package_json, encoding="utf-8") as f:
                data = json.load(f)
                scripts = data.get("scripts", {})
                for script_name in scripts:
                    full_cmd = f"npm run {script_name}"
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


async def _detect_language_framework(root: Path, analysis: CodebaseAnalysis) -> None:  # pylint: disable=too-many-branches,too-many-nested-blocks
    """Detect languages and frameworks."""
    # Check for language-specific files
    file_indicators: dict[str, list[str]] = {
        "Python": ["*.py", "pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "JavaScript": ["*.js", "*.mjs", "package.json", "node_modules"],
        "TypeScript": ["*.ts", "tsconfig.json"],
        "Rust": ["*.rs", "Cargo.toml"],
        "Go": ["*.go", "go.mod"],
        "Java": ["*.java", "pom.xml", "build.gradle"],
        "C++": ["*.cpp", "*.h", "*.hpp", "CMakeLists.txt"],
        "C#": ["*.cs", "*.sln", "*.csproj"],
        "Ruby": ["*.rb", "Gemfile"],
        "PHP": ["*.php", "composer.json"],
        "Swift": ["*.swift", "Package.swift"],
        "Kotlin": ["*.kt", "*.kts"],
        "Shell": ["*.sh"],
        "Docker": ["Dockerfile", "docker-compose.yml"],
    }
    
    for lang, indicators in file_indicators.items():
        for indicator in indicators:
            if indicator.startswith("*."):
                # File extension
                ext = indicator[1:]  # Remove *.
                try:
                    if list(root.rglob(f"*.{ext}")):
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
                    py_files = list(root.rglob("*.py"))[:50]  # Limit for performance
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
            ("React", ["package.json"], ["react", "React"]),
            ("Vue", ["package.json"], ["vue", "Vue"]),
            ("Angular", ["angular.json", "package.json"], ["@angular"]),
            ("Next.js", ["next.config.js", "next.config.mjs"], ["next", "Next.js"]),
            ("Express", ["package.json"], ["express", "Express"]),
            ("NestJS", ["nest-cli.json", "package.json"], ["@nestjs"]),
        ]
        
        for framework, files, _imports in frameworks_to_check:
            if any((root / f).exists() for f in files):
                # Check if framework is in package.json dependencies
                pkg_json = root / "package.json"
                if pkg_json.exists():
                    try:
                        import json
                        with open(pkg_json, encoding="utf-8") as f:
                            data = json.load(f)
                            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                            for _, patterns in [("react", ["react"]), ("vue", ["vue"]), ("next", ["next"])]:
                                for pattern in patterns:
                                    if pattern in deps or any(p.startswith(pattern) for p in deps):
                                        analysis.frameworks.append(framework)
                                        break
                    except (json.JSONDecodeError, OSError, ImportError):
                        pass
    
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
        "main.ts", "index.ts", "App.tsx", "main.rs"
    ]
    
    for entry in common_entry_points:
        path = root / entry
        if path.exists():
            analysis.entry_points.append(entry)
    
    # Check for package __main__.py files
    try:
        py_files = list(root.rglob("__main__.py"))
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
        (".prettierrc", "Prettier"),
        ("mypy.ini", "mypy"),
        ("pyproject.toml", "ruff/black"),
        (".pylintrc", "pylint"),
        (".stylelintrc", "stylelint"),
        ("tsconfig.json", "TypeScript"),
        ("tslint.json", "TSLint"),
    ]
    
    for config_file, standard in linter_configs:
        path = root / config_file
        if path.exists():
            analysis.coding_standards.append(standard)
    
    # Naming conventions from code analysis
    # Check Python files for common patterns
    try:
        py_files = list(root.rglob("*.py"))[:50]  # Limit for performance
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
                    
                break  # Just sample first file for performance
    except (OSError, PermissionError):
        pass
    
    # Sort and deduplicate
    analysis.coding_standards.sort()
    analysis.coding_standards = list(dict.fromkeys(analysis.coding_standards))
    analysis.naming_conventions.sort()
    analysis.naming_conventions = list(dict.fromkeys(analysis.naming_conventions))
