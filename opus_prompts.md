<instruction>
    <role>
        You are an Elite Prompt Engineer and Software Architect.
    </role>
    <task>
        I have analyzed your request and the codebase. The "Draft Prompts" you provided were designed for a greenfield project, but the provided codebase is already quite advanced.

        Therefore, I have **rewritten and optimized** the prompts to be **Context-Aware**. instead of asking you to create things that exist, they ask you to **integrate** and **polish** the existing components.

        Here is the optimized `opus_prompts.md` which now includes a **dedicated section** for the "ChefChat" UI Branding in the onboarding wizard.
    </task>
</instruction>

```markdown
# 👨‍🍳 ChefChat Development Menu (Context-Aware Edition)

These prompts are optimized for the **current state** of the `mistral-vibe` codebase.
**Instruction:** Serve these prompts one by one to Claude Opus 4.5.

-----

## 🥗 PROMPT 1: The Grand Entrance (Onboarding UI)

```xml
<instruction>
    <role>
        You are the Head Chef (UI/UX Designer).
    </role>

    <context>
        The current onboarding wizard in `vibe/setup/onboarding` works, but it looks generic.
        It lacks the "Chef" spirit. The colors are plain, and the text is standard.
    </context>

    <objective>
        Reskin the Onboarding Wizard to fully embrace the "ChefChat" brand (Michelin-star aesthetic).
    </objective>

    <requirements>
        <branding>
            - **Colors**: Use the signature "Mistral Orange" (#FF7000) for highlights, borders of active elements, and the progress bar.
            - **Metaphors**: Change technical terms to culinary ones where appropriate (e.g., "Setup" -> "Mise en Place", "API Key" -> "Secret Sauce").
        </branding>

        <files>
            1. **`vibe/setup/onboarding/onboarding.tcss`**:
               - Update styles to use `#FF7000` for `.theme-item.selected` and `#input-box.valid`.
               - Give the `#welcome-text` a "premium" feel (maybe brighter border).

            2. **`vibe/setup/onboarding/screens/welcome.py`**:
               - Update the ASCII art (if any) or Welcome Text to say "ChefChat".
               - Change the welcome message to something like: "Welcome to the Kitchen. Let's get cooking."

            3. **`vibe/setup/onboarding/screens/theme_selection.py`**:
               - Rename the themes in the UI to be more thematic if possible (e.g., "Dark" -> "Sous-Vide Dark", "Light" -> "Meringue").
        </files>
    </requirements>

    <code_style>
        - **Textual**: Ensure the TUI remains responsive.
        - **Rich**: Use `[orange1]` tags in strings where `rich` markup is supported.
    </code_style>

    <task>
        Overhaul the visual style of the `vibe/setup/onboarding` package to match the premium ChefChat brand.
    </task>
</instruction>
```

-----

## 🍲 PROMPT 2: The Integration (REPL & Agent Wiring)

```xml
<instruction>
    <role>
        You are the Head Chef (Software Architect).
    </role>

    <context>
        We have a robust `ModeManager` in `vibe/cli/mode_manager.py` and a powerful `Agent` in `vibe/core/agent.py`.
        However, the current user interface `vibe/cli/repl.py` is just a mock-up. It echoes input but is NOT connected to the Agent.
        It prints: "Note: Full Agent integration coming in Phase 2".
    </context>

    <objective>
        Connect the `ChefChatREPL` to the `Agent`. We need real functionality now.
    </objective>

    <requirements>
        <file path="vibe/cli/repl.py">
            1. **Instantiate Agent**: inside `__init__`, initialize `self.agent` using `vibe.core.agent.Agent`.
               - Pass the `self.mode_manager` to the agent so it can enforce mode safety.
            2. **Event Loop**: In `run()`, replace the "echo" logic with a real call to `self.agent.act(user_input)`.
            3. **Streaming**: Use `rich.Live` to stream the `AssistantEvent` content in real-time. It must feel like a premium terminal chat.
            4. **Error Handling**: Catch `GracefulExit` or `KeyboardInterrupt` properly.
        </file>
    </requirements>

    <code_style>
        - **Rich UI**: Use `rich.markdown.Markdown` for rendering the AI response.
        - **Async**: The `repl.py` `run` method might need to become async or use `asyncio.run()`.
        - **Strictness**: Ensure `mode_manager` is passed correctly.
    </code_style>

    <task>
        Refactor `vibe/cli/repl.py` to fully integrate with `vibe/core/agent.py`.
        Remove the "Phase 2" placeholder text.
    </task>
</instruction>
```

-----

## 🥘 PROMPT 3: The Safety Net (Interactive Approval)

```xml
<instruction>
    <context>
        The `Agent` in `vibe/core/agent.py` calls `_ask_approval` when `NORMAL` mode receives a write request.
        Currently, `vibe/cli/repl.py` does not provide an `approval_callback` to the Agent.
    </context>

    <objective>
        Implement the "Waitor" logic: Interactive permission handling in the REPL.
    </objective>

    <logic>
        1. **Callback Design**: Create a method `async def ask_user_approval(tool_name, args, call_id) -> ToolDecision` in `repl.py`.
        2. **UI Implementation**:
           - When called, pause the spinner/stream.
           - Display a "Order Confirmation" panel (Red/Orange border) showing:
             - Tool Name
             - Arguments (syntax highlighted)
             - "Allow this action? [Y/n/always]"
           - Return the user's decision to the Agent.
        3. **Wiring**: Pass this callback when initializing `Agent` in Prompt 2.
    </logic>

    <refinement>
        Check `vibe/core/agent.py`. Ensure `_should_execute_tool` correctly respects `YOLO` (Auto-Approve) and `PLAN` (Block Write) modes.
        (Note: `mode_manager.should_block_tool` already handles PLAN blocking, you just need to ensure the UI handles the "Ask" case).
    </refinement>

    <task>
        Update `vibe/cli/repl.py` to implement the approval callback and pass it to the Agent.
    </task>
</instruction>
```

-----

## 📜 PROMPT 4: The Palette (Mode-Specific System Prompts)

```xml
<instruction>
    <context>
        `vibe/cli/mode_manager.py` already contains `get_system_prompt_modifier`.
        `vibe/core/system_prompt.py` already calls it.
        This provides the "Flavor" (System Instructions) for each mode.
    </context>

    <objective>
        Audit and Refine the XML System Prompts for maximum strictness ("Michelin Standard").
    </objective>

    <tasks>
        <task_1>
            Review `vibe/cli/mode_manager.py`.
            Look at `get_system_prompt_modifier()`.
            Ensure the XML tags `<mode_rules>` and `<active_mode>` are extremely clear.
        </task_1>
        <task_2>
            **Enhancement**: Add a "Response Format" instruction for `YOLO` mode.
            - It currently says "ULTRA-CONCISE".
            - Add: "In YOLO mode, if a task is successful, output valid JSON: `{'status': 'success', 'changed_files': [...]}` inside a `<yolo_result>` tag if possible, or just the checkmark."
            - (Optional: Adjust based on user preference for JSON vs Text).
        </task_2>
    </tasks>

    <task>
        Open `vibe/cli/mode_manager.py` and refine the prompt strings in `get_system_prompt_modifier` to be even more robust and authoritative.
    </task>
</instruction>
```

-----

## 🍰 PROMPT 5: The Front of House (Entrypoint & CLI Args)

```xml
<instruction>
    <context>
        We have `vibe/cli/entrypoint.py` which currently launches the TUI (Textual UI) by default.
        We have built the new `ChefChatREPL` in `vibe/cli/repl.py`.
    </context>

    <objective>
        Update the main entry point to expose the new REPL.
    </objective>

    <requirements>
        1. Add a `--repl` or `--classic` flag to the `main` command in `vibe/cli/entrypoint.py`.
        2. If passed, verify `rich` and `prompt_toolkit` are installed.
        3. Launch `run_repl(config, initial_mode=...)`.
        4. Ensure the TUI remains the default (for now) OR switch default if the user prefers.
    </requirements>

    <consistency_check>
        Ensure `vibe/core/config.py` is loaded correctly before launching either UI.
    </consistency_check>

    <task>
        Modify `vibe/cli/entrypoint.py` to support the new REPL mode.
    </task>
</instruction>
```

-----

## � PROMPT 6: The Taste Test (Verification)

```xml
<instruction>
    <role>
        The Food Critic.
    </role>

    <objective>
        Verify the complete flow.
    </objective>

    <checklist>
        1. **Onboarding**: Run `vibe --setup` (or however onboarding triggers). Does the new Orange branding show?
        2. **Startup**: Run `vibe --repl`. Does the banner appear?
        3. **Mode Cycle**: Press `Shift+Tab`. Does it cycle PLAN -> NORMAL -> AUTO...?
        4. **Safety**: Switch to **PLAN**. Try to write a file. Is it blocked?
        5. **Execution**: Switch to **YOLO**. Try to write a file. Does it happen instantly?
    </checklist>

    <task>
        Create a manual verification script `scripts/verify_chef_modes.py` or describe the manual testing steps in `tests/MANUAL_TESTING.md`.
    </task>
</instruction>
```
```
