# Mistral Vibe — SF Hackathon Project

This project was built for the **Mistral San Francisco Hackathon**.

## What it does

- Add a **voice command feature** to interact with the CLI faster.
- Build a **context-awareness plugin flow** to improve UX/UI outputs from ongoing conversation context.

## Quickstart

### 1) Clone

```bash
git clone <your-repo-url>
cd mistral-vibe
```

### 2) Install `uv` (if needed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3) Install dependencies

```bash
uv sync
```

### 4) Run

```bash
uv run vibemic
```

## Optional: Install as Global CLI

```bash
uv tool install --force .
vibemic
```

If `vibemic` is not found, ensure `~/.local/bin` is in your `PATH`.

## In-app commands

- Enable UX/UI plugin mode

```bash
/plugin ux-ui
```

- Disable plugin mode

```bash
/plugin off
```

## API Key

Set your Mistral key before running:

```bash
export MISTRAL_API_KEY="your_api_key"
```

## Voice

Use **Ctrl+S** in the TUI to toggle push-to-talk voice capture.

---

Built during hackathon iteration: fast, practical, and focused on real interaction improvements.
