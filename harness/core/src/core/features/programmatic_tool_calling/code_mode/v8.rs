use std::ffi::c_void;
use std::sync::Once;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use deno_ast::EmitOptions;
use deno_ast::MediaType;
use deno_ast::ModuleItemRef;
use deno_ast::ModuleSpecifier;
use deno_ast::ParseParams;
use deno_ast::ParsedSource;
use deno_ast::SourceRangedForSpanned;
use deno_ast::StartSourcePos;
use deno_ast::TextChange;
use deno_ast::TranspileModuleOptions;
use deno_ast::TranspileOptions;
use deno_ast::apply_text_changes;
use deno_ast::parse_program;
use deno_ast::swc::ast::Callee;
use deno_ast::swc::ast::Expr;
use deno_ast::swc::ast::MemberProp;
use deno_ast::swc::ast::Stmt;
use deno_ast::swc::ast::UnaryOp;
use deno_core::v8;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;

use super::CodeResult;
use super::EvaluationOutcome as TypeScriptRunResult;
use super::EvaluationRequest;
#[cfg(test)]
use crate::core::features::programmatic_tool_calling::ProgrammaticName;
use crate::core::features::programmatic_tool_calling::TypeScriptTool;
use crate::core::features::programmatic_tool_calling::model::PartialEvaluation;
use crate::core::features::programmatic_tool_calling::model::ToolState;
#[cfg(test)]
use crate::core::features::programmatic_tool_calling::model::{ToolFunction, ToolKind};
use crate::core::step_protocol::DeterminismContext;

static INITIALIZE_V8: Once = Once::new();
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(2);
const INITIAL_HEAP_BYTES: usize = 4 * 1024 * 1024;
const MAX_HEAP_BYTES: usize = 16 * 1024 * 1024;

struct HeapLimitMonitor {
    limit_reached: AtomicBool,
    isolate_handle: v8::IsolateHandle,
}

struct HeapLimitCallbackGuard<'i, 'm> {
    isolate: &'i mut v8::OwnedIsolate,
    monitor: &'m HeapLimitMonitor,
}

impl<'i, 'm> HeapLimitCallbackGuard<'i, 'm> {
    fn new(isolate: &'i mut v8::OwnedIsolate, monitor: &'m HeapLimitMonitor) -> Self {
        let data = std::ptr::from_ref(monitor).cast_mut().cast::<c_void>();
        isolate.add_near_heap_limit_callback(terminate_on_near_heap_limit, data);

        Self { isolate, monitor }
    }

    fn isolate_mut(&mut self) -> &mut v8::OwnedIsolate {
        self.isolate
    }

    fn limit_reached(&self) -> bool {
        self.monitor.limit_reached.load(Ordering::SeqCst)
    }
}

impl Drop for HeapLimitCallbackGuard<'_, '_> {
    fn drop(&mut self) {
        self.isolate
            .remove_near_heap_limit_callback(terminate_on_near_heap_limit, MAX_HEAP_BYTES);
    }
}

unsafe extern "C" fn terminate_on_near_heap_limit(
    data: *mut c_void,
    current_heap_limit: usize,
    _initial_heap_limit: usize,
) -> usize {
    // SAFETY: `data` is the pointer registered by `HeapLimitCallbackGuard::new`.
    // The guard borrows the monitor for as long as the callback is registered
    // and unregisters it in `Drop`, so the monitor outlives every invocation.
    // The callback reads `isolate_handle` and writes `limit_reached`; the write
    // is sound through a shared reference because it is an `AtomicBool`.
    let monitor = unsafe { &*data.cast::<HeapLimitMonitor>() };
    monitor.limit_reached.store(true, Ordering::SeqCst);
    monitor.isolate_handle.terminate_execution();

    // V8 crashes the process if the callback does not increase the limit.
    // Give termination enough headroom to unwind the current allocation.
    current_heap_limit.saturating_mul(2)
}

struct ExecutionWatchdog {
    stop_tx: mpsc::Sender<()>,
    thread: Option<thread::JoinHandle<()>>,
}

impl ExecutionWatchdog {
    fn start(isolate_handle: v8::IsolateHandle, timeout: Duration) -> Self {
        let (stop_tx, stop_rx) = mpsc::channel();
        let thread = thread::spawn(move || {
            if stop_rx.recv_timeout(timeout).is_err() {
                isolate_handle.terminate_execution();
            }
        });
        Self {
            stop_tx,
            thread: Some(thread),
        }
    }
}

