from __future__ import annotations

from enum import Enum

from vibe.setup.init.analyzer import CodebaseAnalysis


class GenerationMode(Enum):
    """Mode for generating AGENTS.md content."""

    CREATE = "create"  # Create new file
    SUGGEST = "suggest"  # Suggest improvements to existing
    REVIEW = "review"  # Interactive review before writing


def generate_agents_md(
    analysis: CodebaseAnalysis,
    mode: GenerationMode = GenerationMode.CREATE,
    existing_content: str = "",
) -> str:
    """Generate AGENTS.md content based on codebase analysis.

    Args:
        analysis: Codebase analysis results
        mode: Generation mode (create new, suggest improvements, or interactive review)
        existing_content: Existing AGENTS.md content for suggestion mode

    Returns:
        Generated AGENTS.md content
    """
    if mode == GenerationMode.SUGGEST and existing_content.strip():
        return _generate_suggestions(analysis, existing_content)
    
    return _generate_full_agents_md(analysis)


def _generate_full_agents_md(analysis: CodebaseAnalysis) -> str:
    """Generate complete AGENTS.md content."""
    lines = []
    
    # Header
    project_name = analysis.project_name or "Project"
    lines.append(f"# {project_name} Development Guidelines")
    lines.append("")
    
    if analysis.project_description:
        lines.append(f"{analysis.project_description}")
        lines.append("")
    
    if analysis.project_version:
        lines.append(f"Version: {analysis.project_version}")
        lines.append("")
    
    # Languages and Frameworks
    if analysis.languages:
        lines.append("## Languages")
        lines.append("")
        lines.append(", ".join(analysis.languages))
        lines.append("")
    
    if analysis.frameworks:
        lines.append("## Frameworks")
        lines.append("")
        lines.append(", ".join(analysis.frameworks))
        lines.append("")
    
    # Build and Test Commands
    if analysis.build_commands or analysis.test_commands or analysis.lint_commands or analysis.run_commands:
        lines.append("## Commands")
        lines.append("")
        
        if analysis.build_commands:
            lines.append("### Build")
            lines.append("")
            for cmd in analysis.build_commands:
                lines.append(f"- `{cmd}`")
            lines.append("")
        
        if analysis.test_commands:
            lines.append("### Test")
            lines.append("")
            for cmd in analysis.test_commands:
                lines.append(f"- `{cmd}`")
            lines.append("")
        
        if analysis.run_commands:
            lines.append("### Run")
            lines.append("")
            for cmd in analysis.run_commands:
                lines.append(f"- `{cmd}`")
            lines.append("")
        
        if analysis.lint_commands:
            lines.append("### Lint/Format")
            lines.append("")
            for cmd in analysis.lint_commands:
                lines.append(f"- `{cmd}`")
            lines.append("")
    
    # Project Structure
    if analysis.source_dirs or analysis.test_dirs:
        lines.append("## Project Structure")
        lines.append("")
        
        if analysis.source_dirs:
            lines.append(f"- **Source directories**: {', '.join(analysis.source_dirs)}")
        if analysis.test_dirs:
            lines.append(f"- **Test directories**: {', '.join(analysis.test_dirs)}")
        lines.append("")
    
    # Coding Standards
    if analysis.coding_standards or analysis.naming_conventions:
        lines.append("## Coding Standards")
        lines.append("")
        
        if analysis.coding_standards:
            lines.append(f"- **Linters/Formatters**: {', '.join(analysis.coding_standards)}")
        if analysis.naming_conventions:
            lines.append(f"- **Naming conventions**: {', '.join(analysis.naming_conventions)}")
        lines.append("")
    
    # Package Management
    if analysis.package_managers:
        lines.append("## Package Management")
        lines.append("")
        lines.append(f"- Package managers: {', '.join(analysis.package_managers)}")
        lines.append("")
    
    # Entry Points
    if analysis.entry_points:
        lines.append("## Entry Points")
        lines.append("")
        for entry in analysis.entry_points:
            lines.append(f"- `{entry}`")
        lines.append("")
    
    # Environment Variables
    if analysis.env_vars:
        lines.append("## Environment Variables")
        lines.append("")
        lines.append("Common environment variables used in the project:")
        for var in analysis.env_vars:
            lines.append(f"- `{var}`")
        lines.append("")
    
    # Git Workflows
    if analysis.has_git:
        lines.append("## Git Workflows")
        lines.append("")
        if analysis.git_workflows:
            for workflow in analysis.git_workflows:
                lines.append(f"- Uses {workflow}")
        else:
            lines.append("- Standard Git workflow")
        lines.append("")
    
    # General Development Guidelines
    lines.append("## Development Guidelines")
    lines.append("")
    
    workflow_lines = []

    language_guidelines: dict[str, list[str]] = {
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
    }
    # TypeScript shares the Node.js workflow; C++ shares the CMake workflow.
    language_guidelines["typescript"] = language_guidelines["javascript"]
    language_guidelines["c++"] = language_guidelines["c"]

    # A detected framework is the strongest signal of the primary language —
    # e.g. a WordPress theme is alphabetically "JavaScript, PHP" but is really a
    # PHP project. Promote the framework's language ahead of the sorted list.
    framework_language = {
        "wordpress": "php", "laravel": "php", "symfony": "php",
        "django": "python", "flask": "python", "fastapi": "python",
        "react": "javascript", "vue": "javascript", "angular": "javascript",
        "next.js": "javascript", "express": "javascript", "nestjs": "javascript",
    }
    ordered = [lang.lower() for lang in analysis.languages]
    for fw in analysis.frameworks:
        primary = framework_language.get(fw.lower())
        if primary and primary in ordered:
            ordered.remove(primary)
            ordered.insert(0, primary)
            break

    # Use the first candidate language that has known guidelines.
    for detected in ordered:
        if guidelines := language_guidelines.get(detected):
            workflow_lines = list(guidelines)
            break

    if not workflow_lines:
        workflow_lines.extend([
            "- Run tests before committing",
            "- Format code consistently",
            "- Review changes with linting tools",
            "- Follow existing code patterns",
        ])
    
    for line in workflow_lines:
        lines.append(line)
    lines.append("")
    
    # AI Agent Instructions
    lines.append("## AI Agent Instructions")
    lines.append("")
    lines.append("When working with this codebase:")
    
    agent_instructions = [
        "- Follow the existing code style and patterns",
        "- Use the project's build and test commands",
        "- Respect the project structure and organization",
        "- Add tests for new functionality",
        "- Update documentation when making changes",
    ]
    
    if analysis.source_dirs:
        src_dirs = ", ".join(analysis.source_dirs)
        agent_instructions.append(f"- New code should go in: {src_dirs}")
    
    if analysis.test_dirs:
        test_dirs = ", ".join(analysis.test_dirs)
        agent_instructions.append(f"- Tests should go in: {test_dirs}")
    
    if analysis.config_files:
        agent_instructions.append(f"- Check {', '.join(analysis.config_files[:3])} for project-specific settings")
    
    for instruction in agent_instructions:
        lines.append(instruction)
    lines.append("")
    
    # Footer
    lines.append("---")
    lines.append("")
    lines.append("Generated by Mistral Vibe `/init` command.")
    lines.append("Refine this file with project-specific instructions and conventions.")
    
    return "\n".join(lines)


