use std::hint::black_box;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use super::{DiscoveryTool, SearchMode, SearchRequest, ToolDiscovery};

const DEFAULT_TOOL_COUNT: usize = 500;
const DEFAULT_BUILD_ITERATIONS: usize = 50;
const DEFAULT_BEST_MATCH_ITERATIONS: usize = 200;
const DEFAULT_DETAILS_ITERATIONS: usize = 2_000;
const EXPECTED_BEST_MATCH_P95_MS: f64 = 10.0;

pub(crate) fn run(arguments: impl Iterator<Item = String>) -> Result<(), String> {
    let options = Options::parse(arguments)?;
    let report = benchmark(&options);
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
    );
    Ok(())
}

fn benchmark(options: &Options) -> Value {
    let catalog_timings = measure(options.build_iterations, || {
        black_box(build_catalog(options.tool_count));
    });
    let discovery = build_catalog(options.tool_count);
    let best_match_request = SearchRequest {
        query: Some("search email received today".to_string()),
        ..SearchRequest::default()
    };
    let details_request = SearchRequest {
        mode: SearchMode::Details,
        functions: (0..10.min(options.tool_count))
            .map(|index| format!("connector_0.search_email_records_{index}"))
            .collect(),
        ..SearchRequest::default()
    };
    let best_match_timings = measure(options.best_match_iterations, || {
        black_box(discovery.search_with_summary(best_match_request.clone()));
    });
    let details_timings = measure(options.details_iterations, || {
        black_box(discovery.search_with_summary(details_request.clone()));
    });
    let best_match_p95_ms = milliseconds(best_match_timings.p95);

    json!({
        "benchmark": "tool_discovery",
        "profile": if cfg!(debug_assertions) { "debug" } else { "release" },
        "tool_count": options.tool_count,
        "iterations": {
            "catalog_and_schema_render": options.build_iterations,
            "best_match": options.best_match_iterations,
            "details": options.details_iterations,
        },
        "timings_ms": {
            "catalog_and_schema_render_median": milliseconds(catalog_timings.median),
            "catalog_and_schema_render_p95": milliseconds(catalog_timings.p95),
            "best_match_median": milliseconds(best_match_timings.median),
            "best_match_p95": best_match_p95_ms,
            "details_median": milliseconds(details_timings.median),
            "details_p95": milliseconds(details_timings.p95),
        },
        "expected_best_match_p95_ms": EXPECTED_BEST_MATCH_P95_MS,
        "meets_expected_order_of_magnitude": best_match_p95_ms < EXPECTED_BEST_MATCH_P95_MS,
    })
}

fn build_catalog(tool_count: usize) -> ToolDiscovery {
    let input_schema = input_schema();
    let output_schema = output_schema();
    let categories = ["email", "calendar", "document", "message", "issue"];
    let mut discovery = ToolDiscovery::with_capacity(tool_count);
    for index in 0..tool_count {
        let connector_index = index / 25;
        let namespace = format!("connector_{connector_index}");
        let category = categories[connector_index % categories.len()];
        let name = format!("search_{category}_records_{index}");
        let description = format!("Search {category} records by query, labels, and updated date");
        let integration_description = format!("{category} connector");
        discovery.add_tool(DiscoveryTool {
            namespace: &namespace,
            name: &name,
            description: &description,
            input_schema: &input_schema,
            output_schema: Some(&output_schema),
            integration_description: &integration_description,
            connector_icon_url: None,
        });
    }
    discovery
}

fn input_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "query": { "type": "string", "description": "Search query" },
            "after": { "type": "string", "description": "Lower time bound" },
            "before": { "type": "string", "description": "Upper time bound" },
            "labels": { "type": "array", "items": { "type": "string" } },
            "limit": { "type": "integer", "minimum": 1, "maximum": 100 }
        },
        "required": ["query"],
        "additionalProperties": false
    })
}

fn output_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": { "type": "string" },
                        "title": { "type": "string" },
                        "updatedAt": { "type": "string" }
                    },
                    "required": ["id", "title"]
                }
            },
            "nextPageToken": { "type": "string" }
        },
        "required": ["items"]
    })
}

struct TimingSummary {
    median: Duration,
    p95: Duration,
}

fn measure(iterations: usize, mut operation: impl FnMut()) -> TimingSummary {
    let mut samples = (0..iterations)
        .map(|_| {
            let started = Instant::now();
            operation();
            started.elapsed()
        })
        .collect::<Vec<_>>();
    samples.sort_unstable();
    TimingSummary {
        median: samples[iterations / 2],
        p95: samples[(iterations - 1) * 95 / 100],
    }
}

fn milliseconds(duration: Duration) -> f64 {
    duration.as_secs_f64() * 1_000.0
}

struct Options {
    tool_count: usize,
    build_iterations: usize,
    best_match_iterations: usize,
    details_iterations: usize,
}

impl Options {
    fn parse(mut arguments: impl Iterator<Item = String>) -> Result<Self, String> {
        let mut options = Self {
            tool_count: DEFAULT_TOOL_COUNT,
            build_iterations: DEFAULT_BUILD_ITERATIONS,
            best_match_iterations: DEFAULT_BEST_MATCH_ITERATIONS,
            details_iterations: DEFAULT_DETAILS_ITERATIONS,
        };
        while let Some(argument) = arguments.next() {
            let value = arguments
                .next()
                .ok_or_else(|| format!("missing value for {argument}"))?;
            let parsed = value
                .parse::<usize>()
                .map_err(|_| format!("invalid positive integer for {argument}: {value}"))?;
            if parsed == 0 {
                return Err(format!("{argument} must be greater than zero"));
            }
            match argument.as_str() {
                "--tools" => options.tool_count = parsed,
                "--build-iterations" => options.build_iterations = parsed,
                "--best-match-iterations" => options.best_match_iterations = parsed,
                "--details-iterations" => options.details_iterations = parsed,
                _ => return Err(format!("unknown argument: {argument}")),
            }
        }
        Ok(options)
    }
}
