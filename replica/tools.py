"""Client-side tools: shell, file read/write/edit, glob and grep, confined to a project root."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_OUTPUT_CHARS = 30_000


class ToolError(Exception):
    """Raised by a tool to return an is_error result to the model."""


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n... [{len(text) - MAX_OUTPUT_CHARS} chars truncated] ...\n{text[-half:]}"


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOL_DEFINITIONS = [
    {
        "name": "bash",
        "description": "Run a shell command in the project root and return stdout, stderr and the exit code.",
        "input_schema": _schema(
            {
                "command": {"type": "string", "description": "The command to run."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 120, max 600)."},
            },
            ["command"],
        ),
    },
    {
        "name": "read_file",
        "description": "Read a text file. Returns lines prefixed with 1-based line numbers.",
        "input_schema": _schema(
            {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "offset": {"type": "integer", "description": "First line to read (1-based)."},
                "limit": {"type": "integer", "description": "Maximum number of lines to read (default 2000)."},
            },
            ["path"],
        ),
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given full contents.",
        "input_schema": _schema(
            {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "contents": {"type": "string", "description": "Full file contents."},
            },
            ["path", "contents"],
        ),
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in a file. old_string must occur exactly once unless replace_all is true.",
        "input_schema": _schema(
            {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            ["path", "old_string", "new_string"],
        ),
    },
    {
        "name": "glob",
        "description": "List files matching a glob pattern such as '**/*.py', relative to the project root.",
        "input_schema": _schema({"pattern": {"type": "string"}}, ["pattern"]),
    },
    {
        "name": "grep",
        "description": "Search file contents with a regular expression. Returns path:line:text matches.",
        "input_schema": _schema(
            {
                "pattern": {"type": "string", "description": "Python regular expression."},
                "glob": {"type": "string", "description": "Restrict to files matching this glob (default '**/*')."},
            },
            ["pattern"],
        ),
    },
]

# Tools that change state; these go through the approval callback.
MUTATING_TOOLS = {"bash", "write_file", "edit_file"}


@dataclass
class Toolbox:
    root: Path
    approve: Callable[[str, dict], bool] = lambda name, args: True
    _handlers: dict = field(init=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self._handlers = {
            "bash": self.bash,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "glob": self.glob,
            "grep": self.grep,
        }

    def run(self, name: str, args: dict) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        if name in MUTATING_TOOLS and not self.approve(name, args):
            raise ToolError("the user declined this tool call")
        try:
            return _truncate(handler(**args))
        except TypeError as exc:  # wrong or missing arguments
            raise ToolError(f"invalid arguments: {exc}") from exc

    def _resolve(self, path: str) -> Path:
        # Model-supplied paths are untrusted: reject anything escaping the root.
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise ToolError(f"path escapes the project root: {path}")
        return target

    def bash(self, command: str, timeout: int = 120) -> str:
        timeout = max(1, min(int(timeout), 600))
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.root, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"command timed out after {timeout}s")
        parts = []
        if proc.stdout:
            parts.append(proc.stdout.rstrip())
        if proc.stderr:
            parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
        parts.append(f"[exit code {proc.returncode}]")
        return "\n".join(parts)

    def read_file(self, path: str, offset: int = 1, limit: int = 2000) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        lines = target.read_text(errors="replace").splitlines()
        start = max(offset, 1)
        chunk = lines[start - 1 : start - 1 + limit]
        if not chunk:
            return "(empty)" if not lines else f"(no lines at offset {start}; file has {len(lines)})"
        return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(chunk, start))

    def write_file(self, path: str, contents: str) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
        return f"wrote {len(contents)} chars to {path}"

    def edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        text = target.read_text()
        count = text.count(old_string)
        if count == 0:
            raise ToolError("old_string not found")
        if count > 1 and not replace_all:
            raise ToolError(f"old_string occurs {count} times; add context or set replace_all")
        target.write_text(text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1))
        return f"replaced {count if replace_all else 1} occurrence(s) in {path}"

    def _files(self, pattern: str) -> list[Path]:
        return sorted(
            p for p in self.root.glob(pattern)
            if p.is_file() and ".git" not in p.relative_to(self.root).parts
        )

    def glob(self, pattern: str) -> str:
        matches = [str(p.relative_to(self.root)) for p in self._files(pattern)]
        return "\n".join(matches) if matches else "(no matches)"

    def grep(self, pattern: str, glob: str = "**/*") -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}")
        hits = []
        for path in self._files(glob):
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(self.root)
            hits.extend(f"{rel}:{n}:{line}" for n, line in enumerate(text.splitlines(), 1) if regex.search(line))
            if len(hits) >= 500:
                break
        return "\n".join(hits[:500]) if hits else "(no matches)"
