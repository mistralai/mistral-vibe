# Project Management Scripts

This directory contains scripts that support project versioning, deployment workflows, and Docker sandbox management.

## Versioning

### Usage

```bash
# Bump major version (1.0.0 -> 2.0.0)
uv run scripts/bump_version.py major

# Bump minor version (1.0.0 -> 1.1.0)
uv run scripts/bump_version.py minor

# Bump patch/micro version (1.0.0 -> 1.0.1)
uv run scripts/bump_version.py micro
# or
uv run scripts/bump_version.py patch
```

## Docker Sandbox

The `build_sandbox.sh` script builds the Docker image with the following features:

- Creates a non-root user `vibeuser` (UID 1000 by default)
- Installs all dependencies as the non-root user
- Supports custom UID/GID for better host-container user mapping

### Usage

```bash
# Build with default UID/GID (1000:1000)
./build_sandbox.sh

# Build with custom UID/GID (recommended for proper file permissions)
./build_sandbox.sh $(id -u) $(id -g)
```