impl Drop for ExecutionWatchdog {
    fn drop(&mut self) {
        let _ = self.stop_tx.send(());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

macro_rules! v8_exception_result {
    ($scope:expr, $fallback:expr) => {{
        if $scope.has_terminated() || $scope.is_execution_terminating() {
            Err("TypeScript execution exceeded the process safety deadline".to_string())
        } else {
            let message = $scope
                .exception()
                .and_then(|exception| exception.to_string($scope))
                .map(|message| message.to_rust_string_lossy($scope))
                .unwrap_or_else(|| $fallback.to_string());
            Ok(error_result("ExecutionError", message))
        }
    }};
}

pub(super) fn evaluate(request: EvaluationRequest<'_>) -> Result<TypeScriptRunResult, String> {
    run_typescript(
        request.partial_evaluation,
        request.tools,
        request.execution_id,
        request.determinism,
        request.max_program_effects,
        request.max_program_operations,
    )
}

fn run_typescript(
    partial_evaluation: &PartialEvaluation,
    tools: &[TypeScriptTool],
    execution_id: &str,
    determinism: DeterminismContext,
    max_program_effects: u32,
    max_program_operations: u32,
) -> Result<TypeScriptRunResult, String> {
    run_typescript_with_timeout(
        partial_evaluation,
        tools,
        execution_id,
        determinism,
        max_program_effects,
        max_program_operations,
        DEFAULT_TIMEOUT,
    )
}

fn run_typescript_with_timeout(
    partial_evaluation: &PartialEvaluation,
    tools: &[TypeScriptTool],
    execution_id: &str,
    determinism: DeterminismContext,
    max_program_effects: u32,
    max_program_operations: u32,
    timeout: Duration,
) -> Result<TypeScriptRunResult, String> {
    let transpiled = match transpile(&partial_evaluation.code) {
        Ok(code) => code,
        Err(error) => return Ok(error_result("SyntaxError", error)),
    };
    let mut evaluation = partial_evaluation.clone();
    for _ in 0..=max_program_operations {
        let source = match build_source(
            &evaluation,
            tools,
            execution_id,
            &transpiled,
            determinism,
            max_program_effects,
            max_program_operations,
        ) {
            Ok(source) => source,
            Err(error) => return Ok(error_result("SerializationError", error)),
        };

        initialize_v8();
        let params = v8::CreateParams::default().heap_limits(INITIAL_HEAP_BYTES, MAX_HEAP_BYTES);
        let mut isolate = v8::Isolate::new(params);
        let isolate_handle = isolate.thread_safe_handle();
        let heap_limit_monitor = HeapLimitMonitor {
            limit_reached: AtomicBool::new(false),
            isolate_handle: isolate_handle.clone(),
        };
        let mut heap_limit_callback =
            HeapLimitCallbackGuard::new(&mut isolate, &heap_limit_monitor);
        let _watchdog = ExecutionWatchdog::start(isolate_handle, timeout);
        let result = execute(heap_limit_callback.isolate_mut(), &source);
        let heap_limit_reached = heap_limit_callback.limit_reached();
        drop(heap_limit_callback);
        if heap_limit_reached {
            return Ok(memory_limit_result(evaluation.tool_state));
        }
        let result = result?;
        let TypeScriptRunResult::PartialEvaluation {
            mut partial_evaluation,
        } = result
        else {
            return Ok(result);
        };
        let validation = reject_invalid_pending_tools(&mut partial_evaluation.tool_state, tools);
        if !validation.rejected_any || validation.has_valid_pending {
            return Ok(TypeScriptRunResult::PartialEvaluation { partial_evaluation });
        }
        evaluation = partial_evaluation;
    }
    Ok(error_result(
        "ExecutionError",
        "run_typescript exceeded the validation replay limit",
    ))
}

#[derive(Default)]
struct PendingValidation {
    rejected_any: bool,
    has_valid_pending: bool,
}

fn reject_invalid_pending_tools(
    tool_state: &mut [ToolState],
    tools: &[TypeScriptTool],
) -> PendingValidation {
    let mut result = PendingValidation::default();
    for state in tool_state {
        let ToolState::Pending { kind, id, function } = state else {
            continue;
        };
        let Some(tool) = tools.iter().find(|tool| tool.name == function.name) else {
            result.has_valid_pending = true;
            continue;
        };
        let Some(error) = validate_tool_arguments(tool, &function.arguments) else {
            result.has_valid_pending = true;
            continue;
        };
        *state = ToolState::Rejected {
            kind: kind.clone(),
            id: id.clone(),
            function: function.clone(),
            error,
            content: Vec::new(),
        };
        result.rejected_any = true;
    }
    result
}

fn validate_tool_arguments(tool: &TypeScriptTool, arguments: &Value) -> Option<Value> {
    let mut schema = serde_json::Map::from_iter([
        (
            "$schema".to_string(),
            json!("http://json-schema.org/draft-07/schema#"),
        ),
        ("type".to_string(), json!("object")),
    ]);
    for keyword in ["properties", "required", "additionalProperties"] {
        if let Some(value) = tool.input_schema.get(keyword) {
            schema.insert(keyword.to_string(), value.clone());
        }
    }
    let validator = jsonschema::draft7::options()
        .should_validate_formats(true)
        .build(&Value::Object(schema))
        .ok()?;
    let error = validator.validate(arguments).err()?;
    let errors = format_ajv_validation_errors(error);
    Some({
        json!({
            "type": "jsonSchemaArgumentValidationError",
            "functionName": tool.name,
            "argumentsValidationErrors": errors,
        })
    })
}

fn format_ajv_validation_errors(error: jsonschema::ValidationError<'_>) -> Vec<Value> {
    use jsonschema::error::ValidationErrorKind;

    let instance_path = error.instance_path().to_string();
    let schema_path = format!("#{}", error.schema_path());
    let keyword = error.kind().keyword();
    let (params, message) = ajv_error_details(error.kind(), &error);
    match error.kind() {
        ValidationErrorKind::AdditionalProperties { unexpected } => unexpected
            .iter()
            .take(1)
            .map(|property| {
                json!({
                    "instancePath": instance_path,
                    "schemaPath": schema_path,
                    "keyword": keyword,
                    "params": {"additionalProperty": property},
                    "message": "must NOT have additional properties",
                })
            })
            .collect(),
        ValidationErrorKind::Required { property } => vec![json!({
            "instancePath": instance_path,
            "schemaPath": schema_path,
            "keyword": keyword,
            "params": {"missingProperty": property},
            "message": format!("must have required property '{}'", property.as_str().unwrap_or_default()),
        })],
        _ => vec![json!({
            "instancePath": instance_path,
            "schemaPath": schema_path,
            "keyword": keyword,
            "params": params,
            "message": message,
        })],
    }
}

fn ajv_error_details(
    kind: &jsonschema::error::ValidationErrorKind,
    error: &jsonschema::ValidationError<'_>,
) -> (Value, String) {
    use jsonschema::error::TypeKind;
    use jsonschema::error::ValidationErrorKind;

    match kind {
        ValidationErrorKind::Constant { expected_value } => (
            json!({"allowedValue": expected_value}),
            "must be equal to constant".to_string(),
        ),
        ValidationErrorKind::Enum { options } => (
            json!({"allowedValues": options}),
            "must be equal to one of the allowed values".to_string(),
        ),
        ValidationErrorKind::ExclusiveMaximum { limit } => (
            json!({"comparison": "<", "limit": limit}),
            format!("must be < {limit}"),
        ),
        ValidationErrorKind::ExclusiveMinimum { limit } => (
            json!({"comparison": ">", "limit": limit}),
            format!("must be > {limit}"),
        ),
        ValidationErrorKind::Format { format } => (
            json!({"format": format}),
            format!("must match format \"{format}\""),
        ),
        ValidationErrorKind::Maximum { limit } => (
            json!({"comparison": "<=", "limit": limit}),
            format!("must be <= {limit}"),
        ),
        ValidationErrorKind::Minimum { limit } => (
            json!({"comparison": ">=", "limit": limit}),
            format!("must be >= {limit}"),
        ),
        ValidationErrorKind::MaxItems { limit } => (
            json!({"limit": limit}),
            format!("must NOT have more than {limit} items"),
        ),
        ValidationErrorKind::MinItems { limit } => (
            json!({"limit": limit}),
            format!("must NOT have fewer than {limit} items"),
        ),
        ValidationErrorKind::MaxLength { limit } => (
            json!({"limit": limit}),
            format!("must NOT have more than {limit} characters"),
        ),
        ValidationErrorKind::MinLength { limit } => (
            json!({"limit": limit}),
            format!("must NOT have fewer than {limit} characters"),
        ),
        ValidationErrorKind::MaxProperties { limit } => (
            json!({"limit": limit}),
            format!("must NOT have more than {limit} properties"),
        ),
        ValidationErrorKind::MinProperties { limit } => (
            json!({"limit": limit}),
            format!("must NOT have fewer than {limit} properties"),
        ),
        ValidationErrorKind::MultipleOf { multiple_of } => (
            json!({"multipleOf": multiple_of}),
            format!("must be multiple of {multiple_of}"),
        ),
        ValidationErrorKind::Pattern { pattern } => (
            json!({"pattern": pattern}),
            format!("must match pattern \"{pattern}\""),
        ),
        ValidationErrorKind::Type { kind } => {
            let expected = match kind {
                TypeKind::Single(value) => Value::String(value.to_string()),
                TypeKind::Multiple(values) => Value::Array(
                    values
                        .into_iter()
                        .map(|value| Value::String(value.to_string()))
                        .collect(),
                ),
            };
            let display = match &expected {
                Value::String(value) => value.clone(),
                Value::Array(values) => values
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(","),
                _ => String::new(),
            };
            (json!({"type": expected}), format!("must be {display}"))
        }
        _ => (json!({}), error.to_string()),
    }
}

fn initialize_v8() {
    INITIALIZE_V8.call_once(|| {
        let platform = v8::new_default_platform(0, false).make_shared();
        v8::V8::initialize_platform(platform);
        v8::V8::initialize();
    });
}

fn transpile(code: &str) -> Result<String, String> {
    let mut parsed = parse_typescript(code)?;
    if let Some(rewritten) = without_top_level_main_calls(&parsed) {
        parsed = parse_typescript(&rewritten)?;
    }
    parsed
        .transpile(
            &TranspileOptions::default(),
            &TranspileModuleOptions::default(),
            &EmitOptions::default(),
        )
        .map(|result| result.into_source().text)
        .map_err(|error| error.to_string())
}

fn parse_typescript(code: &str) -> Result<ParsedSource, String> {
    parse_program(ParseParams {
        specifier: ModuleSpecifier::parse("file:///run_typescript.ts")
            .map_err(|error| error.to_string())?,
        text: code.to_string().into(),
        media_type: MediaType::TypeScript,
        capture_tokens: false,
        maybe_syntax: None,
        scope_analysis: false,
    })
    .map_err(|error| error.to_string())
}

/// Returns the program without its top-level statements that call `main`, if any.
///
/// The sandbox calls `main` after evaluating the program, so a program that
/// also calls it would run `main`, and every tool call in it, twice. Each
/// statement becomes an empty statement so that automatic semicolon insertion
/// cannot join its neighbors.
fn without_top_level_main_calls(parsed: &ParsedSource) -> Option<String> {
    let removals = parsed
        .program_ref()
        .body()
        .filter_map(|item| match item {
            ModuleItemRef::Stmt(Stmt::Expr(statement)) if calls_main(&statement.expr) => {
                let range = statement
                    .range()
                    .as_byte_range(StartSourcePos::START_SOURCE_POS);
                Some(TextChange::new(range.start, range.end, ";".to_string()))
            }
            _ => None,
        })
        .collect::<Vec<_>>();
    if removals.is_empty() {
        return None;
    }
    Some(apply_text_changes(parsed.text(), removals))
}

/// Matches `main()`, optionally awaited, voided, or chained with promise handlers.
fn calls_main(expression: &Expr) -> bool {
    match expression {
        Expr::Await(awaited) => calls_main(&awaited.arg),
        Expr::Unary(unary) if unary.op == UnaryOp::Void => calls_main(&unary.arg),
        Expr::Paren(paren) => calls_main(&paren.expr),
        Expr::Call(call) => match &call.callee {
            Callee::Expr(callee) => match callee.as_ref() {
                Expr::Ident(identifier) => &*identifier.sym == "main",
                Expr::Member(member) => {
                    matches!(
                        &member.prop,
                        MemberProp::Ident(name) if matches!(&*name.sym, "then" | "catch" | "finally")
                    ) && calls_main(&member.obj)
                }
                _ => false,
            },
            _ => false,
        },
        _ => false,
    }
}

fn build_source(
    partial_evaluation: &PartialEvaluation,
    tools: &[TypeScriptTool],
    execution_id: &str,
    transpiled: &str,
    determinism: DeterminismContext,
    max_program_effects: u32,
    max_program_operations: u32,
) -> Result<String, String> {
    let previous =
        serde_json::to_string(&partial_evaluation.tool_state).map_err(|error| error.to_string())?;
    let input =
        serde_json::to_string(&partial_evaluation.input).map_err(|error| error.to_string())?;
    let tool_bindings = tools
        .iter()
        .map(|tool| SandboxToolBinding {
            runtime_name: &tool.name,
            namespace: &tool.programmatic_name.namespace,
            function_name: &tool.programmatic_name.name,
        })
        .collect::<Vec<_>>();
    let tool_bindings = serde_json::to_string(&tool_bindings).map_err(|error| error.to_string())?;
    let original_code =
        serde_json::to_string(&partial_evaluation.code).map_err(|error| error.to_string())?;
    let execution_id = serde_json::to_string(execution_id).map_err(|error| error.to_string())?;
    let transpiled = serde_json::to_string(transpiled).map_err(|error| error.to_string())?;
    let time_unix_ms = determinism.time_unix_ms.to_string();
    let random_seed = determinism.random_seed.to_string();
    let max_program_effects = max_program_effects.to_string();
    let max_program_operations = max_program_operations.to_string();

    render_template(&[
        ("__PREVIOUS_OPERATIONS__", &previous),
        ("__INPUT__", &input),
        ("__TOOL_BINDINGS__", &tool_bindings),
        ("__EXECUTION_ID__", &execution_id),
        ("__ORIGINAL_CODE__", &original_code),
        ("__TIME_UNIX_MS__", &time_unix_ms),
        ("__RANDOM_SEED__", &random_seed),
        ("__MAX_PROGRAM_EFFECTS__", &max_program_effects),
        ("__MAX_PROGRAM_OPERATIONS__", &max_program_operations),
        ("__USER_PROGRAM__", &transpiled),
    ])
}

#[derive(Serialize)]
struct SandboxToolBinding<'a> {
    runtime_name: &'a str,
    namespace: &'a str,
    function_name: &'a str,
}

fn render_template(replacements: &[(&str, &str)]) -> Result<String, String> {
    let mut remaining = RUNTIME_TEMPLATE;
    let mut rendered = String::with_capacity(
        RUNTIME_TEMPLATE.len()
            + replacements
                .iter()
                .map(|(_, value)| value.len())
                .sum::<usize>(),
    );
    for (marker, value) in replacements {
        let Some((prefix, suffix)) = remaining.split_once(marker) else {
            return Err(format!("runtime template is missing marker {marker}"));
        };
        rendered.push_str(prefix);
        rendered.push_str(value);
        remaining = suffix;
    }
    rendered.push_str(remaining);
    Ok(rendered)
}

fn execute(isolate: &mut v8::OwnedIsolate, source: &str) -> Result<TypeScriptRunResult, String> {
    v8::scope!(let handle_scope, isolate);
    let context = v8::Context::new(handle_scope, Default::default());
    let scope = &mut v8::ContextScope::new(handle_scope, context);
    v8::tc_scope!(let try_catch, scope);

    let Some(source) = v8::String::new(try_catch, source) else {
        return Ok(error_result(
            "V8Error",
            "could not allocate the TypeScript source",
        ));
    };
    let Some(script) = v8::Script::compile(try_catch, source, None) else {
        return v8_exception_result!(try_catch, "TypeScript compilation failed");
    };
    let Some(value) = script.run(try_catch) else {
        return v8_exception_result!(try_catch, "TypeScript execution failed");
    };
    try_catch.perform_microtask_checkpoint();
    if try_catch.has_terminated() || try_catch.is_execution_terminating() {
        return Err("TypeScript execution exceeded the process safety deadline".to_string());
    }

    let Ok(promise) = v8::Local::<v8::Promise>::try_from(value) else {
        return Ok(error_result(
            "ExecutionError",
            "TypeScript runtime did not return a promise",
        ));
    };
    match promise.state() {
        v8::PromiseState::Pending => Ok(error_result(
            "ExecutionError",
            "TypeScript execution did not settle; unresolved promises are not supported",
        )),
        v8::PromiseState::Rejected => {
            let error = promise.result(try_catch);
            let Some(message) = error.to_string(try_catch) else {
                return Ok(error_result(
                    "ExecutionError",
                    "TypeScript runtime promise was rejected",
                ));
            };
            match strict_v8_string(try_catch, message.into()) {
                Ok(message) => Ok(error_result("ExecutionError", message)),
                Err(error) => Ok(error_result("SerializationError", error)),
            }
        }
        v8::PromiseState::Fulfilled => {
            let value = promise.result(try_catch);
            let serialized = match strict_v8_string(try_catch, value) {
                Ok(serialized) => serialized,
                Err(error) => {
                    return Ok(error_result(
                        "SerializationError",
                        format!("invalid serialized result from the TypeScript isolate: {error}"),
                    ));
                }
            };
            let mut value = match serde_json::from_str::<Value>(&serialized) {
                Ok(value) => value,
                Err(_) => {
                    return Ok(error_result(
                        "SerializationError",
                        "TypeScript isolate result is not valid JSON; values must contain valid Unicode",
                    ));
                }
            };
            restore_v8_number_representations(&mut value);
            Ok(
                serde_json::from_value::<TypeScriptRunResult>(value).unwrap_or_else(|error| {
                    error_result(
                        "SerializationError",
                        format!("invalid result from the TypeScript isolate: {error}"),
                    )
                }),
            )
        }
    }
}

fn strict_v8_string<'s>(
    scope: &mut v8::PinScope<'s, '_>,
    value: v8::Local<'s, v8::Value>,
) -> Result<String, String> {
    let utf16 = deno_core::serde_v8::from_v8::<deno_core::serde_v8::U16String>(scope, value)
        .map_err(|error| format!("expected a string result: {error}"))?;
    String::from_utf16(&utf16).map_err(|_| "serialized result contains invalid Unicode".to_string())
}

fn restore_v8_number_representations(value: &mut Value) {
    match value {
        Value::Number(number) => {
            // serde_v8 classifies integer-valued JavaScript numbers as u32 or i32
            // when possible and as f64 otherwise. Preserve that representation so
            // strict JSON parsing does not change valid evaluator results.
            let is_v8_integer = number
                .as_u64()
                .is_some_and(|value| u32::try_from(value).is_ok())
                || number
                    .as_i64()
                    .is_some_and(|value| i32::try_from(value).is_ok());
            if !is_v8_integer {
                *number = serde_json::Number::from_f64(
                    number
                        .as_f64()
                        .expect("a parsed JSON number is representable as f64"),
                )
                .expect("a finite JSON number remains finite as f64");
            }
        }
        Value::Array(values) => {
            for value in values {
                restore_v8_number_representations(value);
            }
        }
        Value::Object(fields) => {
            for value in fields.values_mut() {
                restore_v8_number_representations(value);
            }
        }
        Value::Null | Value::Bool(_) | Value::String(_) => {}
    }
}

fn error_result(name: &str, message: impl Into<String>) -> TypeScriptRunResult {
    TypeScriptRunResult::Error {
        error: json!({"name": name, "message": message.into()}),
    }
}

