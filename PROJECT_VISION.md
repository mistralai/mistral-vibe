# ChefChat: The Michelin Star AI-Engineer (Technical Masterplan - Definitive Edition)

## 🌟 The Vision: "Beyond the Chatbot"
ChefChat transforms the Command Line Interface from a passive text stream into a **High-Performance Culinary Dashboard**. We are building the world's first **Asynchronous Swarm-based AI Engineer** with a reactive TUI (Terminal User Interface). This is not a tool; it is a specialized team of autonomous agents working for you.

---

## 🍳 Core Philosophy: "The Kitchen Metaphor"
We strictly adhere to this metaphor to guide architectural decisions:
*   **The User** = **Head Chef** (Vision, Final Decision, "The Taste").
*   **The AI** = **The Brigade** (A team of specialized agents working in parallel).
*   **The Code** = **The Dish** (Must be robust, performant, and beautiful).
*   **The Project** = **The Restaurant** (Has a specific style, menu, and clientele).
*   **Context** = **Mise en place** (Everything in its place, prepped before cooking starts).

---

## 🗺️ Phase 1: The "Michelin Star" Visual Identity (UI/UX & TUI)
*Goal: A stunning, immersive environment that feels like a professional workspace, eliminating CLI fatigue.*

### 1.1 The Visual Language: "Dark Kitchen Aesthetic"
We abandon standard terminal colors for a bespoke, high-contrast palette designed for long coding sessions.
*   **Palette:** "Slate & Saffron"
    *   **Backgrounds:** Deep Charcoal (`#1a1b26`) for depth.
    *   **Panel Borders:** Muted Steel (`#414868`) for structure.
    *   **Accents:** Saffron Gold (`#e0af68`) for active states/warnings.
    *   **Success:** Sage Green (`#9ece6a`) for passed tests.
    *   **Error:** Pomegranate Red (`#f7768e`) for failures/bugs.
    *   **Info:** Foam Blue (`#7dcfff`) for system notifications.
    *   **Text:** Storm Grey (`#a9b1d6`) for readability.

### 1.2 The Layout (The TUI Dashboard)
We utilize `textual` (built on `rich`) to create a persistent, reactive 3-pane dashboard. The screen does not scroll away; it updates in place.

```text
+---------------------------------------------------------------+
|  👨‍🍳 ChefChat v2.0 - Omakase Mode   |   Project: ChefChat    | [Status: 🟢 Online]
+---------------------------------------------------------------+
|  1. THE TICKET (Chat/Input)    |  2. THE PASS (Live Activity) |
|                                |                              |
|  User: "Refactor auth.py       |  🔪 Station: Prep            |
|  to use JWT instead of         |  > Parsing dependency tree...|
|  sessions."                    |  [||||||||||] 100%           |
|                                |                              |
|  Chef: "Oui, Chef. I see       |  🔥 Station: Sommelier       |
|  dependency issues in          |  > Fetching PyJWT 2.8 docs...|
|  login(). Fixing now..."       |                              |
|                                |  🔥 Station: Rotisseur       |
|                                |  > Generating test_auth.py   |
|                                |                              |
|  [ Input Field ]               |  🥡 Station: Expeditor       |
|  > _                           |  > Linting check: PASSED     |
+--------------------------------+------------------------------+
|  3. THE PLATE (Output/Diff View)                              |
|                                                               |
|  file: auth.py                                                |
|  @@ -12,4 +12,5 @@                                            |
|   class Auth:                                                 |
|  -    def login(self):                                        |
|  +    def login(self, token: str):                            |
|           # Validated by Health Inspector                     |
|                                                               |
+---------------------------------------------------------------+
|  [Shortcuts: ^C Cancel | ^R Recipes | ^T Taste Test]          |
+---------------------------------------------------------------+
```

### 1.3 Micro-Interactions & Animations
*   **The "Whisk" Loader:** A custom ASCII spinner that simulates whisking, used for low-latency thinking states.
*   **The "Flambé" Transition:** Smooth, GPU-accelerated (where supported) fading effects when switching views or completing tasks.
*   **Smart Syntax Highlighting:** Not just generic Python coloring, but context-aware highlighting (e.g., highlighting variable names that were just changed).

---

## 📜 Phase 2: "Recipes" & Workflow Architecture
*Goal: Standardization of complex tasks. Move from "Prompt Engineering" to "Process Engineering".*

