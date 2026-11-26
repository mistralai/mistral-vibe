#!/usr/bin/env bash

# Mistral Vibe Installation Script
# This script installs or upgrades mistral-vibe with uv (installing uv if needed),
# or upgrades it with Homebrew when Homebrew installed it

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

ORIGINAL_PATH="${PATH}"

# Bump both together when updating uv.
UV_INSTALLER_VERSION="0.11.26"
UV_INSTALLER_SHA256="92fa9085d24c214bb4445cc1da8c15ca9cca8cffb34726240fa08c5302e94ccc"

# TLS-inspecting corporate proxies install their root CA in the OS trust store,
# which curl already uses but uv ignores by default. Make uv trust it too;
# set UV_SYSTEM_CERTS=false to keep uv's bundled roots.
if [[ -z "${UV_SYSTEM_CERTS:-}" && -z "${UV_NATIVE_TLS:-}" ]]; then
    export UV_SYSTEM_CERTS=true
fi

function error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

function info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

function success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

function warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

function find_command_in_path() {
    local cmd="$1"
    local path_value="$2"
    PATH="$path_value" command -v "$cmd" 2>/dev/null || true
}

function print_missing_path_instructions() {
    local executable_name="$1"
    local executable_path="$2"
    local bin_dir
    bin_dir=$(dirname "$executable_path")

    warning "Installation completed, and '$executable_name' was installed at: $executable_path"
    error "Your PATH does not include the folder that contains '$executable_name'."
    error "Add this directory to your shell profile, then restart your terminal:"
    error "  export PATH=\"$bin_dir:\$PATH\""
}

function check_platform() {
    local platform=$(uname -s)

    if [[ "$platform" == "Linux" ]]; then
        info "Detected Linux platform"
        PLATFORM="linux"
    elif [[ "$platform" == "Darwin" ]]; then
        info "Detected macOS platform"
        PLATFORM="macos"
    else
        error "Unsupported platform: $platform"
        error "This installation script currently only supports Linux and macOS"
        exit 1
    fi
}

function check_uv_installed() {
    if command -v uv >/dev/null 2>&1; then
        info "uv is already installed: $(uv --version)"
        UV_INSTALLED=true
    else
        info "uv is not installed"
        UV_INSTALLED=false
    fi
}

function sha256_of() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    else
        return 1
    fi
}

function install_uv() {
    info "Installing uv ${UV_INSTALLER_VERSION} using the official Astral installer..."

    if ! command -v curl &> /dev/null; then
        error "curl is required to install uv. Please install curl first."
        exit 1
    fi

    local installer
    installer=$(mktemp)
    trap "rm -f $(printf '%q' "$installer")" EXIT

    if ! curl -LsSf "https://astral.sh/uv/${UV_INSTALLER_VERSION}/install.sh" -o "$installer"; then
        error "Failed to download the uv installer"
        exit 1
    fi

    local actual_sha256
    if ! actual_sha256=$(sha256_of "$installer"); then
        error "No SHA-256 tool (sha256sum or shasum) available to verify the uv installer"
        exit 1
    fi

    if [[ "$actual_sha256" != "$UV_INSTALLER_SHA256" ]]; then
        error "uv installer checksum mismatch — refusing to execute an unverified installer."
        error "  expected: $UV_INSTALLER_SHA256"
        error "  actual:   $actual_sha256"
        exit 1
    fi

    if sh "$installer"; then
        success "uv installed successfully"

        export PATH="$HOME/.local/bin:$PATH"

        if ! command -v uv &> /dev/null; then
            warning "uv was installed but not found in PATH for this session"
            warning "You may need to restart your terminal or run:"
            warning "  export PATH=\"\$HOME/.cargo/bin:\$HOME/.local/bin:\$PATH\""
        fi
    else
        error "Failed to install uv"
        exit 1
    fi
}

# Only a uv tool install can be upgraded with `uv tool upgrade`; a vibe on PATH
# may come from Homebrew, pip or pipx instead.
function uv_has_vibe() {
    # Captured, not piped: under pipefail, `grep -q` exiting early can SIGPIPE uv.
    local uv_tools
    uv_tools=$(uv --color never tool list 2>/dev/null || true)
    grep -q '^mistral-vibe ' <<<"$uv_tools"
}