fn memory_limit_result(tool_state: Vec<ToolState>) -> TypeScriptRunResult {
    let memory_limit_mib = MAX_HEAP_BYTES / (1024 * 1024);
    let operation_count = tool_state.len();
    TypeScriptRunResult::CodeResult {
        stdout: None,
        stderr: None,
        tool_state,
        result: CodeResult::Error {
            error: json!({
                "name": "MemoryLimitError",
                "message": format!(
                    "run_typescript exceeded its {memory_limit_mib} MiB memory limit with {operation_count} recorded operations. Retry with fewer tool calls in each run_typescript block, request smaller tool results, or split the work across multiple run_typescript calls. Return only the fields needed for the next step instead of every full tool result."
                ),
                "details": {
                    "memoryLimitMiB": memory_limit_mib,
                    "operationCount": operation_count,
                },
            }),
        },
    }
}

const RUNTIME_TEMPLATE: &str = r#"
"use strict";

(async function () {
  const __previous = __PREVIOUS_OPERATIONS__;
  const __input = __INPUT__;
  const __toolBindings = __TOOL_BINDINGS__;
  const __executionId = __EXECUTION_ID__;
  const __originalCode = __ORIGINAL_CODE__;
  const __timeUnixMs = __TIME_UNIX_MS__;
  const __randomSeed = __RANDOM_SEED__;
  const __maxProgramEffects = __MAX_PROGRAM_EFFECTS__;
  const __maxProgramOperations = __MAX_PROGRAM_OPERATIONS__;
  const __output = [];
  const __stdout = [];
  const __stderr = [];
  let __index = 0;
  let __diverged = false;
  let __stepDepth = 0;
  let __stepRandomState = 0;
  let __externalEffectCount = 0;
  let __pendingBatchClosing = false;
  let __pendingBatchClosed = false;
  const __OriginalPromise = Promise;
  const __OriginalPromiseResolve = Promise.resolve.bind(Promise);
  const __OriginalPromiseReject = Promise.reject.bind(Promise);
  const __OriginalPromiseThen = Promise.prototype.then;
  const __OriginalJSONParse = JSON.parse.bind(JSON);
  const __OriginalJSONStringify = JSON.stringify.bind(JSON);
  const __OriginalArrayIsArray = Array.isArray.bind(Array);
  const __OriginalObjectCreate = Object.create.bind(Object);
  const __OriginalObjectKeys = Object.keys.bind(Object);
  const __OriginalObjectSetPrototypeOf = Object.setPrototypeOf.bind(Object);
  const __OriginalTypeError = TypeError;
  const __someOutput = Array.prototype.some.bind(__output);
  const __joinStdout = Array.prototype.join.bind(__stdout);
  const __joinStderr = Array.prototype.join.bind(__stderr);
  let __resolvePendingToolBarrier = () => {};
  const __pendingToolBarrier = new __OriginalPromise((resolve) => {
    __resolvePendingToolBarrier = resolve;
  });

  function __serialize(value) {
    if (value === undefined) return null;
    return __OriginalJSONParse(__OriginalJSONStringify(value));
  }

  function __serializeError(error) {
    try {
      if (error instanceof Error) {
        return __serialize({ name: error.name, message: error.message, stack: error.stack });
      }
      return __serialize(error);
    } catch (_) {
      try {
        return { name: "Error", message: String(error) };
      } catch (_) {
        return { name: "Error", message: "unserializable error" };
      }
    }
  }

  function __copyJSONDataWithoutHooks(value, ancestors) {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
      return value === value && value !== Infinity && value !== -Infinity ? value : null;
    }
    if (typeof value === "bigint") {
      throw new __OriginalTypeError("BigInt values cannot be serialized to JSON");
    }
    if (typeof value !== "object") return undefined;

    for (let index = 0; index < ancestors.length; index += 1) {
      if (ancestors[index] === value) {
        throw new __OriginalTypeError("Converting circular structure to JSON");
      }
    }
    ancestors[ancestors.length] = value;

    let copy;
    if (__OriginalArrayIsArray(value)) {
      copy = [];
      __OriginalObjectSetPrototypeOf(copy, null);
      for (let index = 0; index < value.length; index += 1) {
        const item = __copyJSONDataWithoutHooks(value[index], ancestors);
        copy[index] = item === undefined ? null : item;
      }
    } else {
      copy = __OriginalObjectCreate(null);
      const keys = __OriginalObjectKeys(value);
      for (let index = 0; index < keys.length; index += 1) {
        const key = keys[index];
        const item = __copyJSONDataWithoutHooks(value[key], ancestors);
        if (item !== undefined) copy[key] = item;
      }
    }

    ancestors.length -= 1;
    return copy;
  }

  function __serializeEvaluatorResult(value) {
    const ancestors = [];
    __OriginalObjectSetPrototypeOf(ancestors, null);
    return __OriginalJSONStringify(__copyJSONDataWithoutHooks(value, ancestors));
  }

  function __recordedOperations() {
    const operations = [];
    __OriginalObjectSetPrototypeOf(operations, null);
    for (let index = 0; index < __output.length; index += 1) {
      if (__output[index]) operations[operations.length] = __output[index];
    }
    return operations;
  }

  function __deserializeError(error) {
    if (error && typeof error === "object" && typeof error.message === "string") {
      const replayed = new Error(error.message, { cause: error });
      replayed.name = typeof error.name === "string" ? error.name : "Error";
      if (typeof error.stack === "string") replayed.stack = error.stack;
      return replayed;
    }
    return error;
  }

  function __canonical(value) {
    if (Array.isArray(value)) return value.map(__canonical);
    if (value && typeof value === "object") {
      const result = {};
      for (const key of Object.keys(value).sort()) result[key] = __canonical(value[key]);
      return result;
    }
    return value;
  }

  function __equal(left, right) {
    return __OriginalJSONStringify(__canonical(left)) === __OriginalJSONStringify(__canonical(right));
  }

  function __normalizeArgs(args) {
    if (args === undefined || args === null) return {};
    return __serialize(args);
  }

  function __lookup(name, args) {
    const current = __previous[__index];
    if (!current) return { type: "new" };
    if (current.type === "pending_tool") return { type: "mismatch" };
    if (current.function.name === name && __equal(current.function.arguments || {}, args)) {
      return { type: "replay", state: current };
    }
    return { type: "mismatch" };
  }

  function __operationId(kind, index) {
    return __executionId + ":" + kind + ":" + String(index);
  }

  function __internalRace(left, right) {
    return new __OriginalPromise((resolve, reject) => {
      __OriginalPromiseThen.call(left, resolve, reject);
      __OriginalPromiseThen.call(right, resolve, reject);
    });
  }

  function __consumeExternalEffectBudget() {
    if (__externalEffectCount >= __maxProgramEffects) {
      throw new Error("run_typescript exceeded maxProgramEffects (" + String(__maxProgramEffects) + ").");
    }
    __externalEffectCount += 1;
  }

  function __checkProgramOperationBudget() {
    if (__index >= __maxProgramOperations) {
      throw new Error("run_typescript exceeded maxProgramOperations (" + String(__maxProgramOperations) + ").");
    }
  }

  function __callTool(name, argsInput) {
    if (__stepDepth > 0) {
      return __OriginalPromiseReject(new Error(
        "Tool calls are not allowed inside step(). Call tools before or after step()."));
    }
    let args;
    try {
      args = __normalizeArgs(argsInput);
    } catch (error) {
      return __OriginalPromiseReject(error);
    }
    __checkProgramOperationBudget();

    if (!__diverged) {
      const lookup = __lookup(name, args);
      if (lookup.type === "replay") {
        __consumeExternalEffectBudget();
        __output.push(lookup.state);
        __index += 1;
        if (lookup.state.type === "resolved_tool") return __OriginalPromiseResolve(lookup.state.result);
        return __OriginalPromiseReject(__deserializeError(lookup.state.error));
      }
      if (lookup.type === "mismatch") __diverged = true;
    }

    // Keep a pending batch to operations reached before the scheduled close.
    // Later microtask branches are speculative until replay has real results.
    if (__pendingBatchClosed) {
      __consumeExternalEffectBudget();
      return new __OriginalPromise(() => {});
    }

    const operationIndex = __index;
    __consumeExternalEffectBudget();
    __index += 1;
    __output.push({
      kind: "external",
      type: "pending_tool",
      id: __operationId("tool", operationIndex),
      function: { name, arguments: args },
    });
    if (!__pendingBatchClosing) {
      __pendingBatchClosing = true;
      __OriginalPromiseThen.call(__OriginalPromiseResolve(), () => {
        __pendingBatchClosed = true;
        __resolvePendingToolBarrier();
      });
    }
    return new __OriginalPromise(() => {});
  }

  function __beginStep(suspendAfterPending) {
    const args = {};
    const name = "__internal__.step";
    __checkProgramOperationBudget();
    if (suspendAfterPending && __output.some((operation) => operation && operation.type === "pending_tool")) {
      return { type: "suspend" };
    }

    if (!__diverged) {
      const lookup = __lookup(name, args);
      if (lookup.type === "replay") {
        __output.push(lookup.state);
        __index += 1;
        if (lookup.state.type === "resolved_tool") return { type: "resolve", value: lookup.state.result };
        return { type: "reject", value: lookup.state.error };
      }
      if (lookup.type === "mismatch") __diverged = true;
    }

    const operationIndex = __index;
    const outputIndex = __output.length;
    __index += 1;
    __output.push(null);
    __stepRandomState = __mixStepSeed(__randomSeed, operationIndex);
    return { type: "run", operationIndex, outputIndex };
  }

  function __finishStep(instruction, result) {
    const args = {};
    __output[instruction.outputIndex] = result.type === "success"
      ? {
          kind: "internal",
          type: "resolved_tool",
          id: __operationId("step", instruction.operationIndex),
          function: { name: "__internal__.step", arguments: args },
          result: result.value,
        }
      : {
          kind: "internal",
          type: "rejected_tool",
          id: __operationId("step", instruction.operationIndex),
          function: { name: "__internal__.step", arguments: args },
          error: result.error,
        };
  }

  async function step(callback) {
    if (typeof callback !== "function") throw new Error("step() expects a function.");
    if (__stepDepth > 0) throw new Error("Nested step() calls are not supported.");
    const instruction = __beginStep(true);
    if (instruction.type === "resolve") return instruction.value;
    if (instruction.type === "reject") throw __deserializeError(instruction.value);
    if (instruction.type === "suspend") return await new __OriginalPromise(() => {});

    __stepDepth += 1;
    try {
      const value = __serialize(await callback());
      __finishStep(instruction, { type: "success", value });
      return value;
    } catch (error) {
      __finishStep(instruction, { type: "error", error: __serializeError(error) });
      throw error;
    } finally {
      __stepDepth -= 1;
    }
  }

  const console = Object.freeze({
    log: (...args) => __stdout.push(args.map((value) => {
      if (typeof value === "string") return value;
      try { return __OriginalJSONStringify(value); } catch (_) { return String(value); }
    }).join(" ")),
    info: (...args) => console.log(...args),
    debug: (...args) => console.log(...args),
    error: (...args) => __stderr.push(args.map((value) => {
      if (typeof value === "string") return value;
      try { return __OriginalJSONStringify(value); } catch (_) { return String(value); }
    }).join(" ")),
    warn: (...args) => console.error(...args),
  });

  const tools = {};
  for (const binding of __toolBindings) {
    const namespace = binding.namespace;
    const functionName = binding.function_name;
    if (!tools[namespace]) tools[namespace] = {};
    tools[namespace][functionName] = (args) => __callTool(binding.runtime_name, args);
  }
  for (const namespace of Object.keys(tools)) Object.freeze(tools[namespace]);
  Object.freeze(tools);

  const __OriginalDate = Date;
  const __OriginalPromiseRace = Promise.race.bind(Promise);
  const __OriginalPromiseAny = Promise.any.bind(Promise);
  // Let contenders settle without a global step scope so their tools and ambient
  // operations remain independent. Only the selected outcome is recorded here.
  function __recordPromiseSelectionOutcome(outcome) {
    const instruction = __beginStep(false);
    if (instruction.type === "resolve") return instruction.value;
    if (instruction.type === "reject") throw __deserializeError(instruction.value);

    const recorded = __serialize(outcome);
    __finishStep(instruction, { type: "success", value: recorded });
    return recorded;
  }
  async function __durablePromiseRace(values) {
    const entries = [];
    for (const value of values) {
      const entry = __OriginalPromiseResolve(value);
      entries.push(entry);
    }
    return await new __OriginalPromise((resolve, reject) => {
      let selected = false;
      const select = (observedWinnerIndex) => {
        if (selected) return;
        selected = true;
        try {
          const winnerIndex = __recordPromiseSelectionOutcome(observedWinnerIndex);
          __OriginalPromiseThen.call(entries[winnerIndex], resolve, reject);
        } catch (error) {
          reject(error);
        }
      };
      for (let index = 0; index < entries.length; index += 1) {
        __OriginalPromiseThen.call(entries[index], () => select(index), () => select(index));
      }
    });
  }
  async function __durablePromiseAny(values) {
    const entries = [];
    for (const value of values) {
      const entry = __OriginalPromiseResolve(value);
      entries.push(entry);
    }
    if (entries.length === 0) {
      __recordPromiseSelectionOutcome({ type: "all_rejected" });
      return await __OriginalPromiseAny(entries);
    }
    return await new __OriginalPromise((resolve, reject) => {
      let rejectedCount = 0;
      let selected = false;
      const select = (observedOutcome) => {
        if (selected) return;
        selected = true;
        try {
          const outcome = __recordPromiseSelectionOutcome(observedOutcome);
          const selectedEntry = outcome.type === "all_rejected"
            ? __OriginalPromiseAny(entries)
            : entries[outcome.index];
          __OriginalPromiseThen.call(selectedEntry, resolve, reject);
        } catch (error) {
          reject(error);
        }
      };
      for (let index = 0; index < entries.length; index += 1) {
        __OriginalPromiseThen.call(
          entries[index],
          () => select({ type: "fulfilled", index }),
          () => {
            rejectedCount += 1;
            if (rejectedCount === entries.length) select({ type: "all_rejected" });
          },
        );
      }
    });
  }
  function __mixStepSeed(seed, operationIndex) {
    let value = (seed ^ Math.imul((operationIndex + 1) >>> 0, 0x9e3779b9)) >>> 0;
    value ^= value >>> 16;
    value = Math.imul(value, 0x7feb352d) >>> 0;
    value ^= value >>> 15;
    value = Math.imul(value, 0x846ca68b) >>> 0;
    value ^= value >>> 16;
    return value === 0 ? 0x6d2b79f5 : value;
  }
  function __nextStepRandom() {
    __stepRandomState ^= __stepRandomState << 13;
    __stepRandomState ^= __stepRandomState >>> 17;
    __stepRandomState ^= __stepRandomState << 5;
    __stepRandomState >>>= 0;
    return __stepRandomState / 4294967296;
  }
  function __runDurableSync(callback) {
    if (__stepDepth > 0) return callback();
    // Synchronous durable builtins cannot leave a partial step, so they may record after pending tools.
    const instruction = __beginStep(false);
    if (instruction.type === "resolve") return instruction.value;
    if (instruction.type === "reject") throw __deserializeError(instruction.value);

    __stepDepth += 1;
    try {
      const value = __serialize(callback());
      __finishStep(instruction, { type: "success", value });
      return value;
    } catch (error) {
      __finishStep(instruction, { type: "error", error: __serializeError(error) });
      throw error;
    } finally {
      __stepDepth -= 1;
    }
  }
  function __durableNow() {
    return __runDurableSync(function () { return __timeUnixMs; });
  }
  Date = function Date(...args) {
    if (new.target === undefined) {
      return new __OriginalDate(__durableNow()).toString();
    }
    if (args.length === 0) {
      return Reflect.construct(__OriginalDate, [__durableNow()]);
    }
    return Reflect.construct(__OriginalDate, args);
  };
  Date.now = function () { return __durableNow(); };
  Date.parse = __OriginalDate.parse.bind(__OriginalDate);
  Date.UTC = __OriginalDate.UTC.bind(__OriginalDate);
  Date.prototype = __OriginalDate.prototype;
  Math.random = function () {
    return __runDurableSync(function () {
      return __nextStepRandom();
    });
  };
  globalThis.crypto = Object.freeze({
    randomUUID: function () {
      return __runDurableSync(function () {
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
          const value = Math.floor(__nextStepRandom() * 16);
          return (character === "x" ? value : (value & 3) | 8).toString(16);
        });
      });
    },
    getRandomValues: function (array) {
      if (!ArrayBuffer.isView(array) || array instanceof DataView || typeof array.length !== "number") {
        throw new TypeError("crypto.getRandomValues() expects an integer typed array.");
      }
      const randomBytes = __runDurableSync(function () {
        const values = [];
        for (let index = 0; index < array.byteLength; index += 1) {
          values.push(Math.floor(__nextStepRandom() * 256));
        }
        return values;
      });
      const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = randomBytes[index];
      }
      return array;
    },
  });
  globalThis.performance = Object.freeze({
    now: function () {
      return __runDurableSync(function () { return __timeUnixMs; });
    },
  });
  Promise.race = function (values) {
    // An explicit step already records the complete selection result.
    if (__stepDepth > 0) return __OriginalPromiseRace(values);
    return __durablePromiseRace(values);
  };
  Promise.any = function (values) {
    // An explicit step already records the complete selection result.
    if (__stepDepth > 0) return __OriginalPromiseAny(values);
    return __durablePromiseAny(values);
  };
  globalThis.fetch = function () { throw new Error("fetch() is not supported in the sandbox. Use a tool to make HTTP requests instead."); };
  globalThis.setTimeout = function (callback, delay, ...args) {
    if (delay !== undefined && delay !== 0) {
      throw new Error("Only zero-delay setTimeout() is supported in this sandbox.");
    }
    Promise.resolve().then(function () { callback(...args); });
    return 0;
  };
  globalThis.setInterval = function () { throw new Error("setInterval() is not supported in the sandbox. It introduces non-deterministic timing."); };
  globalThis.setImmediate = function (callback, ...args) {
    Promise.resolve().then(function () { callback(...args); });
    return 0;
  };
  globalThis.clearTimeout = function () {};
  globalThis.clearInterval = function () { throw new Error("clearInterval() is not supported in the sandbox because timers are disabled."); };
  globalThis.clearImmediate = function () {};
  globalThis.eval = function () { throw new Error("eval() is not supported in the sandbox. Write your logic directly in the main function instead."); };

  const __userProgram = __USER_PROGRAM__;
  const __AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const __runUserCode = new __AsyncFunction(
    "tools",
    "step",
    "input",
    "console",
    `"use strict";\n${__userProgram}\n` +
      `if (typeof main !== "function") throw new Error("run_typescript code must define async function main().");\n` +
      `return await main(input);`,
  );
  let __collected;
  try {
    const __value = await __internalRace(
      __runUserCode(tools, step, __input, console),
      __pendingToolBarrier,
    );
    __collected = {
      type: "success",
      value: __serialize(__value),
    };
  } catch (error) {
    __collected = { type: "error", error: __serializeError(error) };
  }

  const __stdoutText = __stdout.length > 0 ? __joinStdout("\n") : undefined;
  const __stderrText = __stderr.length > 0 ? __joinStderr("\n") : undefined;
  const __hasPending = __someOutput((operation) => operation && operation.type === "pending_tool");
  const __result = __hasPending
    ? {
        type: "partial_evaluation",
        partial_evaluation: { code: __originalCode, input: __input, tool_state: __recordedOperations() },
      }
    : {
        type: "code_result",
        stdout: __stdoutText,
        stderr: __stderrText,
        tool_state: __recordedOperations(),
        result: __collected,
      };
  return __serializeEvaluatorResult(__result);
})();
"#;

