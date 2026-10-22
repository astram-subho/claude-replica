"""Command-line entry point: one-shot (`replica "task"`) or interactive REPL (`replica`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anthropic

from .agent import MODEL, Agent, Callbacks
from .tools import Toolbox

DIM, CYAN, RED, RESET = "\033[2m", "\033[36m", "\033[31m", "\033[0m"


def _summarize(args: dict) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= 160 else text[:157] + "..."


def _make_approver(auto_approve: bool):
    def approve(name: str, args: dict) -> bool:
        if auto_approve:
            return True
        detail = args.get("command") or args.get("path")
        answer = input(f"{CYAN}Allow {name}: {detail}? [y/N] {RESET}").strip().lower()
        return answer in ("y", "yes")

    return approve


def _callbacks() -> Callbacks:
    def on_tool_result(name: str, result: str, is_error: bool) -> None:
        first = result.strip().splitlines()[0] if result.strip() else ""
        color = RED if is_error else DIM
        print(f"{color}  ⎿ {first[:200]}{RESET}", flush=True)

    return Callbacks(
        on_text=lambda t: print(t, end="", flush=True),
        on_thinking=lambda t: print(f"{DIM}{t}{RESET}", end="", flush=True),
        on_tool_call=lambda n, a: print(f"\n{CYAN}● {n}{RESET} {DIM}{_summarize(a)}{RESET}", flush=True),
        on_tool_result=on_tool_result,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replica", description=__doc__)
    parser.add_argument("prompt", nargs="*", help="Task to run; omit for an interactive session.")
    parser.add_argument("--root", default=".", help="Project directory the tools operate in.")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--show-thinking", action="store_true", help="Stream summarized reasoning.")
    parser.add_argument("--yes", action="store_true", help="Run shell commands and file writes without asking.")
    opts = parser.parse_args(argv)

    agent = Agent(
        toolbox=Toolbox(Path(opts.root), approve=_make_approver(opts.yes)),
        model=opts.model,
        effort=opts.effort,
        show_thinking=opts.show_thinking,
        callbacks=_callbacks(),
    )

    def ask(prompt: str) -> None:
        try:
            agent.run(prompt)
        except anthropic.APIError as exc:
            print(f"\n{RED}API error: {exc}{RESET}", file=sys.stderr)
        print()

    if opts.prompt:
        ask(" ".join(opts.prompt))
        return 0

    print(f"{DIM}Replica ({opts.model}, effort {opts.effort}) in {agent.toolbox.root}. Ctrl-D to exit.{RESET}")
    while True:
        try:
            prompt = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt:
            ask(prompt)


if __name__ == "__main__":
    raise SystemExit(main())
