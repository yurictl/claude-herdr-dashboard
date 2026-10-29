#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=0.80"]
# ///
"""Companion dashboard for a Claude Code agent running in a herdr pane.

Shows: agent status (from herdr), context usage, tool-call log and a file tree
of the agent's cwd with files touched in this session highlighted.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DirectoryTree, Footer, ProgressBar, RichLog, Static

PROJECTS = Path.home() / ".claude" / "projects"
HIDDEN = {".git", "node_modules", "__pycache__", ".venv", ".DS_Store", ".mypy_cache", ".pytest_cache"}
STATUS_STYLE = {
    "working": ("◐ working", "bold yellow"),
    "blocked": ("⚠ blocked — waiting for input", "bold red"),
    "idle": ("● idle", "bold green"),
    "done": ("✔ done", "bold green"),
    "unknown": ("? unknown", "dim"),
}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DEFAULT_WINDOW = 1_000_000
GONE_POLLS = 5  # exit after this many polls without an agent in the pane


def herdr_agent(pane: str) -> dict | None:
    try:
        out = subprocess.run(
            ["herdr", "agent", "get", pane], capture_output=True, text=True, timeout=3
        ).stdout
        return json.loads(out)["result"]["agent"]
    except Exception:
        return None


def find_transcript(session_id: str) -> Path | None:
    hits = list(PROJECTS.glob(f"*/{session_id}.jsonl"))
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def local_time(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def tool_summary(name: str, inp: dict) -> str:
    for key in ("file_path", "command", "pattern", "description", "url", "query", "prompt", "skill"):
        if key in inp and inp[key]:
            val = str(inp[key]).replace("\n", " ⏎ ")
            return val
    return ""


class TouchedTree(DirectoryTree):
    """DirectoryTree that hides noise and marks files the agent edited."""

    touched: set[str] = set()

    def filter_paths(self, paths):
        return [p for p in paths if p.name not in HIDDEN]

    def render_label(self, node, base_style, style):
        label = super().render_label(node, base_style, style)
        path = node.data.path if node.data else None
        if path is not None:
            sp = str(path)
            if sp in self.touched:
                label.stylize("bold #ff9e3b")
                label.append(" ✎", "bold #ff9e3b")
            elif node.allow_expand and any(t.startswith(sp + os.sep) for t in self.touched):
                label.stylize("#ff9e3b")
        return label


class Dashboard(App):
    CSS = """
    #header { height: 3; padding: 0 1; border-bottom: solid $panel; }
    #ctxrow { height: 1; padding: 0 1; }
    #ctxbar { width: 1fr; }
    #ctxlabel { width: auto; padding-left: 1; }
    #tree { width: 40%; border-right: solid $panel; }
    #right { width: 60%; }
    #tools { height: 1fr; }
    #touched { height: auto; max-height: 10; border-top: solid $panel; padding: 0 1; }
    """
    BINDINGS = [
        ("r", "reload_tree", "Reload tree"),
        ("c", "clear_log", "Clear log"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, pane: str, window: int | None):
        super().__init__()
        self.pane = pane
        self.window_override = window
        self.session_id: str | None = None
        self.transcript: Path | None = None
        self.offset = 0
        self.pending: dict[str, tuple[str, str]] = {}  # tool_use_id -> (name, summary)
        self.touched: dict[str, int] = {}
        self.tool_count = 0
        self.ctx_tokens = 0
        self.model = "?"
        self.title_text = ""
        self.cwd = Path.cwd()
        self.status = "unknown"
        self.missing = 0

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="ctxrow"):
            yield ProgressBar(id="ctxbar", total=100, show_eta=False, show_percentage=False)
            yield Static(id="ctxlabel")
        with Horizontal():
            yield TouchedTree(self.cwd, id="tree")
            with Vertical(id="right"):
                yield RichLog(id="tools", markup=False, wrap=False, max_lines=2000)
                yield Static(id="touched")
        yield Footer()

    def on_mount(self) -> None:
        self.poll()
        self.set_interval(1.0, self.poll)

    # --- polling -------------------------------------------------------
    def poll(self) -> None:
        agent = herdr_agent(self.pane)
        if agent:
            self.missing = 0
            self.status = agent.get("agent_status", "unknown")
            sess = (agent.get("agent_session") or {}).get("value")
            cwd = agent.get("cwd")
            if cwd and Path(cwd) != self.cwd and Path(cwd).is_dir():
                self.cwd = Path(cwd)
                self.query_one(TouchedTree).path = self.cwd
            if sess and sess != self.session_id:
                self.switch_session(sess)
            self.title_text = agent.get("terminal_title_stripped") or self.title_text
        else:
            self.status = "unknown"
            self.missing += 1
            if self.missing >= GONE_POLLS:
                self.exit()
                return
        if self.transcript is None and self.session_id:
            self.transcript = find_transcript(self.session_id)
        self.read_transcript()
        self.render_header()

    def switch_session(self, sess: str) -> None:
        self.session_id = sess
        self.transcript = find_transcript(sess)
        self.offset = 0
        self.pending.clear()
        self.touched.clear()
        self.tool_count = 0
        self.ctx_tokens = 0
        self.query_one("#tools", RichLog).clear()

    def read_transcript(self) -> None:
        if not self.transcript or not self.transcript.exists():
            return
        size = self.transcript.stat().st_size
        if size < self.offset:  # rewritten/compacted
            self.offset = 0
        if size == self.offset:
            return
        with self.transcript.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        # keep a partial trailing line for the next poll
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            return
        self.offset += last_nl + 1
        tree_dirty = False
        for raw in chunk[: last_nl + 1].splitlines():
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            tree_dirty |= self.handle(rec)
        self.render_touched()
        if tree_dirty:
            self.action_reload_tree()

    def handle(self, rec: dict) -> bool:
        kind = rec.get("type")
        log = self.query_one("#tools", RichLog)
        if kind == "ai-title":
            self.title_text = rec.get("aiTitle") or self.title_text
            return False
        if rec.get("isSidechain"):
            return False
        msg = rec.get("message") or {}
        content = msg.get("content")
        if kind == "assistant":
            usage = msg.get("usage") or {}
            ctx = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if ctx:
                self.ctx_tokens = ctx
            self.model = rec.get("requestedModel") or msg.get("model") or self.model
            dirty = False
            for block in content if isinstance(content, list) else []:
                if block.get("type") != "tool_use":
                    continue
                name, inp = block.get("name", "?"), block.get("input") or {}
                summ = tool_summary(name, inp)
                self.pending[block.get("id")] = (name, summ)
                self.tool_count += 1
                ts = local_time(rec.get("timestamp"))
                line = Text()
                line.append(f"{ts} ", "dim")
                line.append("▸ ", "cyan")
                line.append(f"{name:<10}", "bold cyan")
                line.append(" " + summ[:200])
                log.write(line)
                if name in EDIT_TOOLS and inp.get("file_path"):
                    p = str(Path(inp["file_path"]).resolve())
                    self.touched[p] = self.touched.get(p, 0) + 1
                    dirty = True
            return dirty
        if kind == "user" and isinstance(content, list):
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                name, summ = self.pending.pop(block.get("tool_use_id"), ("?", ""))
                if block.get("is_error"):
                    err = str(block.get("content"))[:160].replace("\n", " ")
                    line = Text("         ✗ ", "bold red")
                    line.append(f"{name}: {err}", "red")
                    log.write(line)
        elif kind == "user" and isinstance(content, str) and not rec.get("isMeta"):
            line = Text("── prompt: ", "bold magenta")
            line.append(content.replace("\n", " ")[:200], "magenta")
            log.write(line)
        return False

    # --- rendering -----------------------------------------------------
    def context_window(self) -> int:
        if self.window_override:
            return self.window_override
        return DEFAULT_WINDOW

    def render_header(self) -> None:
        label, style = STATUS_STYLE.get(self.status, STATUS_STYLE["unknown"])
        t = Text()
        t.append(label, style)
        t.append(f"   {self.model}", "dim")
        t.append(f"   tools: {self.tool_count}", "dim")
        if self.pending:
            t.append(f"  (in progress: {len(self.pending)})", "yellow")
        t.append("\n")
        t.append(self.title_text or "—", "bold")
        t.append(f"   {self.cwd}", "dim")
        self.query_one("#header", Static).update(t)

        win = self.context_window()
        pct = min(100, self.ctx_tokens * 100 / win) if win else 0
        self.query_one("#ctxbar", ProgressBar).update(progress=pct)
        color = "green" if pct < 50 else "yellow" if pct < 80 else "red"
        self.query_one("#ctxlabel", Static).update(
            Text(f"ctx {self.ctx_tokens / 1000:.1f}k / {win // 1000}k ({pct:.0f}%)", color)
        )

    def render_touched(self) -> None:
        self.query_one(TouchedTree).touched = set(self.touched)
        t = Text("Edited files:", "bold")
        if not self.touched:
            t.append(" —", "dim")
        for p, n in sorted(self.touched.items(), key=lambda kv: -kv[1]):
            rel = os.path.relpath(p, self.cwd) if p.startswith(str(self.cwd)) else p
            t.append(f"\n  ✎ {rel}", "#ff9e3b")
            t.append(f"  ×{n}", "dim")
        self.query_one("#touched", Static).update(t)

    # --- actions -------------------------------------------------------
    def action_reload_tree(self) -> None:
        tree = self.query_one(TouchedTree)
        tree.touched = set(self.touched)
        tree.reload()

    def action_clear_log(self) -> None:
        self.query_one("#tools", RichLog).clear()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pane", default=os.environ.get("DASH_TARGET_PANE"), help="herdr pane id of the agent")
    ap.add_argument("--window", type=int, help="context window size in tokens (default 1M)")
    args = ap.parse_args()
    if not args.pane:
        ap.error("--pane (or DASH_TARGET_PANE) is required")
    Dashboard(args.pane, args.window).run()


if __name__ == "__main__":
    main()
