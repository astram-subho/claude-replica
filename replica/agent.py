"""The agent loop: stream a turn, run the requested tools, feed results back, repeat."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Callable

import anthropic

from .tools import TOOL_DEFINITIONS, Toolbox, ToolError

MODEL = "claude-opus-5-5"
SYSTEM_PROMPT = files("replica").joinpath("system_prompt.md").read_text()

_JSON_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict}
_SCHEMAS = {t["name"]: t["input_schema"] for t in TOOL_DEFINITIONS}


def validate_input(name: str, args: object) -> str | None:
    """Return an error message if args don't match the tool's schema, else None.

    Eager input streaming skips server-side validation, so a malformed input
    can arrive silently truncated; check it before running anything.
    """
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"unknown tool: {name}"
    if not isinstance(args, dict):
        return "input is not an object"
    for key in schema["required"]:
        if key not in args:
            return f"missing required field: {key}"
    for key, value in args.items():
        prop = schema["properties"].get(key)
        if prop is None:
            return f"unexpected field: {key}"
        expected = _JSON_TYPES[prop["type"]]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            return f"field {key} should be {prop['type']}"
    return None


@dataclass
class Callbacks:
    on_text: Callable[[str], None] = lambda text: None
    on_thinking: Callable[[str], None] = lambda text: None
    on_tool_call: Callable[[str, dict], None] = lambda name, args: None
    on_tool_result: Callable[[str, str, bool], None] = lambda name, result, is_error: None


@dataclass
class Agent:
    toolbox: Toolbox
    model: str = MODEL
    effort: str = "high"
    show_thinking: bool = False
    max_tokens: int = 64_000
    callbacks: Callbacks = field(default_factory=Callbacks)
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)
    # Append-only history: earlier turns (and their thinking blocks) are never edited.
    messages: list = field(default_factory=list)

    def _tools(self) -> list[dict]:
        return [{**t, "eager_input_streaming": True} for t in TOOL_DEFINITIONS]

    def _stream_turn(self):
        thinking = {"type": "adaptive", "display": "summarized" if self.show_thinking else "omitted"}
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=self._tools(),
            messages=self.messages,
            thinking=thinking,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            # On a safety-classifier decline, re-run on Anthropic's recommended model for that category.
            fallbacks="default",
            betas=["server-side-fallback-2026-07-01"],
        ) as stream:
            for event in stream:
                if event.type == "text":
                    self.callbacks.on_text(event.text)
                elif event.type == "thinking" and event.thinking:
                    self.callbacks.on_thinking(event.thinking)
            return stream.get_final_message()

    def run(self, prompt: str) -> str:
        """Run one user request to completion and return the final text."""
        self.messages.append({"role": "user", "content": prompt})
        json_retries = 0
        while True:
            try:
                response = self._stream_turn()
                json_retries = 0
            except ValueError:
                # Tool input JSON the SDK could not parse at all; no tool_use_id
                # to answer, so re-issue the turn. API errors propagate.
                json_retries += 1
                if json_retries > 2:
                    raise
                continue

            self.messages.append({"role": "assistant", "content": response.content})
            text = "".join(b.text for b in response.content if b.type == "text")

            if response.stop_reason == "refusal":
                return text or "[the request was declined]"
            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return text
            if response.stop_reason == "max_tokens":
                # A truncated tool input parses as a valid partial object; don't run it.
                raise RuntimeError("tool input truncated at max_tokens; raise max_tokens and retry")

            results = []
            for block in tool_uses:
                results.append(self._run_tool(block))
            self.messages.append({"role": "user", "content": results})

    def _run_tool(self, block) -> dict:
        error = validate_input(block.name, block.input)
        if error:
            content, is_error = json.dumps({"INVALID_JSON": json.dumps(block.input), "error": error}), True
        else:
            self.callbacks.on_tool_call(block.name, block.input)
            try:
                content, is_error = self.toolbox.run(block.name, block.input), False
            except ToolError as exc:
                content, is_error = f"Error: {exc}", True
            except OSError as exc:
                content, is_error = f"Error: {exc}", True
        self.callbacks.on_tool_result(block.name, content, is_error)
        result = {"type": "tool_result", "tool_use_id": block.id, "content": content or "(no output)"}
        if is_error:
            result["is_error"] = True
        return result
