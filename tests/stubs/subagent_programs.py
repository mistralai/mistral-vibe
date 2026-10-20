"""The model-written programs tests use to drive subagents.

The subagent tools these programs call are in one place, so a change to
their names or to how a parent collects a subagent's result changes this
file only.
"""

from __future__ import annotations

import json

SUBAGENT_TOOLS = "tools.agents"
"""The namespace of the subagent tools in a ``run_typescript`` program."""

SUBAGENT_NAME = "name"
"""The argument that names a subagent."""


_WAIT_FOR_CHILD = """\
  // Spawn does not wait; poll until the child's Turn ends.
  let child;
  for (let attempt = 0; attempt < 150; attempt++) {{
    const {{agents}} = await {tools}.list({{}});
    child = agents.find((agent) => agent.name === {name});
    if (child && child.status !== "running") break;
    await tools.self.sleep({{seconds: 0.2}});
  }}"""


def spawn_and_wait(name: str, message: str) -> str:
    """A program that spawns subagent ``name`` with ``message`` and returns its
    listing once its Turn ends, leaving it idle.
    """
    named = f"{SUBAGENT_NAME}: {json.dumps(name)}"
    return f"""
async function main() {{
  await {SUBAGENT_TOOLS}.spawn({{{named}, message: {json.dumps(message)}}});
{_WAIT_FOR_CHILD.format(tools=SUBAGENT_TOOLS, name=json.dumps(name))}
  return {{child}};
}}
"""


def spawn_only(name: str, message: str) -> str:
    """A program that spawns subagent ``name`` with ``message`` and returns at
    once, leaving it running.
    """
    named = f"{SUBAGENT_NAME}: {json.dumps(name)}"
    return f"""
async function main() {{
  await {SUBAGENT_TOOLS}.spawn({{{named}, message: {json.dumps(message)}}});
  return "spawned";
}}
"""


def spawn_two(first: str, second: str, message: str) -> str:
    """A program that spawns subagents ``first`` and ``second``, each with
    ``message``, and returns at once, leaving both running.
    """
    spawns = "\n".join(
        f"  await {SUBAGENT_TOOLS}.spawn({{{SUBAGENT_NAME}: {json.dumps(name)},"
        f" message: {json.dumps(message)}}});"
        for name in (first, second)
    )
    return f"""
async function main() {{
{spawns}
  return "spawned";
}}
"""
