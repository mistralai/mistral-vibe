"""🍽️ ChefChat Plating System - Redesigned
==========================================

The "Plating" feature presents your coding work like a chef plates a dish.
A beautiful, stylized summary of what was accomplished.

Features:
- /plate - Present the current work beautifully
- /recipe - Show the "recipe" (ingredients + steps) for a coding task
- /taste - Quick code taste test (review)

Each presentation is mode-aware and themed with professional kitchen energy!
"""

from __future__ import annotations

from datetime import UTC, datetime
import random
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from chefchat.cli.mode_manager import ModeManager
    from chefchat.core.agent import AgentStats

# Import the dark color palette
try:
    from chefchat.cli.ui_components import COLORS
except ImportError:
    # Fallback if ui_components not available
    COLORS = {
        "fire": "#FF7000",
        "gold": "#FFD700",
        "steel": "#8B9DC3",
        "sage": "#98C379",
        "ember": "#E06C75",
        "honey": "#E5C07B",
        "cream": "#E8E8E8",
        "silver": "#ABB2BF",
        "smoke": "#5C6370",
        "ash": "#3E4451",
    }


# Difficulty thresholds for recipe complexity
_EASY_STEPS_MAX = 3
_MEDIUM_STEPS_MAX = 6


# =============================================================================
# PLATING PRESENTATIONS - Present work like a finished dish
# =============================================================================

PRESENTATION_STYLES: dict[str, dict[str, str]] = {
    "plan": {
        "title": "📋 THE BLUEPRINT",
        "style": "Methodical presentation with clear structure",
        "chef_note": "As they say in the kitchen: *mise en place!*",
        "emoji": "🔪",
    },
    "normal": {
        "title": "🍽️ DAILY SPECIAL",
        "style": "Clean presentation, honest execution",
        "chef_note": "Solid work. Consistent quality.",
        "emoji": "✋",
    },
    "auto": {
        "title": "⚡ RAPID SERVICE",
        "style": "Fast plating, maximum throughput",
        "chef_note": "Hot and fast - just how we like it!",
        "emoji": "⚡",
    },
    "yolo": {
        "title": "🚀 CHEF'S SPECIAL",
        "style": "Bold presentation, no holds barred",
        "chef_note": "Send it! *chef's kiss*",
        "emoji": "🚀",
    },
    "architect": {
        "title": "🏛️ TASTING MENU",
        "style": "Elevated presentation, multi-course vision",
        "chef_note": "A symphony of design decisions.",
        "emoji": "🏛️",
    },
}


def generate_plating(
    mode_manager: ModeManager | None,
    stats: AgentStats | None = None,
    work_summary: str | None = None,
) -> Panel:
    """Generate a beautiful plating presentation using Rich.

    Args:
        mode_manager: Current mode for themed presentation
        stats: Agent stats for the metrics
        work_summary: Optional summary of what was accomplished

    Returns:
        Rich Panel with beautiful formatting
    """
    # Get mode-specific styling
    mode_name = mode_manager.current_mode.value if mode_manager else "normal"
    style = PRESENTATION_STYLES.get(mode_name, PRESENTATION_STYLES["normal"])

    # Current time for presentation
    now = datetime.now(UTC)
    time_str = now.strftime("%H:%M")

    # Get stats if available
    if stats:
        steps = str(stats.steps)
        tokens = f"{stats.session_total_llm_tokens:,}"
        cost = f"${stats.session_cost:.4f}"
        tool_calls = str(stats.tool_calls_succeeded)
    else:
        steps = "—"
        tokens = "—"
        cost = "—"
        tool_calls = "—"

    # Random plating flourish
    flourishes = [
        "✨ Drizzled with elegant abstractions",
        "🌟 Topped with a reduction of best practices",
        "💫 Garnished with type safety",
        "⭐ Finished with documentation",
        "🎯 Precision-placed with surgical accuracy",
        "🔮 Crystallized with pure logic",
    ]
    flourish = random.choice(flourishes)

    # Build the content
    content = Text()

    # Title section
    content.append(style["title"], style=f"bold {COLORS['fire']}")
    content.append("\n\n")

    # Service info
    content.append("🕐 Served at: ", style=COLORS['silver'])
    content.append(time_str, style=f"bold {COLORS['cream']}")
    content.append("\n")

    content.append("🍽️ Style: ", style=COLORS['silver'])
    content.append(style["style"], style=COLORS['cream'])
    content.append("\n\n")

    # Separator
    content.append("─" * 56, style=COLORS['ash'])
    content.append("\n\n")

    # Metrics table
    metrics_table = Table(show_header=False, box=None, padding=(0, 2))
    metrics_table.add_column("metric", style=COLORS['silver'], width=24)
    metrics_table.add_column("value", style=COLORS['cream'])

    metrics_table.add_row("📊 Preparations (steps)", steps)
    metrics_table.add_row("🔤 Ingredients (tokens)", tokens)
    metrics_table.add_row("💰 Kitchen cost", cost)
    metrics_table.add_row("🔧 Tools employed", tool_calls)

    # Combine all elements
    elements = [
        content,
        metrics_table,
        Text(),
        Text("─" * 56, style=COLORS['ash']),
        Text(),
        Text(flourish, style=f"italic {COLORS['lavender']}"),
        Text(),
        Text(),
    ]

    # Chef's note
    note = Text()
    note.append("👨‍🍳 Chef's Note: ", style=f"bold {COLORS['fire']}")
    note.append(style["chef_note"], style=f"italic {COLORS['silver']}")
    elements.append(note)

    # Work summary if provided
    if work_summary:
        elements.extend([
            Text(),
            Text(),
            Text("─" * 56, style=COLORS['ash']),
            Text(),
            Text("📝 What We Prepared", style=f"bold {COLORS['gold']}"),
            Text(),
            Text(work_summary, style=COLORS['cream']),
        ])

    return Panel(
        Group(*elements),
        title=f"[{COLORS['fire']}]{style['emoji']} Plating[/{COLORS['fire']}]",
        border_style=COLORS['gold'],
        box=box.ROUNDED,
        padding=(1, 2)
    )