#[cfg(test)]
mod tests {
    use super::*;

    fn determinism() -> DeterminismContext {
        DeterminismContext {
            time_unix_ms: 1_700_000_000_000,
            random_seed: 0x1234_5678,
        }
    }

    fn run(
        partial_evaluation: &PartialEvaluation,
        tools: &[TypeScriptTool],
        execution_id: &str,
    ) -> TypeScriptRunResult {
        run_typescript(
            partial_evaluation,
            tools,
            execution_id,
            determinism(),
            128,
            1024,
        )
        .expect("TypeScript host execution should succeed")
    }

    fn tool(name: &str) -> TypeScriptTool {
        TypeScriptTool {
            name: name.to_string(),
            programmatic_name: ProgrammaticName {
                namespace: "test".to_string(),
                name: name.to_string(),
            },
            description: String::new(),
            input_schema: json!({"type": "object"}),
            output_schema: None,
        }
    }

    fn tool_with_schema(name: &str, input_schema: Value) -> TypeScriptTool {
        TypeScriptTool {
            input_schema,
            ..tool(name)
        }
    }

    fn partial(code: &str) -> PartialEvaluation {
        PartialEvaluation {
            code: code.to_string(),
            input: json!({}),
            tool_state: Vec::new(),
        }
    }

    fn pending(result: TypeScriptRunResult) -> PartialEvaluation {
        match result {
            TypeScriptRunResult::PartialEvaluation { partial_evaluation } => partial_evaluation,
            other => panic!("expected a partial evaluation, got {other:?}"),
        }
    }

    fn resolve_all(mut partial: PartialEvaluation, values: &[Value]) -> PartialEvaluation {
        let mut values = values.iter();
        partial.tool_state = partial
            .tool_state
            .into_iter()
            .map(|state| match state {
                ToolState::Pending { kind, id, function } => ToolState::Resolved {
                    kind,
                    id,
                    function,
                    result: values.next().cloned().expect("missing resolved value"),
                    content: Vec::new(),
                },
                state => state,
            })
            .collect();
        assert!(values.next().is_none(), "unused resolved values");
        partial
    }

