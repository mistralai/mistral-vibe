#![recursion_limit = "256"]
// The featureless crate has no host entry point, so its private core is intentionally unreachable.
#![cfg_attr(
    not(any(
        feature = "benchmark",
        feature = "node-binding",
        feature = "python-binding"
    )),
    allow(dead_code)
)]

#[cfg(feature = "python-binding")]
use pyo3::prelude::*;

#[cfg(feature = "benchmark")]
pub mod benchmark;

#[cfg(feature = "benchmark")]
pub mod tool_discovery_benchmark {
    pub fn run(arguments: impl Iterator<Item = String>) -> Result<(), String> {
        crate::core::run_tool_discovery_benchmark(arguments)
    }
}

mod core;
mod engine;
#[cfg(feature = "node-binding")]
mod node_binding;
#[cfg(test)]
#[path = "tests/mod.rs"]
mod public_interface_tests;
#[cfg(feature = "python-binding")]
mod python_binding;

#[cfg(feature = "python-binding")]
#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    python_binding::register(module)
}