# =============================================================================
# RECIPE GENERATOR - Show the "recipe" for a coding task
# =============================================================================


def generate_recipe(
    task_name: str,
    ingredients: list[str],
    steps: list[str],
    mode_manager: ModeManager | None = None,
    prep_time: str = "10 min",
    cook_time: str = "varies",
    serves: str = "the whole team",
) -> Panel:
    """Generate a recipe-style breakdown of a coding task.

    Args:
        task_name: Name of the feature/fix
        ingredients: List of files/dependencies needed
        steps: Implementation steps
        mode_manager: For mode-aware styling
        prep_time: Planning time estimate
        cook_time: Implementation time estimate
        serves: Who benefits from this

    Returns:
        Rich Panel with recipe formatting
    """
    mode_name = mode_manager.current_mode.value if mode_manager else "normal"

    # Difficulty based on steps
    if len(steps) <= _EASY_STEPS_MAX:
        difficulty = "🟢 Easy"
    elif len(steps) <= _MEDIUM_STEPS_MAX:
        difficulty = "🟡 Medium"
    else:
        difficulty = "🔴 Advanced"

    # Build content
    content = Text()

    # Metadata table
    meta_table = Table(show_header=False, box=None, padding=(0, 2))
    meta_table.add_column("label", style=f"bold {COLORS['silver']}", width=15)
    meta_table.add_column("value", style=COLORS['cream'])

    meta_table.add_row("⏱️ Prep Time", prep_time)
    meta_table.add_row("🍳 Cook Time", cook_time)
    meta_table.add_row("🍽️ Serves", serves)
    meta_table.add_row("📊 Difficulty", difficulty)

    # Ingredients section
    ingredients_text = Text()
    ingredients_text.append("🥗 Ingredients\n\n", style=f"bold {COLORS['gold']}")
    for ing in ingredients:
        ingredients_text.append(f"  • {ing}\n", style=COLORS['silver'])

    # Steps section
    steps_text = Text()
    steps_text.append("\n👨‍🍳 Method\n\n", style=f"bold {COLORS['gold']}")
    for i, step in enumerate(steps, 1):
        steps_text.append(f"  {i}. ", style=f"bold {COLORS['fire']}")
        steps_text.append(f"{step}\n", style=COLORS['cream'])

    # Random chef tip
    tips = [
        "Always taste your tests before serving to production!",
        "Let your code rest before the final review - fresh eyes catch bugs!",
        "A watched CI/CD pipeline never finishes... but refresh anyway.",
        "When in doubt, add more types.",
        "The secret ingredient is always error handling.",
        "Mise en place: organize your imports before cooking!",
    ]
    tip = random.choice(tips)

    tip_text = Text()
    tip_text.append("\n💡 Chef's Tip\n\n", style=f"bold {COLORS['honey']}")
    tip_text.append(f"  {tip}", style=f"italic {COLORS['silver']}")

    # Footer
    footer_text = Text()
    footer_text.append(f"\nRecipe from the ChefChat Kitchen • Mode: {mode_name.upper()}",
                      style=COLORS['smoke'])

    # Combine all elements
    elements = [
        meta_table,
        Text(),
        Text("─" * 56, style=COLORS['ash']),
        Text(),
        ingredients_text,
        steps_text,
        tip_text,
        Text(),
        Text("─" * 56, style=COLORS['ash']),
        footer_text,
    ]

    return Panel(
        Group(*elements),
        title=f"[{COLORS['fire']}]📖 Recipe: {task_name}[/{COLORS['fire']}]",
        border_style=COLORS['steel'],
        box=box.ROUNDED,
        padding=(1, 2)
    )