    #[test]
    fn pauses_and_replays_multiple_tool_calls() {
        let source = r#"
async function main(): Promise<number> {
  const [left, right] = await Promise.all([
    tools.test.first({ value: 1 }),
    tools.test.second({ value: 2 }),
  ]);
  return left.value + right.value;
}
"#;
        let first = pending(run(
            &partial(source),
            &[tool("first"), tool("second")],
            "run",
        ));
        assert_eq!(first.tool_state.len(), 2);
        let resolved = resolve_all(first, &[json!({"value": 3}), json!({"value": 4})]);

        let result = run(&resolved, &[tool("first"), tool("second")], "run");

        assert_eq!(
            result,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state: resolved.tool_state,
                result: CodeResult::Success { value: json!(7) },
            }
        );
    }

    #[test]
    fn runs_main_once_when_the_program_also_calls_it() {
        for invocation in [
            "main();",
            "await main();",
            "void main();",
            "main().catch(console.error);",
            "main().then(console.log).catch(console.error);",
        ] {
            let source = format!(
                r#"
async function main() {{
  const draft = await tools.test.create_draft({{ subject: "Résumé — semaine 38" }});
  return draft.id;
}}
{invocation}
"#
            );
            let first = pending(run(&partial(&source), &[tool("create_draft")], "run"));
            assert_eq!(first.tool_state.len(), 1, "{invocation}");
            let resolved = resolve_all(first, &[json!({"id": "draft-1"})]);

            let result = run(&resolved, &[tool("create_draft")], "run");

            assert_eq!(
                result,
                TypeScriptRunResult::CodeResult {
                    stdout: None,
                    stderr: None,
                    tool_state: resolved.tool_state,
                    result: CodeResult::Success {
                        value: json!("draft-1")
                    },
                },
                "{invocation}"
            );
        }
    }

    #[test]
    fn rejects_lone_utf16_surrogates_in_tool_arguments() {
        for code_unit in [0xd800, 0xdc00] {
            let source = format!(
                r#"
async function main() {{
  return tools.test.write_file({{ content: String.fromCharCode({code_unit}) }});
}}
"#
            );

            let result = run(&partial(&source), &[tool("write_file")], "run");

            let TypeScriptRunResult::Error { error } = result else {
                panic!("expected invalid Unicode to fail serialization, got {result:?}");
            };
            assert_eq!(error["name"], "SerializationError");
        }
    }

    #[test]
    fn rejects_lone_utf16_surrogates_in_every_evaluator_result_field() {
        let cases = [
            (
                "final return value",
                "async function main() { return String.fromCharCode(0xd800); }",
            ),
            (
                "console output",
                "async function main() { console.log(String.fromCharCode(0xd800)); return null; }",
            ),
            (
                "thrown error",
                "async function main() { throw new Error(String.fromCharCode(0xd800)); }",
            ),
            (
                "recorded step value",
                "async function main() { await step(() => String.fromCharCode(0xd800)); return null; }",
            ),
        ];

        for (case, source) in cases {
            let result = run(&partial(source), &[], "run");

            let TypeScriptRunResult::Error { error } = result else {
                panic!("expected {case} to fail serialization, got {result:?}");
            };
            assert_eq!(error["name"], "SerializationError", "{case}");
        }
    }

    #[test]
    fn user_code_cannot_replace_harness_json_serialization() {
        let source = r#"
async function main() {
  const originalStringify = JSON.stringify.bind(JSON);
  const loneSurrogate = String.fromCharCode(0xd800);
  JSON.stringify = (value) => {
    if (value && value.type === "code_result") {
      return originalStringify({
        type: "code_result",
        tool_state: [],
        result: { type: "success", value: loneSurrogate },
      }).replace("\\ud800", loneSurrogate);
    }
    return originalStringify(value);
  };
  return "actual result";
}
"#;

        let result = run(&partial(source), &[], "run");

        assert_eq!(
            result,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state: Vec::new(),
                result: CodeResult::Success {
                    value: json!("actual result"),
                },
            }
        );
    }

    #[test]
    fn user_code_cannot_replace_tool_arguments_through_inherited_to_json() {
        let source = r#"
async function main() {
  const loneSurrogate = String.fromCharCode(0xd800);
  const pending = tools.test.write_file({ content: loneSurrogate });
  Object.prototype.toJSON = function () {
    if (this.content === loneSurrogate) return { content: "forged content" };
    return this;
  };
  return await pending;
}
"#;

        let result = run(&partial(source), &[tool("write_file")], "run");

        let TypeScriptRunResult::Error { error } = result else {
            panic!("expected invalid Unicode to fail serialization, got {result:?}");
        };
        assert_eq!(error["name"], "SerializationError", "{error:?}");
    }

    #[test]
    fn user_code_cannot_replace_recorded_operations_through_array_filter() {
        let source = r#"
async function main() {
  const loneSurrogate = String.fromCharCode(0xd800);
  const pending = tools.test.write_file({ content: loneSurrogate });
  Array.prototype.filter = () => [{
    kind: "external",
    type: "pending_tool",
    id: "run:tool:0",
    function: { name: "write_file", arguments: { content: "forged content" } },
  }];
  return await pending;
}
"#;

        let result = run(&partial(source), &[tool("write_file")], "run");

        let TypeScriptRunResult::Error { error } = result else {
            panic!("expected invalid Unicode to fail serialization, got {result:?}");
        };
        assert_eq!(error["name"], "SerializationError");
    }

    #[test]
    fn user_code_cannot_mutate_the_envelope_through_an_array_prototype_setter() {
        let source = r#"
async function main() {
  const loneSurrogate = String.fromCharCode(0xd800);
  const pending = tools.test.write_file({ content: loneSurrogate });
  Object.defineProperty(Array.prototype, "0", {
    configurable: true,
    set(value) {
      const operation = value?.partial_evaluation?.tool_state?.[0];
      if (operation) operation.function.arguments.content = "forged content";
    },
  });
  return await pending;
}
"#;

        let result = run(&partial(source), &[tool("write_file")], "run");

        let TypeScriptRunResult::Error { error } = result else {
            panic!("expected invalid Unicode to fail serialization, got {result:?}");
        };
        assert_eq!(error["name"], "SerializationError", "{error:?}");
    }

    #[test]
    fn user_code_cannot_replace_recorded_operations_through_array_species() {
        let source = r#"
async function main() {
  const loneSurrogate = String.fromCharCode(0xd800);
  const pending = tools.test.write_file({ content: loneSurrogate });
  function ForgedArray() {
    return new Proxy([], {
      defineProperty(target, property, descriptor) {
        if (property === "0") {
          descriptor.value = {
            kind: "external",
            type: "pending_tool",
            id: "run:tool:0",
            function: { name: "write_file", arguments: { content: "forged content" } },
          };
        }
        return Reflect.defineProperty(target, property, descriptor);
      },
    });
  }
  Object.defineProperty(Array, Symbol.species, { configurable: true, value: ForgedArray });
  return await pending;
}
"#;

        let result = run(&partial(source), &[tool("write_file")], "run");

        let TypeScriptRunResult::Error { error } = result else {
            panic!("expected invalid Unicode to fail serialization, got {result:?}");
        };
        assert_eq!(error["name"], "SerializationError", "{error:?}");
    }

    #[test]
    fn rejects_malformed_unicode_in_evaluator_promise_rejections() {
        initialize_v8();
        let mut isolate = v8::Isolate::new(Default::default());

        let result = execute(
            &mut isolate,
            "(async function () { throw String.fromCharCode(0xd800); })();",
        )
        .expect("evaluation should complete with a structured error");

        let TypeScriptRunResult::Error { error } = result else {
            panic!("expected invalid Unicode to fail serialization, got {result:?}");
        };
        assert_eq!(error["name"], "SerializationError");
    }

    #[test]
    fn preserves_valid_json_and_unicode_across_the_v8_boundary() {
        let source = r#"
async function main() {
  const value = {
    emoji: "\u{1f600}",
    combining: "e\u0301",
    controls: "\u0000\b\t\n\f\r",
    replacement_character: "\ufffd",
    large_number: 1700000000000,
    nested: [true, null, { number: 42.5 }],
  };
  console.log(value.emoji + value.combining + value.controls);
  const recorded = await step(() => value);
  const echoed = await tools.test.echo({ value: recorded });
  return { value: recorded, echoed };
}
"#;
        let expected_value = json!({
            "emoji": "😀",
            "combining": "e\u{301}",
            "controls": "\0\u{8}\t\n\u{c}\r",
            "replacement_character": "�",
            "large_number": 1_700_000_000_000.0,
            "nested": [true, null, {"number": 42.5}],
        });
        let first = pending(run(&partial(source), &[tool("echo")], "run"));

        let [
            ToolState::Resolved {
                kind: ToolKind::Internal,
                result: recorded,
                ..
            },
            ToolState::Pending { function, .. },
        ] = first.tool_state.as_slice()
        else {
            panic!("expected one recorded step and one pending tool call");
        };
        assert_eq!(recorded, &expected_value);
        assert_eq!(function.arguments, json!({"value": expected_value}));

        let resolved = resolve_all(first, &[json!({"ok": true})]);
        let result = run(&resolved, &[tool("echo")], "run");

        assert_eq!(
            result,
            TypeScriptRunResult::CodeResult {
                stdout: Some("😀e\u{301}\0\u{8}\t\n\u{c}\r".to_string()),
                stderr: None,
                tool_state: resolved.tool_state,
                result: CodeResult::Success {
                    value: json!({
                        "value": expected_value,
                        "echoed": {"ok": true},
                    }),
                },
            }
        );
    }

    #[test]
    fn preserves_tool_call_identity_across_async_hop_counts() {
        for hop_count in 0..=7 {
            replay_preserves_tool_call_invariants(&source_with_await_hops(hop_count), hop_count);
        }
    }

    fn source_with_await_hops(hop_count: usize) -> String {
        let awaits = "  await null;\n".repeat(hop_count);
        format!(
            r#"
async function main(): Promise<number> {{
  const first = tools.test.first({{ value: 1 }});
{awaits}  const second = tools.test.second({{ value: 2 }});
  const [left, right] = await Promise.all([first, second]);
  return left.value + right.value;
}}
"#
        )
    }

    fn replay_preserves_tool_call_invariants(source: &str, hop_count: usize) {
        let tools = [tool("first"), tool("second")];
        let mut evaluation = partial(source);
        let mut seen_pending_ids = std::collections::HashSet::new();
        let mut resolved_ids = std::collections::HashSet::new();

        for _ in 0..8 {
            let result = run(&evaluation, &tools, "run");
            match result {
                TypeScriptRunResult::PartialEvaluation { partial_evaluation } => {
                    for state in &partial_evaluation.tool_state {
                        match state {
                            ToolState::Pending { id, function, .. } => {
                                assert!(
                                    !resolved_ids.contains(id),
                                    "resolved operation {id} was emitted as pending again for {hop_count} hops"
                                );
                                assert!(
                                    seen_pending_ids.insert(id.clone()),
                                    "operation {id} was emitted as pending more than once for {hop_count} hops"
                                );
                                assert!(matches!(function.name.as_str(), "first" | "second"));
                            }
                            ToolState::Resolved { id, .. } => {
                                resolved_ids.insert(id.clone());
                            }
                            ToolState::Rejected { id, .. } => {
                                panic!(
                                    "operation {id} should not be rejected for {hop_count} hops"
                                );
                            }
                        }
                    }

                    evaluation = resolve_pending_by_name(partial_evaluation);
                }
                TypeScriptRunResult::CodeResult {
                    stdout,
                    stderr,
                    tool_state,
                    result: CodeResult::Success { value },
                } => {
                    assert_eq!(stdout, None);
                    assert_eq!(stderr, None);
                    assert_eq!(value, json!(7));
                    let [
                        ToolState::Resolved {
                            id: first_id,
                            function: first_function,
                            result: first_result,
                            ..
                        },
                        ToolState::Resolved {
                            id: second_id,
                            function: second_function,
                            result: second_result,
                            ..
                        },
                    ] = tool_state.as_slice()
                    else {
                        panic!("expected exactly two resolved tool calls");
                    };
                    assert_eq!(first_id, "run:tool:0");
                    assert_eq!(first_function.name, "first");
                    assert_eq!(first_result, &json!({"value": 3}));
                    assert_eq!(second_id, "run:tool:1");
                    assert_eq!(second_function.name, "second");
                    assert_eq!(second_result, &json!({"value": 4}));
                    return;
                }
                other => panic!(
                    "expected the program to finish successfully for {hop_count} hops, got {other:?}"
                ),
            }
        }

        panic!("program did not finish within the replay bound for {hop_count} hops");
    }

    fn resolve_pending_by_name(mut partial: PartialEvaluation) -> PartialEvaluation {
        partial.tool_state = partial
            .tool_state
            .into_iter()
            .map(|state| match state {
                ToolState::Pending { kind, id, function } => {
                    let result = match function.name.as_str() {
                        "first" => json!({"value": 3}),
                        "second" => json!({"value": 4}),
                        other => panic!("unexpected pending function {other}"),
                    };
                    ToolState::Resolved {
                        kind,
                        id,
                        function,
                        result,
                        content: Vec::new(),
                    }
                }
                state => state,
            })
            .collect();
        partial
    }

    #[test]
    fn does_not_reject_all_settled_for_pending_tool_suspension() {
        let source = r#"
async function main() {
  const [read] = await Promise.allSettled([
    tools.test.read_file({ path: "config.json" }),
  ]);

  if (read.status === "rejected") {
    await tools.test.write_file({
      path: "config.json",
      content: "{}",
    });
    return { recovered: true };
  }

  return read.value;
}
"#;
        let tools = [tool("read_file"), tool("write_file")];
        let first = pending(run(&partial(source), &tools, "run"));

        let [ToolState::Pending { function, .. }] = first.tool_state.as_slice() else {
            panic!("expected exactly one pending tool call");
        };
        assert_eq!(function.name, "read_file");
        assert_eq!(function.arguments, json!({"path": "config.json"}));

        let resolved = resolve_all(first, &[json!({"content": "{}"})]);
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&resolved, &tools, "run")
        else {
            panic!("expected the program to finish on the success path");
        };

        assert_eq!(value, json!({"content": "{}"}));
        let [
            ToolState::Resolved {
                function, result, ..
            },
        ] = tool_state.as_slice()
        else {
            panic!("expected only the resolved read_file call");
        };
        assert_eq!(function.name, "read_file");
        assert_eq!(result, &json!({"content": "{}"}));
    }

    #[test]
    fn prevents_orphaned_tool_call_after_pending_batch_closes() {
        let source = r#"
async function main() {
  let settled = false;
  tools.test.read_file({ path: "config.json" }).then(() => { settled = true; });
  await null;

  if (!settled) {
    await tools.test.write_file({
      path: "config.json",
      content: "{}",
    });
    return { recovered: true };
  }

  return { content: "{}" };
}
"#;
        let tools = [tool("read_file"), tool("write_file")];
        let first = pending(run(&partial(source), &tools, "run"));

        let [ToolState::Pending { function, .. }] = first.tool_state.as_slice() else {
            panic!("expected only read_file in the pending batch");
        };
        assert_eq!(function.name, "read_file");
        assert_eq!(function.arguments, json!({"path": "config.json"}));

        let resolved = resolve_all(first, &[json!({"content": "{}"})]);
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&resolved, &tools, "run")
        else {
            panic!("expected the program to finish on replay");
        };

        assert_eq!(value, json!({"content": "{}"}));
        let [
            ToolState::Resolved {
                function, result, ..
            },
        ] = tool_state.as_slice()
        else {
            panic!("expected only the resolved read_file call");
        };
        assert_eq!(function.name, "read_file");
        assert_eq!(result, &json!({"content": "{}"}));
    }

    #[test]
    fn bounds_speculative_tool_loop_after_pending_batch_closes() {
        let source = r#"
async function main() {
  let index = 0;
  while (true) {
    tools.test.read_file({ path: "p" + index });
    index += 1;
    await null;
  }
}
"#;
        let first = pending(run(&partial(source), &[tool("read_file")], "run"));

        let [ToolState::Pending { function, .. }] = first.tool_state.as_slice() else {
            panic!("expected only the first pending read_file call");
        };
        assert_eq!(function.name, "read_file");
        assert_eq!(function.arguments, json!({"path": "p0"}));
    }

    #[test]
    fn suspends_explicit_step_after_pending_tool_until_replay() {
        let source = r#"
async function main() {
  const read = tools.test.read_file({});
  const local = await step(async () => {
    await Promise.resolve();
    return "recorded";
  });
  return { read: await read, local };
}
"#;
        let first = pending(run(&partial(source), &[tool("read_file")], "run"));

        let [ToolState::Pending { function, .. }] = first.tool_state.as_slice() else {
            panic!("expected only the suspended read_file call");
        };
        assert_eq!(function.name, "read_file");

        let resolved = resolve_all(first, &[json!({"content": "ok"})]);
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&resolved, &[tool("read_file")], "run")
        else {
            panic!("expected the program to finish after replay");
        };

        assert_eq!(
            value,
            json!({"read": {"content": "ok"}, "local": "recorded"})
        );
        assert!(matches!(
            tool_state.as_slice(),
            [
                ToolState::Resolved {
                    kind: ToolKind::External,
                    ..
                },
                ToolState::Resolved {
                    kind: ToolKind::Internal,
                    ..
                },
            ]
        ));
    }

    #[test]
    fn records_sync_durable_builtin_after_pending_tool_in_same_pass() {
        let source = r#"
async function main() {
  const read = tools.test.read_file({});
  const random = Math.random();
  if (random === 0) {
    await tools.test.write_file({});
  }
  return { read: await read, random };
}
"#;
        let tools = [tool("read_file"), tool("write_file")];
        let first = pending(run(&partial(source), &tools, "run"));

        let [
            ToolState::Pending {
                function: read_function,
                ..
            },
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: step_function,
                result: random,
                ..
            },
        ] = first.tool_state.as_slice()
        else {
            panic!("expected the suspended read_file and replayable random value");
        };
        assert_eq!(read_function.name, "read_file");
        assert_eq!(step_function.name, "__internal__.step");
        assert!(random.as_f64().is_some_and(|value| value != 0.0));
        let captured_random = random.clone();

        let resolved = resolve_all(first, &[json!({"content": "ok"})]);
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&resolved, &tools, "run")
        else {
            panic!("expected the program to finish after replay");
        };

        assert_eq!(
            value,
            json!({"read": {"content": "ok"}, "random": captured_random})
        );
        assert!(matches!(
            tool_state.as_slice(),
            [
                ToolState::Resolved {
                    kind: ToolKind::External,
                    ..
                },
                ToolState::Resolved {
                    kind: ToolKind::Internal,
                    ..
                },
            ]
        ));
    }

    #[test]
    fn records_unserializable_step_errors_and_keeps_later_tool_call() {
        // SymbolName and BigIntMessage return JSON-unrepresentable values; ThrowingMessage covers getters that throw.
        let source = r#"
class ThrowingMessage extends Error {
  get message() { throw new Error("nope"); }
}

class SymbolName extends Error {
  get name() { return Symbol("s"); }
}

class BigIntMessage extends Error {
  get message() { return 10n; }
}

async function main() {
  for (const createError of [
    () => new ThrowingMessage(),
    () => new SymbolName(),
    () => new BigIntMessage(),
  ]) {
    try {
      await step(() => { throw createError(); });
    } catch (_error) {}
  }
  return await tools.test.read_file({ path: "secret.txt" });
}
"#;
        let first = pending(run(&partial(source), &[tool("read_file")], "run"));

        let [
            ToolState::Rejected {
                kind: ToolKind::Internal,
                function: first_step_function,
                ..
            },
            ToolState::Rejected {
                kind: ToolKind::Internal,
                function: second_step_function,
                ..
            },
            ToolState::Rejected {
                kind: ToolKind::Internal,
                function: third_step_function,
                ..
            },
            ToolState::Pending {
                function: read_function,
                ..
            },
        ] = first.tool_state.as_slice()
        else {
            panic!("expected rejected steps and pending read_file call");
        };
        assert_eq!(first_step_function.name, "__internal__.step");
        assert_eq!(second_step_function.name, "__internal__.step");
        assert_eq!(third_step_function.name, "__internal__.step");
        assert_eq!(read_function.name, "read_file");
        assert_eq!(read_function.arguments, json!({"path": "secret.txt"}));

        let resolved = resolve_all(first, &[json!({"content": "secret"})]);
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&resolved, &[tool("read_file")], "run")
        else {
            panic!("expected the program to finish after replay");
        };

        assert_eq!(value, json!({"content": "secret"}));
        assert!(matches!(
            tool_state.as_slice(),
            [
                ToolState::Rejected {
                    kind: ToolKind::Internal,
                    ..
                },
                ToolState::Rejected {
                    kind: ToolKind::Internal,
                    ..
                },
                ToolState::Rejected {
                    kind: ToolKind::Internal,
                    ..
                },
                ToolState::Resolved {
                    kind: ToolKind::External,
                    ..
                },
            ]
        ));
    }

    #[test]
    fn preserves_placeholder_text_in_source_across_partial_evaluation() {
        let source = r#"
async function main() {
  await tools.test.echo({});
  return "__USER_PROGRAM__";
}
"#;

        let first = pending(run(&partial(source), &[tool("echo")], "run"));

        assert_eq!(first.code, source);
    }

    #[test]
    fn preserves_placeholder_text_in_input_and_replayed_tool_state() {
        let source = r#"
async function main(input) {
  const result = await tools.test.echo({});
  return { input: input.value, result: result.value };
}
"#;
        let mut initial = partial(source);
        initial.input = json!({"value": "__TOOLS__"});
        let first = pending(run(&initial, &[tool("echo")], "run"));
        let resolved = resolve_all(first, &[json!({"value": "__USER_PROGRAM__"})]);

        let result = run(&resolved, &[tool("echo")], "run");

        assert!(matches!(
            result,
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                ..
            } if value == json!({"input": "__TOOLS__", "result": "__USER_PROGRAM__"})
        ));
    }

    #[test]
    fn records_step_once_and_replays_its_value() {
        let source = r#"
async function main() {
  const value = await step(() => Math.random());
  return { value };
}
"#;
        let first = run(&partial(source), &[], "run");
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = first
        else {
            panic!("expected success");
        };
        let captured = value["value"].clone();
        assert_eq!(tool_state.len(), 1);
        let state = PartialEvaluation {
            code: source.to_string(),
            input: json!({}),
            tool_state,
        };

        let replay = run(&state, &[], "run");

        assert_eq!(
            replay,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state: state.tool_state,
                result: CodeResult::Success {
                    value: json!({"value": captured})
                },
            }
        );
    }

    #[test]
    fn deterministic_input_controls_time_and_randomness() {
        let source = r#"
async function main() {
  return await step(() => ({ now: Date.now(), random: Math.random(), uuid: crypto.randomUUID() }));
}
"#;
        let context = DeterminismContext {
            time_unix_ms: 1_725_000_000_123,
            random_seed: 42,
        };

        let first = run_typescript(&partial(source), &[], "run", context, 128, 1024).unwrap();
        let replay = run_typescript(&partial(source), &[], "run", context, 128, 1024).unwrap();
        let changed = run_typescript(
            &partial(source),
            &[],
            "run",
            DeterminismContext {
                time_unix_ms: context.time_unix_ms + 1,
                random_seed: context.random_seed + 1,
            },
            128,
            1024,
        )
        .unwrap();

        assert_eq!(replay, first);
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            ..
        } = first
        else {
            panic!("expected deterministic success");
        };
        assert_eq!(value["now"].as_f64(), Some(context.time_unix_ms as f64));
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success {
                value: changed_value,
            },
            ..
        } = changed
        else {
            panic!("expected changed deterministic success");
        };
        assert_ne!(changed_value, value);
    }

    #[test]
    fn max_program_effects_prevents_additional_external_calls() {
        let source = r#"
async function main() {
  return await Promise.all([
    tools.test.first({}),
    tools.test.second({}),
  ]);
}
"#;
        let first = run_typescript(
            &partial(source),
            &[tool("first"), tool("second")],
            "run",
            determinism(),
            1,
            1024,
        )
        .unwrap();
        let resolved = resolve_all(pending(first), &[json!("first")]);

        let completed = run_typescript(
            &resolved,
            &[tool("first"), tool("second")],
            "run",
            determinism(),
            1,
            1024,
        )
        .unwrap();

        let TypeScriptRunResult::CodeResult {
            tool_state,
            result: CodeResult::Error { error },
            ..
        } = completed
        else {
            panic!("expected the program effect limit to fail the program");
        };
        assert_eq!(tool_state, resolved.tool_state);
        assert!(
            error["message"]
                .as_str()
                .is_some_and(|message| message.contains("maxProgramEffects (1)"))
        );
    }

    #[test]
    fn max_program_operations_bounds_internal_step_records() {
        let source = r#"
async function main() {
  const first = await step(() => "first");
  const second = await step(() => "second");
  return { first, second };
}
"#;

        let completed =
            run_typescript(&partial(source), &[], "run", determinism(), 128, 1).unwrap();

        let TypeScriptRunResult::CodeResult {
            tool_state,
            result: CodeResult::Error { error },
            ..
        } = completed
        else {
            panic!("expected the program operation limit to fail the program");
        };
        assert_eq!(tool_state.len(), 1);
        assert!(matches!(
            tool_state[0],
            ToolState::Resolved {
                kind: ToolKind::Internal,
                ..
            }
        ));
        assert!(
            error["message"]
                .as_str()
                .is_some_and(|message| message.contains("maxProgramOperations (1)"))
        );
    }

    #[test]
    fn replays_rejected_tool_calls() {
        let source = r#"
async function main() {
  await tools.test.failure({ value: 1 });
  return "unreachable";
}
"#;
        let mut state = pending(run(&partial(source), &[tool("failure")], "run"));
        let ToolState::Pending { kind, id, function } = state.tool_state.remove(0) else {
            panic!("expected a pending tool");
        };
        state.tool_state.push(ToolState::Rejected {
            kind,
            id,
            function,
            error: json!({"name": "ToolError", "message": "boom"}),
            content: Vec::new(),
        });

        let result = run(&state, &[tool("failure")], "run");

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Error { error },
            tool_state,
            ..
        } = result
        else {
            panic!("expected a rejected code result");
        };
        assert_eq!(error["message"], "boom");
        assert_eq!(tool_state, state.tool_state);
    }

    #[test]
    fn validates_tool_arguments_before_emitting_external_calls() {
        let source = r#"
async function main() {
  return await tools.test.search({});
}
"#;
        let search = tool_with_schema(
            "search",
            json!({
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }),
        );

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Error { error },
            tool_state,
            ..
        } = run(&partial(source), std::slice::from_ref(&search), "run")
        else {
            panic!("expected invalid arguments to fail inside run_typescript");
        };

        assert_eq!(error["type"], "jsonSchemaArgumentValidationError");
        assert_eq!(error["functionName"], "search");
        assert_eq!(error["argumentsValidationErrors"][0]["keyword"], "required");
        assert_eq!(
            error["argumentsValidationErrors"][0]["params"]["missingProperty"],
            "query"
        );
        assert!(matches!(
            tool_state.as_slice(),
            [ToolState::Rejected {
                kind: ToolKind::External,
                ..
            }]
        ));
    }

    #[test]
    fn validation_errors_match_ajv_shape_and_stop_after_the_first_error() {
        let source = r#"
async function main() {
  return await tools.test.search({ query: "" });
}
"#;
        let search = tool_with_schema(
            "search",
            json!({
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1}
                },
                "required": ["query", "limit"]
            }),
        );

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Error { error },
            ..
        } = run(&partial(source), std::slice::from_ref(&search), "run")
        else {
            panic!("expected invalid arguments to fail inside run_typescript");
        };
        let errors = error["argumentsValidationErrors"].as_array().unwrap();
        assert_eq!(errors.len(), 1);
        assert_eq!(errors[0]["keyword"], "required");
        assert_eq!(errors[0]["params"]["missingProperty"], "limit");
        assert_eq!(errors[0]["message"], "must have required property 'limit'");

        let source = r#"