### 2.1 The Recipe Schema (`.chef/recipes/*.yaml`)
Recipes are declarative workflows that chain multiple agents and steps together. They are version-controlled and shareable.

**Structure:**
*   **Name & Description:** Clear identifiers.
*   **Ingredients (Context):** What files/docs are strictly required? (e.g., "schema.prisma", "api_docs.md").
*   **Steps:** A sequence of actions assigned to specific stations.
*   **Plating (Output):** How should the result be presented? (Diff, Pull Request, File Write).

**Example Recipe:**
```yaml
name: "Refactor to SOLID"
description: "Analyzes a class, breaks it down into interfaces, and generates tests."
ingredients:
  - "target_file"
steps:
  - station: "health-inspector"
    action: "analyze_complexity"
    output: "complexity_report"
  - station: "sous-chef"
    action: "plan_refactor"
    input: "complexity_report"
    strategy: "interface_segregation"
  - station: "line-cook"
    action: "generate_code"
  - station: "expeditor"
    action: "verify_tests"
serving_suggestion: "Review the interface definitions first."
```

### 2.2 The Command Interface
*   `chef prep [files]`: Analyze and index files without changing them. Updates the knowledge graph.
*   `chef cook [recipe]`: Execute a predefined workflow.
    *   *Example:* `chef cook refactor-auth --target user_service.py`
*   `chef taste`: Run the project's test suite against the *current* staging area (not yet committed).
*   `chef plate`: Finalize changes, format code, and commit/push to Git.

---

## 🐝 Phase 3: "The Brigade" (Swarm Intelligence Architecture)
*Goal: UNPARALLELED SPEED and QUALITY via parallelism. Why wait for one model to do everything?*

### 3.1 The Actor Model Architecture
We implement an Actor Model where specialized agents run in their own threads/processes, communicating via a central Message Bus.

*   **👨‍🍳 Chef de Cuisine (The Orchestrator):**
    *   *Role:* The Manager. Parses the user prompt, breaks it into "Tickets", and assigns them to stations. Maintains the high-level plan.
*   **🍷 The Sommelier (Library/Docs Specialist):**
    *   *Role:* The Researcher.
    *   *Action:* If the code uses `FastAPI`, the Sommelier instantly fetches the latest FastAPI docs and "pours" relevant snippets into the Line Cook's context window.
*   **🔪 The Sous-Chef (Architect):**
    *   *Role:* The Planner.
    *   *Action:* Does NOT write code. Creates the pseudocode structure and interface definitions. Ensures the "Big Picture" is logical.
*   **🔥 The Line Cook (Code Generator):**
    *   *Role:* The Builder.
    *   *Action:* Writes the actual implementation based *strictly* on the Sous-Chef's blueprints.
*   **🕵️ The Health Inspector (Security/QA):**
    *   *Role:* The Auditor.
    *   *Action:* Runs *during* generation. Uses static analysis (AST parsing) to catch security risks (SQLi, hardcoded secrets) before the code is even shown to the user.
*   **🧹 The Plongeur (The Cleaner):**
    *   *Role:* Maintenance.
    *   *Action:* Runs in the background to clean up unused imports, fix whitespace, and auto-format code (Black/Ruff) constantly.

### 3.2 Parallel Execution Pipeline
While the **Line Cook** is implementing `function_A`, the **Sommelier** is fetching context for `function_B`, and the **Sous-Chef** is reviewing the plan for `function_C`. This pipeline approach drastically reduces wait times.

---

## 👅 Phase 4: "The Taste Test" (Hyper-Configurable QA)
*Goal: The only AI that refuses to serve bad code. Quality is enforced by system rules, not user vigilance.*

### 4.1 The Palate Configuration (`.chef/palate.toml`)
Users define the "Definition of Done" for their kitchen (project).

```toml
[palate]
strictness = "michelin" # options: diner, bistro, brasserie, michelin, bocuse

[palate.rules]
no_print_statements = true
require_type_hints = true
max_cognitive_complexity = 8
docstring_style = "google"
forbidden_imports = ["pdb", "os.system"]

[palate.testing]
auto_run = true
framework = "pytest"
min_coverage = 90
fail_on_warning = true
```