def _generate_suggestions(analysis: CodebaseAnalysis, existing_content: str) -> str:
    """Generate suggestions for improving existing AGENTS.md."""
    lines = [
        "## Suggested Improvements for AGENTS.md",
        "",
        "Based on codebase analysis, consider adding:",
        "",
    ]
    
    # Check what's missing
    missing_sections = []
    
    # Check for commands
    if not _section_exists(existing_content, r"Commands|Build|Test|Run"):
        if analysis.build_commands or analysis.test_commands:
            missing_sections.append(
                "**Commands**: Add build and test commands section with:\n" +
                "\n".join(f"  - `{cmd}`" for cmd in analysis.build_commands[:3] + analysis.test_commands[:3])
            )
    
    # Check for project structure
    if not _section_exists(existing_content, r"Structure|Organization"):
        if analysis.source_dirs or analysis.test_dirs:
            missing_sections.append(
                f"**Project Structure**: Document source dirs: {', '.join(analysis.source_dirs) if analysis.source_dirs else 'N/A'}"
            )
    
    # Check for coding standards
    if not _section_exists(existing_content, r"Standards|Conventions|Style"):
        if analysis.coding_standards:
            missing_sections.append(
                f"**Coding Standards**: Mention linters: {', '.join(analysis.coding_standards)}"
            )
    
    # Check for entry points
    if not _section_exists(existing_content, r"Entry|Main"):
        if analysis.entry_points:
            missing_sections.append(
                f"**Entry Points**: List entry points: {', '.join(analysis.entry_points[:3])}"
            )
    
    # Check for languages/frameworks
    if not _section_exists(existing_content, r"Language|Framework|Stack"):
        if analysis.languages or analysis.frameworks:
            tech_stack = []
            if analysis.languages:
                tech_stack.append(f"Languages: {', '.join(analysis.languages)}")
            if analysis.frameworks:
                tech_stack.append(f"Frameworks: {', '.join(analysis.frameworks)}")
            if tech_stack:
                missing_sections.append("**Technology Stack**: " + "; ".join(tech_stack))
    
    if missing_sections:
        for section in missing_sections:
            lines.append(f"- {section}")
            lines.append("")
    else:
        lines.append("The existing AGENTS.md looks comprehensive!")
        lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append("Suggestions generated by Mistral Vibe `/init` command.")
    
    return "\n".join(lines)


def _section_exists(content: str, patterns: str) -> bool:
    """Check if any pattern exists in content as a markdown section header."""
    import re
    header_patterns = patterns.split("|")
    for pattern in header_patterns:
        if re.search(rf"^#{1,6}\s+.*{pattern}", content, re.MULTILINE | re.IGNORECASE):
            return True
    return False
