# Mistral Vibe

[![PyPI Version](https://img.shields.io/pypi/v/mistral-vibe)](https://pypi.org/project/mistral-vibe)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/release/python-3120/)
[![CI Status](https://github.com/mistralai/mistral-vibe/actions/workflows/ci.yml/badge.svg)](https://github.com/mistralai/mistral-vibe/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mistralai/mistral-vibe)](https://github.com/mistralai/mistral-vibe/blob/main/LICENSE)

```
██████████████████░░
██████████████████░░
████  ██████  ████░░
████    ██    ████░░
████          ████░░
████  ██  ██  ████░░
██      ██      ██░░
██████████████████░░
██████████████████░░
```

Mistral Vibe is an open-source coding agent for your terminal. Describe what you want, and it reads your code, edits files and runs commands, asking before it does anything risky.

**Read the [documentation](https://docs.mistral.ai/vibe/code/overview) to learn more.**

## Get started

Install Vibe on macOS or Linux:

```bash
curl -LsSf https://mistral.ai/vibe/install.sh | bash
```

On Windows, install [uv](https://docs.astral.sh/uv/) first:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then, in a new terminal:

```powershell
uv tool install mistral-vibe
```

If you already use uv, `uv tool install mistral-vibe` works on any platform.

Once installed, run `vibe` in your project and sign in with your Mistral account or an API key. Type `/help` to see every command.

<details>
<summary>Install troubleshooting</summary>

If a new terminal says `vibe` is not found, run `uv tool update-shell` and open a new terminal.

Vibe needs a GIL-enabled build of Python 3.12 or later. If you have a free-threaded (no-GIL) Python and the install fails, use the GIL build of Python 3.14:

```bash
uv python install cpython-3.14
uv tool install --python cpython-3.14+gil mistral-vibe
```

</details>

## Documentation

- [Work with the CLI](https://docs.mistral.ai/vibe/code/cli/work-with-cli)
- [Configuration](https://docs.mistral.ai/vibe/code/cli/configuration)
- [Commands and shortcuts](https://docs.mistral.ai/vibe/code/cli/commands-shortcuts)
- [Safety, approvals and permissions](https://docs.mistral.ai/vibe/code/safety-approvals-permissions)
- [Use Vibe in other IDEs](https://docs.mistral.ai/vibe/code/use-vibe-in-other-ides)
- [Programmatic mode, managed shell tools and proxy setup](https://github.com/mistralai/mistral-vibe/blob/main/docs/README.md)
- [Changelog](https://github.com/mistralai/mistral-vibe/blob/main/CHANGELOG.md)

## Feedback

Bug reports and feature requests are welcome; we do not accept pull requests. See [CONTRIBUTING](https://github.com/mistralai/mistral-vibe/blob/main/CONTRIBUTING.md).

## Data collection and usage

Use of Vibe is subject to our [Privacy Policy](https://legal.mistral.ai/terms/privacy-policy). Set `enable_telemetry = false` in `config.toml` to turn off telemetry and crash reporting.

## License

Copyright 2025 Mistral AI. Licensed under the [Apache License, Version 2.0](https://github.com/mistralai/mistral-vibe/blob/main/LICENSE).