### 4.2 The Auto-Correction Loop (Self-Healing)
1.  **Generate:** Line Cook creates code.
2.  **Taste:** System runs `pytest` and `ruff` (linter) in a secure sandbox.
3.  **Refine:** If errors exist, the output (stderr) is fed back to the Line Cook with the error log.
    *   *Iteration:* The Line Cook attempts to fix it.
4.  **Serve:** Only when tests pass (or max retries reached) is the code presented to the user.
5.  **Optimistic Serving (Optional):** Display the code immediately with a "UNVERIFIED" badge. As background tests pass, the badge flips to "APPROVED" ✅ in real-time.

---

## 🎓 Phase 5: "The Masterclass" (Adaptive Learning & Memory)
*Goal: The tool gets smarter the more you use it. Unlocking true uniqueness.*

### 5.1 Project Knowledge Graph (RAG on Steroids)
We don't just "read files". We parse the AST (Abstract Syntax Tree) to build a persistent graph database of the codebase.
*   **Nodes:** Classes, Functions, Variables, Files.
*   **Edges:** "Inherits From", "Calls", "Imports", "Instantiates".
*   **Benefit:** When you modify `User`, the Chef knows *exactly* which 12 other files depend on it without needing to `grep` or read the whole codebase.

### 5.2 The "Little Black Book" (User Preferences)
The Chef learns your style implicitly and explicitly.
*   **Implicit Learning:** "User rejected 3 suggestions containing `list()`. User prefers list comprehensions." -> *Update Weights*.
*   **Explicit Teaching:** `chef teach "Always use pathlib, never os.path"` -> *Stored in Vector Memory*.
*   **Project-Specific Rules:** "In this project, we use snake_case for everything, including classes (legacy)."

### 5.3 Session Retrospectives
After a coding session (`chef service end`), the system generates a markdown report:
*   **The Menu:** Summary of tasks completed.
*   **The Bill:** Technical Debt incurred/paid off.
*   **Next Service:** Suggestions for the next session based on TODOs found in code.

---

## 🍹 Phase Extra: "The Secret Menu" (10 Delightful & Unique Features)
*Goal: Features that make the user smile and wonder "How did I live without this?"*

1.  **"Mise en Place" Snapshot:**
    *   Before any major change, ChefChat automatically creates a hidden git tag/branch. `chef undo` is instant and guaranteed safe.
2.  **"Chef's Table" (Live Share):**
    *   Generate a read-only localhost URL where a colleague can watch the Chef work in real-time (CLI streaming to Web).
3.  **"The Special" (Daily Tip):**
    *   On startup, ChefChat analyzes your codebase and gives ONE actionable tip. "Did you know you have 3 different Date util libraries? Consolidate to `arrow`?"
4.  **"Leftovers" Manager (Snippets):**
    *   "Chef, save this regex for later." -> `chef fridge store regex_email`. Later: `chef fridge get regex_email`.
5.  **"Dietary Restrictions" (License Check):**
    *   Checks imports against a policy. "Warning: You are importing a GPL library into a MIT project. Are you sure?"
6.  **"Pairing Suggestions" (Library Recommender):**
    *   "You are using `FastAPI`. Would you like to add `Pydantic` and `Uvicorn` to the recipe?"
7.  **"Kitchen Noise" (Ambient Mode):**
    *   Play subtle, non-distracting lofi/ambient sounds via the CLI (optional) to induce flow state.
8.  **"The Critic" (Roast Mode):**
    *   `chef critic`: Generates a brutally honest, sarcastic code review of your project. Good for humbling yourself and finding hidden issues.
9.  **"Drive-Thru" (Natural Language Shell):**
    *   `chef drive "Undo the last 3 commits and force push"` -> Translates to complex git commands, shows them, asks for confirm, executes.
10. **"Inventory Check" (Dependency Health):**
    *   Visual dashboard of your `requirements.txt` / `package.json` showing age, vulnerability status, and major version drift.

---

## 🚀 Implementation Priority Strategy

1.  **Foundation (Day 1-2):** Setup `rich`/`textual` app structure, defined `Brigade` class, `Sous-Chef` system prompt.
2.  **Intelligence (Day 3-5):** Implement the Context Manager (Mise en place) and simple Model Router.
3.  **Workflow (Day 6-7):** Build the `Recipe` parser and YAML schema loader.
4.  **Visuals (Day 8+):** Polish the TUI, animations, and "Dark Kitchen" theme.
5.  **Expansion:** Add Agents one by one (Sommelier, Inspector, etc.).