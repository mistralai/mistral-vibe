fn main() {
    if let Err(error) = _native::tool_discovery_benchmark::run(std::env::args().skip(1)) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