# =============================================================================
# TASTE TEST - Quick code review
# =============================================================================

TASTE_VERDICTS = [
    ("⭐⭐⭐⭐⭐", "EXCEPTIONAL", "This code is *chef's kiss*! Michelin-worthy."),
    ("⭐⭐⭐⭐", "EXCELLENT", "Almost perfect! Just needs a touch more."),
    ("⭐⭐⭐", "GOOD", "Solid work. Gets the job done well."),
    ("⭐⭐", "NEEDS WORK", "The ingredients are there, but needs refinement."),
    ("⭐", "BACK TO BASICS", "Let's revisit this from scratch."),
]

TASTE_ASPECTS = {
    "readability": [
        "Clear as a consommé 🍜",
        "Easy to follow like a well-written recipe 📖",
        "Could use some clarifying comments 💭",
        "A bit like reading hieroglyphics 🤔",
        "My eyes are watering like cutting onions 🧅",
    ],
    "structure": [
        "Perfectly layered like a mille-feuille 🥐",
        "Well-organized mise en place 📋",
        "Some ingredients out of place 🥄",
        "Like a kitchen after dinner rush 🌪️",
        "Needs a complete reorganization 📦",
    ],
    "efficiency": [
        "Runs like a well-oiled wok 🥘",
        "Efficient as a professional kitchen ⚡",
        "Some slow spots in the service 🐢",
        "Could use some optimization 🌿",
        "Burning through resources 🔥",
    ],
    "maintainability": [
        "A recipe anyone could follow 👨‍🍳👩‍🍳",
        "Well-documented like a cookbook 📚",
        "Some secret ingredients undocumented 🤫",
        "Inherited family recipe, unclear origins 👴",
        "Only the original chef understands this 🧙",
    ],
}


def generate_taste_test(
    code_snippet: str | None = None,
    file_path: str | None = None,
    mode_manager: ModeManager | None = None,
    severity: int | None = None,  # 1-5, None for random
) -> Panel:
    """Generate a fun taste test (code review) report.

    Args:
        code_snippet: Code being reviewed (optional)
        file_path: File being reviewed (optional)
        mode_manager: For mode-aware commentary
        severity: Override the review severity (1-5)

    Returns:
        Rich Panel with taste test report
    """
    # Random severity if not specified
    if severity is None:
        # Weight towards positive reviews (we're encouraging!)
        severity = random.choices([1, 2, 3, 4, 5], weights=[5, 10, 30, 35, 20])[0]

    stars, verdict, description = TASTE_VERDICTS[5 - severity]

    # Random aspects
    aspects = {
        aspect: random.choice(comments) for aspect, comments in TASTE_ASPECTS.items()
    }

    # Mode-specific commentary
    mode_name = mode_manager.current_mode.value if mode_manager else "normal"
    mode_notes = {
        "plan": "📋 *In planning mode - being thorough with the review.*",
        "normal": "✋ *Standard taste test - checking all the bases.*",
        "auto": "⚡ *Quick taste - looks good, let's move!*",
        "yolo": "🚀 *LGTM ship it! ...but maybe run the tests first.*",
        "architect": "🏛️ *Looking at the high-level flavor profile.*",
    }
    mode_note = mode_notes.get(mode_name, "")

    # Build report header
    if file_path:
        header_text = f"Tasting: {file_path}"
    elif code_snippet:
        preview = code_snippet[:50].replace("\n", " ") + "..."
        header_text = f"Tasting: {preview}"
    else:
        header_text = "General Kitchen Inspection"

    # Build content
    content = Text()

    # Header
    content.append(header_text, style=f"bold {COLORS['cream']}")
    content.append("\n\n")
    content.append("─" * 56, style=COLORS['ash'])
    content.append("\n\n")

    # Overall rating
    content.append("Overall Rating\n\n", style=f"bold {COLORS['gold']}")
    content.append(stars, style=COLORS['honey'])
    content.append(f" {verdict}\n", style=f"bold {COLORS['fire']}")
    content.append(f"{description}\n\n", style=f"italic {COLORS['silver']}")

    content.append("─" * 56, style=COLORS['ash'])
    content.append("\n\n")

    # Flavor profile table
    content.append("Flavor Profile\n\n", style=f"bold {COLORS['gold']}")

    profile_table = Table(show_header=False, box=None, padding=(0, 1))
    profile_table.add_column("aspect", style=f"bold {COLORS['silver']}", width=18)
    profile_table.add_column("notes", style=COLORS['cream'])

    profile_table.add_row("📖 Readability", aspects["readability"])
    profile_table.add_row("🏗️ Structure", aspects["structure"])
    profile_table.add_row("⚡ Efficiency", aspects["efficiency"])
    profile_table.add_row("🔧 Maintainability", aspects["maintainability"])

    # Footer
    footer = Text()
    if mode_note:
        footer.append(f"\n{mode_note}\n", style=f"italic {COLORS['smoke']}")
    footer.append("\nTaste test by Chef's AI • Not a substitute for real code review!",
                 style=COLORS['smoke'])

    # Combine elements
    elements = [
        content,
        profile_table,
        footer,
    ]

    return Panel(
        Group(*elements),
        title=f"[{COLORS['fire']}]🍽️ Taste Test Results[/{COLORS['fire']}]",
        border_style=COLORS['honey'],
        box=box.ROUNDED,
        padding=(1, 2)
    )