function brew_has_vibe() {
    command -v brew >/dev/null 2>&1 && brew list --formula mistral-vibe >/dev/null 2>&1
}

function upgrade_vibe_with_brew() {
    info "Updating mistral-vibe using Homebrew..."
    if ! brew upgrade mistral-vibe </dev/null; then
        error "Homebrew could not upgrade mistral-vibe (is it pinned? see 'brew list --pinned')"
        exit 1
    fi
    success "Mistral Vibe updated successfully!"
}

function uv_vibe_path() {
    local uv_bin_dir
    uv_bin_dir=$(uv --color never tool dir --bin 2>/dev/null || true)
    if [[ -z "$uv_bin_dir" || ! -x "$uv_bin_dir/vibe" ]]; then
        error "Installation completed but 'vibe' command not found"
        error "uv did not expose a 'vibe' executable in the expected tools directory."
        error "Please check your installation and PATH settings"
        return 1
    fi
    echo "$uv_bin_dir/vibe"
}

function report_vibe() {
    local new_vibe="$1" path_vibe
    path_vibe=$(find_command_in_path vibe "$ORIGINAL_PATH")
    if [[ -z "$path_vibe" ]]; then
        print_missing_path_instructions "vibe" "$new_vibe"
        exit 1
    fi
    if [[ ! "$path_vibe" -ef "$new_vibe" ]]; then
        warning "Another 'vibe' at $path_vibe runs instead of the new one at $new_vibe"
        warning "Uninstall it, or put $(dirname "$new_vibe") first on your PATH"
        return
    fi
    success "Installation completed successfully!"
    echo
    echo "You can now run vibe with:"
    echo "  vibe"
    echo
    echo "Or for ACP mode:"
    echo "  vibe-acp"
}

# Install on the GIL-enabled build of Python 3.14 (Vibe supports 3.12+): since
# 3.14, uv may select a free-threaded (no-GIL) interpreter without an explicit
# request, and the locked dependencies ship no cp314t wheels. uv cannot download
# a `+gil` request, so seed a managed GIL 3.14 first when no local one satisfies it.
function uv_tool_gil() {
    local subcommand="$1"
    shift
    uv tool "$subcommand" --python cpython-3.14+gil "$@" 2>/dev/null ||
        { uv python install cpython-3.14 && uv tool "$subcommand" --python cpython-3.14+gil "$@"; }
}

function install_vibe() {
    info "Installing mistral-vibe from GitHub repository using uv..."

    local uv_bin_dir replaced=false
    uv_bin_dir=$(uv --color never tool dir --bin 2>/dev/null || true)
    if [[ -n "$uv_bin_dir" ]] && [[ -e "$uv_bin_dir/vibe" || -L "$uv_bin_dir/vibe" ]]; then
        replaced=true
    fi

    # --force replaces vibe commands another installer (pipx, pip --user) left in uv's bin dir.
    uv_tool_gil install --force mistral-vibe

    success "Mistral Vibe installed successfully! (commands: vibe, vibe-acp)"
    if [[ "$replaced" == "true" ]]; then
        warning "Replaced an existing 'vibe' in $uv_bin_dir"
        warning "If pip or pipx installed it and you uninstall that package later, run this script again"
    fi
}

function update_vibe() {
    info "Updating mistral-vibe from GitHub repository using uv..."
    uv_tool_gil upgrade mistral-vibe

    success "Mistral Vibe updated successfully!"
}

function main() {
    echo
    echo "██████████████████░░"
    echo "██████████████████░░"
    echo "████  ██████  ████░░"
    echo "████    ██    ████░░"
    echo "████          ████░░"
    echo "████  ██  ██  ████░░"
    echo "██      ██      ██░░"
    echo "██████████████████░░"
    echo "██████████████████░░"
    echo
    echo "Starting Mistral Vibe installation..."
    echo

    check_platform

    check_uv_installed

    local new_vibe
    if [[ "$UV_INSTALLED" == "true" ]] && uv_has_vibe; then
        update_vibe
        new_vibe=$(uv_vibe_path)
    elif brew_has_vibe; then
        upgrade_vibe_with_brew
        new_vibe="$(brew --prefix)/bin/vibe"
    else
        if [[ "$UV_INSTALLED" == "false" ]]; then
            install_uv
        fi
        install_vibe
        new_vibe=$(uv_vibe_path)
    fi

    report_vibe "$new_vibe"
}

main
