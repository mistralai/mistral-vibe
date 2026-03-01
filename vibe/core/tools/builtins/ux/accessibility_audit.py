"""UX Designer tool: static accessibility audit (WCAG 2.1)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
import re
from typing import TYPE_CHECKING, ClassVar, final

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class AccessibilityAuditArgs(BaseModel):
    path: str = Field(
        default=".",
        description="Path to HTML file or directory to audit.",
    )
    wcag_level: str = Field(
        default="AA",
        description="WCAG level to check (A, AA, AAA).",
    )


class AccessibilityAuditResult(BaseModel):
    path: str
    issues: list[str] = Field(default_factory=list)
    passed: list[str] = Field(default_factory=list)
    score: float = Field(description="Accessibility score 0-100.")
    summary: str = Field(description="Brief summary of the audit.")


class AccessibilityAuditConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    check_aria: bool = Field(default=True, description="Check ARIA usage.")
    min_contrast_ratio: float = Field(
        default=4.5,
        description="Minimum contrast ratio for AA (4.5 for normal text).",
    )


def _audit_html(content: str, path: str) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    passed: list[str] = []

    # Img without alt
    imgs = re.findall(r"<img[^>]*>", content, re.IGNORECASE)
    for img in imgs:
        if "alt=" not in img.lower():
            issues.append("Image missing alt attribute")
        else:
            passed.append("Images have alt attributes")

    # Form inputs without labels
    inputs = re.findall(r"<input[^>]*>", content, re.IGNORECASE)
    for inp in inputs:
        if "type=" in inp and "hidden" not in inp.lower():
            inp_id = re.search(r'id=["\']([^"\']+)["\']', inp, re.IGNORECASE)
            if inp_id and f'for="{inp_id.group(1)}"' not in content and f"for='{inp_id.group(1)}'" not in content:
                if "aria-label" not in inp.lower() and "aria-labelledby" not in inp.lower():
                    issues.append("Form input may lack accessible label")
                    break
    if inputs and "Form input may lack accessible label" not in issues:
        passed.append("Form inputs appear labeled")

    # Headings
    h1_count = len(re.findall(r"<h1[^>]*>", content, re.IGNORECASE))
    if h1_count == 0:
        issues.append("Page missing h1 heading")
    elif h1_count > 1:
        issues.append("Multiple h1 headings (consider single main heading)")
    else:
        passed.append("Single h1 heading present")

    # Landmarks / semantic
    if "<main" in content or 'role="main"' in content:
        passed.append("Main landmark present")
    else:
        issues.append("Consider adding <main> or role=main")

    # Lang attribute
    if re.search(r"<html[^>]*lang\s*=", content, re.IGNORECASE):
        passed.append("Document has lang attribute")
    else:
        issues.append("Document missing lang attribute on <html>")

    # Buttons/links
    buttons = re.findall(r"<button[^>]*>[\s]*</button>", content, re.IGNORECASE)
    for _ in buttons:
        issues.append("Empty button - ensure aria-label or visible text")
        break

    return (issues, passed)


class AccessibilityAudit(
    BaseTool[
        AccessibilityAuditArgs,
        AccessibilityAuditResult,
        AccessibilityAuditConfig,
        BaseToolState,
    ],
    ToolUIData[AccessibilityAuditArgs, AccessibilityAuditResult],
):
    description: ClassVar[str] = (
        "Run a static accessibility audit on HTML files. "
        "Checks for WCAG 2.1 issues: alt text, labels, headings, landmarks, lang."
    )

    @final
    async def run(
        self, args: AccessibilityAuditArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | AccessibilityAuditResult, None]:
        base = Path(args.path).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base

        if not base.exists():
            raise ToolError(f"Path not found: {base}")

        all_issues: list[str] = []
        all_passed: list[str] = []
        files_checked = 0

        paths_to_check: list[Path] = []
        if base.is_file():
            if base.suffix.lower() in {".html", ".htm", ".xhtml"}:
                paths_to_check.append(base)
        else:
            for p in base.rglob("*.html"):
                paths_to_check.append(p)
            for p in base.rglob("*.htm"):
                paths_to_check.append(p)

        for path in paths_to_check[:20]:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                all_issues.append(f"{path}: read error - {exc}")
                continue

            issues, passed = _audit_html(content, str(path))
            all_issues.extend(f"{path.name}: {i}" for i in issues)
            all_passed.extend(passed)
            files_checked += 1

        all_issues = list(dict.fromkeys(all_issues))
        all_passed = list(dict.fromkeys(all_passed))

        total = len(all_issues) + len(all_passed)
        score = 100.0 * len(all_passed) / total if total else 100.0

        summary = (
            f"Audited {files_checked} file(s). "
            f"{len(all_issues)} issue(s), {len(all_passed)} check(s) passed."
        )

        yield AccessibilityAuditResult(
            path=str(base),
            issues=all_issues,
            passed=all_passed,
            score=round(score, 1),
            summary=summary,
        )

    def resolve_permission(self, args: AccessibilityAuditArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @classmethod
    def format_call_display(cls, args: AccessibilityAuditArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Auditing accessibility: {args.path}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, AccessibilityAuditResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )
        return ToolResultDisplay(
            success=True,
            message=f"Score: {event.result.score}/100 - {event.result.summary}",
            warnings=event.result.issues[:3] if event.result.issues else [],
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Auditing accessibility"