# =============================================================================
# KITCHEN TIMER - Time estimates
# =============================================================================


def estimate_cooking_time(task_description: str) -> dict[str, Any]:
    """Estimate time for a coding task in cooking terms.

    Args:
        task_description: Description of the task

    Returns:
        Dict with time estimates and cooking metaphor
    """
    desc_lower = task_description.lower()

    # Simple heuristics
    if any(w in desc_lower for w in ["bug", "fix", "typo", "small"]):
        return {
            "prep_time": "5 min",
            "cook_time": "10-15 min",
            "total": "15-20 min",
            "metaphor": "🥪 Quick sandwich",
            "tip": "Quick fix - but don't rush the testing!",
        }
    elif any(w in desc_lower for w in ["feature", "add", "new", "implement"]):
        return {
            "prep_time": "15-30 min",
            "cook_time": "1-2 hours",
            "total": "1.5-2.5 hours",
            "metaphor": "🍝 Full pasta dinner",
            "tip": "Take time to plan the architecture before diving in.",
        }
    elif any(w in desc_lower for w in ["refactor", "rewrite", "migrate", "upgrade"]):
        return {
            "prep_time": "30-60 min",
            "cook_time": "2-4 hours",
            "total": "2.5-5 hours",
            "metaphor": "🦃 Holiday feast",
            "tip": "This is a big one - break it into smaller courses!",
        }
    elif any(w in desc_lower for w in ["design", "architect", "plan", "research"]):
        return {
            "prep_time": "1-2 hours",
            "cook_time": "depends",
            "total": "ongoing",
            "metaphor": "📖 Writing the cookbook",
            "tip": "Good planning saves cooking time later!",
        }
    else:
        return {
            "prep_time": "15 min",
            "cook_time": "30-60 min",
            "total": "45 min - 1.5 hours",
            "metaphor": "🍲 Hearty stew",
            "tip": "Taste as you go - incremental progress is key!",
        }


def format_kitchen_timer(task_description: str) -> Panel:
    """Format a kitchen timer display for a task.

    Args:
        task_description: What needs to be done

    Returns:
        Rich Panel with timer display
    """
    est = estimate_cooking_time(task_description)

    # Build content
    content = Text()

    content.append("Task: ", style=f"bold {COLORS['silver']}")
    content.append(f"{task_description}\n\n", style=COLORS['cream'])

    content.append("─" * 56, style=COLORS['ash'])
    content.append("\n\n")

    # Time table
    time_table = Table(show_header=False, box=None, padding=(0, 2))
    time_table.add_column("label", style=f"bold {COLORS['silver']}", width=18)
    time_table.add_column("value", style=COLORS['cream'])

    time_table.add_row("🔪 Prep Time", est["prep_time"])
    time_table.add_row("🍳 Cook Time", est["cook_time"])
    time_table.add_row("⏱️ Total Time", est["total"])
    time_table.add_row("🍽️ Dish Type", est["metaphor"])

    # Chef's tip
    tip = Text()
    tip.append("\n\n💡 Chef's Tip\n\n", style=f"bold {COLORS['honey']}")
    tip.append(est["tip"], style=f"italic {COLORS['silver']}")

    # Footer
    footer = Text()
    footer.append("\n\nEstimates based on complexity heuristics. Actual time may vary!",
                 style=COLORS['smoke'])
    footer.append("\nRemember: good code takes time to simmer. 🍲",
                 style=f"italic {COLORS['smoke']}")

    elements = [
        content,
        time_table,
        tip,
        footer,
    ]

    return Panel(
        Group(*elements),
        title=f"[{COLORS['fire']}]⏱️ Kitchen Timer[/{COLORS['fire']}]",
        border_style=COLORS['honey'],
        box=box.ROUNDED,
        padding=(1, 2)
    )


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "PRESENTATION_STYLES",
    "estimate_cooking_time",
    "format_kitchen_timer",
    "generate_plating",
    "generate_recipe",
    "generate_taste_test",
]