async function main() {
  return await tools.test.search({ query: "", limit: 1 });
}
"#;
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Error { error },
            ..
        } = run(&partial(source), &[search], "run")
        else {
            panic!("expected minLength validation to fail inside run_typescript");
        };
        let error = &error["argumentsValidationErrors"][0];
        assert_eq!(error["keyword"], "minLength");
        assert_eq!(error["params"]["limit"], 1);
        assert_eq!(error["message"], "must NOT have fewer than 1 characters");
    }

    #[test]
    fn non_object_tool_arguments_are_reported_as_schema_validation_errors() {
        let source = r#"
async function main() {
  return await tools.test.search("hello");
}
"#;
        let search = tool_with_schema(
            "search",
            json!({
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }),
        );

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Error { error },
            tool_state,
            ..
        } = run(&partial(source), &[search], "run")
        else {
            panic!("expected non-object arguments to fail validation");
        };
        let error = &error["argumentsValidationErrors"][0];
        assert_eq!(error["keyword"], "type");
        assert_eq!(error["params"]["type"], "object");
        assert_eq!(error["message"], "must be object");
        assert!(matches!(
            tool_state.as_slice(),
            [ToolState::Rejected { .. }]
        ));
    }

    #[test]
    fn rejects_unknown_arguments_for_strict_tool_schemas() {
        let source = r#"
async function main() {
  return await tools.test.search({ query: "hello", max_results: 5 });
}
"#;
        let search = tool_with_schema(
            "search",
            json!({
                "type": "object",
                "additionalProperties": false,
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }),
        );

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Error { error },
            ..
        } = run(&partial(source), &[search], "run")
        else {
            panic!("expected an unknown argument to fail validation");
        };

        assert_eq!(
            error["argumentsValidationErrors"][0]["keyword"],
            "additionalProperties"
        );
        assert_eq!(
            error["argumentsValidationErrors"][0]["params"]["additionalProperty"],
            "max_results"
        );
    }

    #[test]
    fn validation_errors_can_be_caught_by_sandbox_code() {
        let source = r#"
async function main() {
  try {
    await tools.test.search({});
    return "unexpected";
  } catch (error) {
    return error.type;
  }
}
"#;
        let search = tool_with_schema(
            "search",
            json!({
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }),
        );

        assert!(matches!(
            run(&partial(source), &[search], "run"),
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                tool_state,
                ..
            } if value == json!("jsonSchemaArgumentValidationError")
                && matches!(tool_state.as_slice(), [ToolState::Rejected { .. }])
        ));
    }

    #[test]
    fn validation_ignores_external_schema_extension_keywords() {
        let source = r#"
async function main() {
  return await tools.test.search({ query: "hello" });
}
"#;
        let search = tool_with_schema(
            "search",
            json!({
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "x-mcp-annotation": {"connector": "search"}
                    }
                },
                "required": ["query"]
            }),
        );

        let pending = pending(run(&partial(source), &[search], "run"));
        assert!(matches!(
            pending.tool_state.as_slice(),
            [ToolState::Pending {
                kind: ToolKind::External,
                ..
            }]
        ));
    }

    #[test]
    fn discards_stale_suffix_after_control_flow_diverges() {
        let source = r#"
async function main() {
  const branch = await tools.test.branch({});
  if (branch.useLeft) return await tools.test.left({});
  return await tools.test.right({});
}
"#;
        let first = pending(run(
            &partial(source),
            &[tool("branch"), tool("left"), tool("right")],
            "run",
        ));
        let left = pending(run(
            &resolve_all(first, &[json!({"useLeft": true})]),
            &[tool("branch"), tool("left"), tool("right")],
            "run",
        ));
        let completed_left = resolve_all(left, &[json!("left")]);
        let mut changed = completed_left;
        let ToolState::Resolved { result, .. } = &mut changed.tool_state[0] else {
            panic!("expected the branch result to be resolved");
        };
        *result = json!({"useLeft": false});

        let right = pending(run(
            &changed,
            &[tool("branch"), tool("left"), tool("right")],
            "run",
        ));

        assert_eq!(right.tool_state.len(), 2);
        let ToolState::Pending { function, .. } = &right.tool_state[1] else {
            panic!("expected a pending replacement call");
        };
        assert_eq!(function.name, "right");
    }

    #[test]
    fn blocks_unsupported_ambient_effects() {
        for (expression, expected_message) in [
            ("fetch('https://example.com')", "fetch() is not supported"),
            ("setTimeout(() => {}, 1)", "Only zero-delay setTimeout()"),
            ("setInterval(() => {}, 0)", "setInterval() is not supported"),
            ("eval('1 + 1')", "eval() is not supported"),
        ] {
            let source = format!("async function main() {{ return {expression}; }}");
            let TypeScriptRunResult::CodeResult {
                result: CodeResult::Error { error },
                ..
            } = run(&partial(&source), &[], "run")
            else {
                panic!("expected {expression} to fail");
            };
            assert!(
                error["message"]
                    .as_str()
                    .is_some_and(|message| message.contains(expected_message)),
                "unexpected error for {expression}: {error}"
            );
        }
    }

    #[test]
    fn direct_non_idempotent_builtins_create_replayable_internal_steps() {
        let source = r#"
async function main() {
  const now = Date.now();
  const createdAt = new Date().toISOString();
  const dateString = Date(0);
  const random = Math.random();
  const uuid = crypto.randomUUID();
  const values = new Uint8Array(4);
  crypto.getRandomValues(values);
  return { now, createdAt, dateString, random, uuid, values: Array.from(values) };
}
"#;

        let first = run(&partial(source), &[], "run");
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = first
        else {
            panic!("expected direct durable builtins to succeed");
        };
        assert_eq!(tool_state.len(), 6);
        assert!(tool_state.iter().all(|state| matches!(
            state,
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: ToolFunction { name, .. },
                ..
            } if name == "__internal__.step"
        )));
        assert_eq!(
            value["now"].as_f64(),
            Some(determinism().time_unix_ms as f64)
        );
        assert_eq!(value["createdAt"], "2023-11-14T22:13:20.000Z");
        assert!(
            value["dateString"]
                .as_str()
                .is_some_and(|date| !date.is_empty())
        );
        assert!(value["random"].as_f64().is_some());
        assert!(value["uuid"].as_str().is_some_and(|uuid| uuid.len() == 36));
        assert_eq!(value["values"].as_array().map(Vec::len), Some(4));

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &[],
            "run",
        );
        assert_eq!(
            replay,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state,
                result: CodeResult::Success { value },
            }
        );
    }

    #[test]
    fn direct_async_non_idempotent_builtins_preserve_values_and_replay() {
        let source = r#"
async function main() {
  const raced = await Promise.race([Promise.resolve(new Date(0))]);
  const any = await Promise.any([
    Promise.reject(new Error("skip")),
    Promise.resolve(new Date(1000)),
  ]);
  let raceError;
  try {
    await Promise.race([Promise.reject(new TypeError("race failed"))]);
  } catch (error) {
    raceError = {
      isTypeError: error instanceof TypeError,
      message: error.message,
      name: error.name,
    };
  }
  let aggregate;
  try {
    await Promise.any([
      Promise.reject(new TypeError("first")),
      Promise.reject(new RangeError("second")),
    ]);
  } catch (error) {
    aggregate = {
      errorTypes: [error.errors[0] instanceof TypeError, error.errors[1] instanceof RangeError],
      isAggregateError: error instanceof AggregateError,
      name: error.name,
      messages: error.errors.map((item) => item.message),
    };
  }
  return {
    aggregate,
    any: any.toISOString(),
    anyIsDate: any instanceof Date,
    performanceNow: performance.now(),
    raced: raced.toISOString(),
    racedIsDate: raced instanceof Date,
    raceError,
  };
}
"#;

        let first = run(&partial(source), &[], "run");
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = first
        else {
            panic!("expected direct durable async builtins to succeed");
        };
        assert_eq!(tool_state.len(), 5);
        assert!(tool_state.iter().all(|state| matches!(
            state,
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: ToolFunction { name, .. },
                ..
            } if name == "__internal__.step"
        )));
        assert_eq!(
            value,
            json!({
                "aggregate": {
                    "errorTypes": [true, true],
                    "isAggregateError": true,
                    "name": "AggregateError",
                    "messages": ["first", "second"],
                },
                "any": "1970-01-01T00:00:01.000Z",
                "anyIsDate": true,
                "performanceNow": determinism().time_unix_ms as f64,
                "raced": "1970-01-01T00:00:00.000Z",
                "racedIsDate": true,
                "raceError": {
                    "isTypeError": true,
                    "message": "race failed",
                    "name": "TypeError",
                },
            })
        );

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &[],
            "run",
        );
        assert_eq!(
            replay,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state,
                result: CodeResult::Success { value },
            }
        );
    }

    #[test]
    fn direct_promise_selection_records_nested_durable_callbacks_independently() {
        let source = r#"
function deferredValue(label) {
  return Promise.resolve().then(() => {
    const bytes = new Uint8Array(4);
    const random = Math.random();
    const uuid = crypto.randomUUID();
    crypto.getRandomValues(bytes);
    return {
      bytes: Array.from(bytes),
      date: new Date(),
      label,
      now: Date.now(),
      performanceNow: performance.now(),
      random,
      uuid,
    };
  });
}

async function main() {
  const raced = await Promise.race([deferredValue("race")]);
  const any = await Promise.any([deferredValue("any")]);
  return {
    any: { ...any, date: any.date.toISOString(), isDate: any.date instanceof Date },
    raced: { ...raced, date: raced.date.toISOString(), isDate: raced.date instanceof Date },
  };
}
"#;
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&partial(source), &[], "run")
        else {
            panic!("expected nested durable promise callbacks to succeed");
        };
        assert_eq!(tool_state.len(), 14);
        assert!(tool_state.iter().all(|state| matches!(
            state,
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: ToolFunction { name, .. },
                ..
            } if name == "__internal__.step"
        )));
        assert_eq!(value["any"]["isDate"], true);
        assert_eq!(value["raced"]["isDate"], true);

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &[],
            "run",
        );
        assert_eq!(
            replay,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state,
                result: CodeResult::Success { value },
            }
        );
    }

    #[test]
    fn pending_promise_selection_does_not_block_an_unrelated_tool_call() {
        let source = r#"
async function main() {
  const winner = Promise.race([Promise.resolve("winner")]);
  const unrelated = await tools.test.read_file({});
  return { unrelated, winner: await winner };
}
"#;
        let tools = [tool("read_file")];

        let first = pending(run(&partial(source), &tools, "run"));

        assert_eq!(first.tool_state.len(), 2);
        assert!(matches!(
            &first.tool_state[0],
            ToolState::Pending {
                kind: ToolKind::External,
                function: ToolFunction { name, .. },
                ..
            } if name == "read_file"
        ));
        assert!(matches!(
            &first.tool_state[1],
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: ToolFunction { name, .. },
                ..
            } if name == "__internal__.step"
        ));

        let completed = run(&resolve_all(first, &[json!("contents")]), &tools, "run");
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = &completed
        else {
            panic!("expected the unrelated tool and promise selection to succeed");
        };
        assert_eq!(value, &json!({"unrelated": "contents", "winner": "winner"}));
        assert_eq!(tool_state.len(), 2);
        assert!(matches!(
            &tool_state[1],
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: ToolFunction { name, .. },
                ..
            } if name == "__internal__.step"
        ));

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &tools,
            "run",
        );
        assert_eq!(replay, completed);
    }

    #[test]
    fn promise_selection_preserves_a_local_winner_while_a_sibling_tool_is_pending() {
        let cases = [
            (
                "race fulfillment",
                r#"async function main() {
  return await Promise.race([
    tools.test.slow({}),
    Promise.resolve("local"),
  ]);
}"#,
                json!("local"),
            ),
            (
                "race rejection",
                r#"async function main() {
  try {
    return await Promise.race([
      tools.test.slow({}),
      Promise.reject(new TypeError("local")),
    ]);
  } catch (error) {
    return { name: error.name, message: error.message };
  }
}"#,
                json!({"name": "TypeError", "message": "local"}),
            ),
            (
                "any fulfillment",
                r#"async function main() {
  return await Promise.any([
    tools.test.slow({}),
    Promise.resolve("local"),
  ]);
}"#,
                json!("local"),
            ),
        ];
        let tools = [tool("slow")];

        for (label, source, expected) in cases {
            let first = pending(run(&partial(source), &tools, "run"));
            assert_eq!(first.tool_state.len(), 2, "{label}");
            assert!(matches!(
                &first.tool_state[..],
                [
                    ToolState::Pending {
                        kind: ToolKind::External,
                        ..
                    },
                    ToolState::Resolved {
                        kind: ToolKind::Internal,
                        function: ToolFunction { name, .. },
                        ..
                    }
                ] if name == "__internal__.step"
            ));

            let completed = run(&resolve_all(first, &[json!("tool")]), &tools, "run");
            let TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                tool_state,
                ..
            } = &completed
            else {
                panic!("expected {label} to complete");
            };
            assert_eq!(value, &expected, "{label}");

            let replay = run(
                &PartialEvaluation {
                    code: source.to_string(),
                    input: json!({}),
                    tool_state: tool_state.clone(),
                },
                &tools,
                "run",
            );
            assert_eq!(replay, completed, "{label}");
        }
    }

    #[test]
    fn concurrent_promise_selections_record_independent_outcomes() {
        let source = r#"
async function main() {
  const [raced, selected] = await Promise.all([
    Promise.race([Promise.resolve("race")]),
    Promise.any([Promise.resolve("any")]),
  ]);
  return { raced, selected };
}
"#;

        let first = run(&partial(source), &[], "run");
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = &first
        else {
            panic!("expected concurrent promise selections to succeed");
        };
        assert_eq!(value, &json!({"raced": "race", "selected": "any"}));
        assert_eq!(tool_state.len(), 2);
        assert!(tool_state.iter().all(|state| matches!(
            state,
            ToolState::Resolved {
                kind: ToolKind::Internal,
                function: ToolFunction { name, .. },
                ..
            } if name == "__internal__.step"
        )));

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &[],
            "run",
        );
        assert_eq!(replay, first);
    }

    #[test]
    fn promise_selection_callbacks_may_call_tools() {
        let source = r#"
async function main() {
  return await Promise.any([
    Promise.resolve().then(() => tools.test.read_file({})),
  ]);
}
"#;
        let tools = [tool("read_file")];

        let first = pending(run(&partial(source), &tools, "run"));
        let completed = run(&resolve_all(first, &[json!("contents")]), &tools, "run");

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = &completed
        else {
            panic!("expected the promise selection callback tool to succeed");
        };
        assert_eq!(tool_state.len(), 2);
        assert_eq!(value, &json!("contents"));

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &tools,
            "run",
        );
        assert_eq!(replay, completed);
    }

    #[test]
    fn direct_promise_selection_consumes_thenables_once_in_iterator_order() {
        let source = r#"
function trackedIterable(label) {
  let index = 0;
  let thenCalls = 0;
  const order = [];
  return {
    get result() { return { order, thenCalls }; },
    values: {
      [Symbol.iterator]() {
        return {
          next() {
            order.push(`next:${label}:${index}`);
            if (index === 2) return { done: true };
            const value = index;
            index += 1;
            return {
              done: false,
              value: Object.defineProperty({}, "then", {
                get() {
                  order.push(`then:${label}:${value}`);
                  return (resolve) => {
                    thenCalls += 1;
                    resolve(value);
                  };
                },
              }),
            };
          },
        };
      },
    },
  };
}

async function main() {
  const race = trackedIterable("race");
  const raced = await Promise.race(race.values);
  const any = trackedIterable("any");
  const selected = await Promise.any(any.values);
  return { any: any.result, race: race.result, raced, selected };
}
"#;
        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&partial(source), &[], "run")
        else {
            panic!("expected custom promise inputs to succeed");
        };
        assert_eq!(
            value,
            json!({
                "any": {
                    "order": ["next:any:0", "then:any:0", "next:any:1", "then:any:1", "next:any:2"],
                    "thenCalls": 2,
                },
                "race": {
                    "order": ["next:race:0", "then:race:0", "next:race:1", "then:race:1", "next:race:2"],
                    "thenCalls": 2,
                },
                "raced": 0,
                "selected": 0,
            })
        );

        let replay = run(
            &PartialEvaluation {
                code: source.to_string(),
                input: json!({}),
                tool_state: tool_state.clone(),
            },
            &[],
            "run",
        );
        assert_eq!(
            replay,
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state,
                result: CodeResult::Success { value },
            }
        );
    }

    #[test]
    fn allows_local_non_idempotent_apis_inside_explicit_step() {
        let source = r#"
async function main() {
  return await step(async () => {
    const values = new Uint8Array(4);
    crypto.getRandomValues(values);
    await new Promise((resolve) => setTimeout(resolve, 0));
    return {
      any: await Promise.any([Promise.reject(new Error("skip")), Promise.resolve("any")]),
      now: Date.now(),
      performanceNow: performance.now(),
      raced: await Promise.race([Promise.resolve("winner")]),
      random: Math.random(),
      uuid: crypto.randomUUID(),
      values: Array.from(values),
    };
  });
}
"#;

        let TypeScriptRunResult::CodeResult {
            result: CodeResult::Success { value },
            tool_state,
            ..
        } = run(&partial(source), &[], "run")
        else {
            panic!("expected explicit step APIs to succeed");
        };
        assert_eq!(tool_state.len(), 1);
        assert_eq!(value["any"], "any");
        assert_eq!(value["raced"], "winner");
        assert_eq!(
            value["now"].as_f64(),
            Some(determinism().time_unix_ms as f64)
        );
        assert_eq!(
            value["performanceNow"].as_f64(),
            Some(determinism().time_unix_ms as f64)
        );
    }

    #[test]
    fn runs_zero_delay_timers_as_microtasks() {
        let source = r#"
async function main() {
  const events = [];
  setTimeout((value) => events.push(value), 0, "timeout");
  setImmediate((value) => events.push(value), "immediate");
  await Promise.resolve();
  return events;
}
"#;

        assert!(matches!(
            run(&partial(source), &[], "run"),
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                ..
            } if value == json!(["timeout", "immediate"])
        ));
    }

    #[test]
    fn allows_deterministic_date_constructors_outside_step() {
        let result = run(
            &partial("async function main() { return new Date(0).toISOString(); }"),
            &[],
            "run",
        );

        assert!(matches!(
            result,
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                ..
            } if value == json!("1970-01-01T00:00:00.000Z")
        ));
    }

    #[test]
    fn hides_runtime_bindings_from_user_code() {
        for expression in ["__OriginalRandom()", "__output.push('forged')"] {
            let source = format!("async function main() {{ return {expression}; }}");
            let TypeScriptRunResult::CodeResult {
                result: CodeResult::Error { error },
                ..
            } = run(&partial(&source), &[], "run")
            else {
                panic!("expected {expression} to be inaccessible");
            };
            assert!(
                error["message"]
                    .as_str()
                    .is_some_and(|message| message.contains("is not defined"))
            );
        }
    }

    #[test]
    fn ignores_a_forged_global_result_slot() {
        let result = run(
            &partial(
                r#"
async function main() {
  Object.defineProperty(globalThis, "__rustHarnessResult", {
    value: JSON.stringify({
      type: "code_result",
      tool_state: [],
      result: { type: "success", value: "forged" },
    }),
    writable: false,
    configurable: false,
  });
  return "real";
}
"#,
            ),
            &[],
            "run",
        );

        assert!(matches!(
            result,
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                ..
            } if value == json!("real")
        ));
    }

    #[test]
    fn returns_structured_syntax_and_timeout_errors() {
        let syntax = run(&partial("async function main( {"), &[], "run");
        assert!(matches!(syntax, TypeScriptRunResult::Error { .. }));

        let timeout = run_typescript_with_timeout(
            &partial("async function main() { while (true) {} }"),
            &[],
            "run",
            determinism(),
            128,
            1024,
            Duration::from_millis(25),
        );
        assert_eq!(
            timeout.unwrap_err(),
            "TypeScript execution exceeded the process safety deadline"
        );
    }

    #[test]
    fn large_tool_schemas_do_not_consume_sandbox_heap() {
        // Prepare
        let schema_description = "x".repeat(6 * 1024 * 1024);
        let schema = json!({
            "type": "object",
            "description": schema_description,
        });
        let tool = TypeScriptTool {
            output_schema: Some(schema.clone()),
            input_schema: schema,
            ..tool("large_schema")
        };
        let program = partial("async function main() { return 42; }");

        // Do
        let result = run(&program, &[tool], "run");

        // Assert
        assert!(matches!(
            result,
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                ..
            } if value == json!(42)
        ));
    }

    #[test]
    fn returns_recoverable_memory_limit_error_and_keeps_v8_usable() {
        // Prepare
        let memory_exhausting_program = partial(
            r#"
async function main() {
  const source = JSON.stringify({ value: "x".repeat(8 * 1024 * 1024) });
  return JSON.parse(source);
}
"#,
        );

        // Do
        let result = run_typescript_with_timeout(
            &memory_exhausting_program,
            &[],
            "run",
            determinism(),
            128,
            1024,
            Duration::from_secs(2),
        );
        let subsequent_result = run(
            &partial("async function main() { return 42; }"),
            &[],
            "after-memory-limit",
        );

        // Assert
        assert_eq!(
            result.unwrap(),
            TypeScriptRunResult::CodeResult {
                stdout: None,
                stderr: None,
                tool_state: Vec::new(),
                result: CodeResult::Error {
                    error: json!({
                        "name": "MemoryLimitError",
                        "message": "run_typescript exceeded its 16 MiB memory limit with 0 recorded operations. Retry with fewer tool calls in each run_typescript block, request smaller tool results, or split the work across multiple run_typescript calls. Return only the fields needed for the next step instead of every full tool result.",
                        "details": {
                            "memoryLimitMiB": 16,
                            "operationCount": 0,
                        },
                    }),
                },
            }
        );
        assert!(matches!(
            subsequent_result,
            TypeScriptRunResult::CodeResult {
                result: CodeResult::Success { value },
                ..
            } if value == json!(42)
        ));
    }
}
