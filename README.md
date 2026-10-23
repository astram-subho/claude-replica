# claude-replica

A command-line coding agent that runs Claude through the Anthropic API with the same kind of tools and working habits Claude Code uses.

## What "replica" means here

Model weights can't be copied, and Anthropic doesn't publish them. What you can reproduce is the setup around the model:

- **Same model.** It calls `claude-opus-5-5` with adaptive thinking, so the reasoning comes from that model itself. Nothing here distills or imitates it.
- **Same kind of harness.** It runs an agent loop with shell, read, write, edit, glob and grep tools, scoped to one project directory.
- **Similar working principles.** `replica/system_prompt.md` covers reading before changing, keeping changes minimal, checking your work, reporting results honestly and confirming before destructive actions. These are written from scratch; it is not Claude Code's actual system prompt.

## Setup

```bash
pip install -e '.[dev]'
export ANTHROPIC_API_KEY=sk-ant-...   # or: ant auth login
```

## Usage

```bash
replica                                   # interactive session in the current directory
replica "add type hints to utils.py"      # one-shot task
replica --root ../other-project --effort xhigh --show-thinking
replica --yes "run the tests and fix failures"   # skip approval prompts
```

| Flag | Default | Meaning |
|---|---|---|
| `--root` | `.` | Directory the tools work in. Paths outside it are rejected. |
| `--model` | `claude-opus-5-5` | Any Claude model ID. |
| `--effort` | `high` | `low` … `max`. Higher means deeper reasoning and more tokens. |
| `--show-thinking` | off | Streams a summary of the model's reasoning as it works. |
| `--yes` | off | Runs `bash`, `write_file` and `edit_file` without asking first. |

## How it works

- `replica/agent.py` streams each turn. If the model asks for tools, it checks each input against the tool's schema, runs the tool, and sends the results back. It repeats until the model answers in plain text. History is append-only, with thinking blocks passed back unchanged. If a safety classifier declines a request, it is re-run on Anthropic's recommended fallback model (`fallbacks: "default"`).
- `replica/tools.py` holds the tool definitions and how they run. Every path is resolved inside `--root`. Tool output is cut off at 30,000 characters.

## Try it in the browser

`web/playground.html` is the same agent loop as a single web page. It runs on your own Claude account, so you don't need an API key. Publish it as a claude.ai Artifact with the `sample` capability.

The page doesn't have the CLI's shell. Instead it works on a virtual project folder: `glob`, `read_file`, `write_file`, `edit_file` and `grep` behave as they do in the CLI, and `run_js` runs a JavaScript file in a sandboxed worker with Node-style `require`. Claude gets only a few tool rounds per message there, so larger tasks can take more than one message.

## Tests

```bash
pytest
```

The tests use a fake client, so they don't need an API key.
