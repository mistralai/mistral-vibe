#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use schemars::JsonSchema;

use crate::core::error::CoreError;
use crate::core::step_protocol::Action;
use crate::core::step_protocol::DeterminismContext;
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::result::invalid_tool_call_result;
use crate::core::tools::schema::ToolSchema;
use crate::core::wire::tool::ToolCall;

use super::context::ProgramContext;
use super::execution::start_program_execution;
use super::{CompletedProgramResult, ProgramOutcome};

pub(crate) const RUN_TYPESCRIPT_NAME: &str = "run_typescript";
const RUN_TYPESCRIPT_DESCRIPTION: &str = r#"
Executes TypeScript code in a sandbox environment.

## How to use run_typescript

The `run_typescript` tool executes TypeScript code in a sandbox environment. This sandbox has access to ES2015 - ES2022 language features, but doesn't have access to NodeJS, browser APIs, or network I/O.

### Sandbox capabilities and durability model

- The sandbox is a replayable TypeScript workflow environment, not a general-purpose Node.js runtime. Use it for orchestration, tool-call wiring, and local data transformation.
- It supports standard JavaScript/TypeScript computation such as async/await, Promises, JSON, Array/Object/Map/Set operations, RegExp, URL parsing, TextEncoder/TextDecoder, and console output for debugging.
- It does not provide ambient filesystem access, Node.js packages, browser APIs, DOM APIs, or direct network I/O. Reach external systems only through the provided `tools.<group>.<function>` functions.
- Execution may pause while tool calls are pending and later replay from the beginning. Local code can therefore run more than once; keep it to pure orchestration, pure data transforms, and tool-call wiring.
- Time, randomness, and promise-selection APIs are recorded automatically and replay deterministically. This includes `Date()`, `new Date()`, `Date.now()`, `performance.now()`, `Math.random()`, `crypto.randomUUID()`, `crypto.getRandomValues()`, `Promise.race()`, and `Promise.any()`.

### The `main` function

**IMPORTANT:** The sandboxed environment expects a `main` function that will be run automatically.
- You MUST always define an `async function main() { /* ... */ }` containing all your code
- You MUST NOT call `main()` yourself - the sandbox calls it automatically
- The result of the `main` function will be visible as a { type: "success", value } object.
- The input must contain inline `code`.
- Tool calls may throw. Use `try/catch` or `.catch(...)` to recover or keep partial results; otherwise let errors propagate to the tool result.
- AVOID using console.log or console.error to read the output of functions. Rely on the main function's returned value instead. Only use console for debugging.
- For independent calls, prefer `Promise.allSettled(...)`. Use `Promise.all(...)` only when all must succeed; avoid sequential awaits when calls can run concurrently.
- You MUST NOT use `fetch()`, `setInterval()`, `atob()`, `btoa()`, `DOMParser`, `Buffer`, `require()`, `import()`, or `export` — they do not exist. Tool functions are already available under the `tools` namespace.
- When a string contains double quotes, you MUST use single quotes for the outer string.
  WRONG: `await tools.web_search.web_search({ query: ""Sample Topic" TV date" });`
  CORRECT: `await tools.web_search.web_search({ query: '"Sample Topic" TV date' });`

### Available tool functions

Tool functions are available under `tools.<group>.<function>`; no imports needed.

Before calling an unfamiliar tool function, use `search_tool_functions` to load details for the exact function name. Use only documented function paths and argument shapes.


### Example

1. Discover the appropriate tool function signature. Assuming you found the following type definition:

```typescript
declare namespace tools.someGroup {
  function someFunction(args: { timestamp: number, someQuery: string }): Promise<{ someOutput: number }>;
}
```

2. Run TypeScript code. You can call `run_typescript` with code like:

```json
{"code": "async function main() {\n  const timestamp = Date.now();\n  const { someOutput } = await tools.someGroup.someFunction({ timestamp, someQuery: '...' });\n  return someOutput;\n}"}
```

"#;

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct RunTypeScriptArguments {
    code: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ToolName {
    RunTypeScript,
}

impl ToolName {
    const ALL: [Self; 1] = [Self::RunTypeScript];

    pub(crate) fn direct_name(self) -> &'static str {
        match self {
            Self::RunTypeScript => RUN_TYPESCRIPT_NAME,
        }
    }

    fn definition(self) -> ToolDefinition {
        match self {
            Self::RunTypeScript => ToolDefinition {
                name: self.direct_name().to_string(),
                description: RUN_TYPESCRIPT_DESCRIPTION.to_string(),
                parameters: RunTypeScriptArguments::tool_schema(),
            },
        }
    }
}

pub(crate) fn direct_tools() -> Vec<ToolDefinition> {
    ToolName::ALL
        .into_iter()
        .map(ToolName::definition)
        .collect()
}

fn tool_name(name: &str) -> Option<ToolName> {
    ToolName::ALL
        .into_iter()
        .find(|tool_name| tool_name.direct_name() == name)
}

pub(crate) fn is_direct_name(name: &str) -> bool {
    tool_name(name).is_some()
}

pub(crate) fn is_private_wrapper(name: &str) -> bool {
    tool_name(name) == Some(ToolName::RunTypeScript)
}

pub(crate) fn dispatch(
    context: ProgramContext<'_>,
    call: &ToolCall,
    determinism: DeterminismContext,
) -> Option<Result<(ProgramOutcome, Vec<Action>), CoreError>> {
    let name = tool_name(&call.name)?;
    let result = match name {
        ToolName::RunTypeScript => start_program_execution(context, call, determinism),
    };
    Some(match result {
        Ok(outcome) => Ok(outcome),
        Err(error) => invalid_tool_call_outcome(error.detail().to_string()),
    })
}

fn invalid_tool_call_outcome(reason: String) -> Result<(ProgramOutcome, Vec<Action>), CoreError> {
    Ok((
        ProgramOutcome::Completed(Box::new(CompletedProgramResult {
            result: invalid_tool_call_result(reason),
        })),
        Vec::new(),
    ))
}
