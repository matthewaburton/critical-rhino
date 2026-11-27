"""Replay a made-up conversation in Claude Code, in a private tmux, and measure what it draws.

    python3 scripts/audit_claude_code.py           # both themes
    python3 scripts/audit_claude_code.py --shots   # also save each view to build/claude-code-audit/ (.ans, .html, .png)

Claude Code runs from a throwaway home and config folder that hold only this
checkout's themes, and the tmux server starts with a bare environment: it never sees
your settings, plugins, sign-in or API key. Nothing goes to a model. Claude Code
resumes a conversation written here, as it would one of yours: a file read, an edit
and its diff, a test run, an answer with a code block and a list, and a bash command.
The project is a git repository with the edit uncommitted, so /diff has a file to show.

Each view is captured with its colors (capture-pane -e) and every character measured
as Ghostty draws it, as make terminal:audit does: text at 4.5:1, faint text and rules
at 3:1, brights asked for by number at their own minimum. The views, in each theme:
the conversation in every permission mode (Shift+Tab cycles them), the slash-command
menu, /theme with its preview, the /diff panel with its file list open (the +N -M
counts and the diff), and the conversation with highlighting off. What Claude Code
draws that no setting reaches (build_claude_code.own_style) is counted, not failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import audit_terminal
import build_claude_code
import build_ghostty
import rhino

OUT = rhino.ROOT / "build" / "claude-code-audit"
SOCKET = "rhino-claude-audit"
SIZE = (110, 44)
SESSION = "5e55a0de-0000-4000-8000-000000000001"
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")   # hyperlinks, on file names
SGR = re.compile(r"\x1b\[[0-9;:]*m")
MENU_ROW = re.compile(r"^ +❯? +/[a-z-]+ {2,}\S", re.MULTILINE)   # a command and its description
COUNTS = re.compile(r"herd\.py +\+1 -1")                           # the /diff panel's file row
# Footer labels, in the order Shift+Tab reaches them from the default mode.
MODES = {"manual mode on": "manual", "accept edits on": "accept-edits", "plan mode on": "plan",
         "auto mode on": "auto"}
PICTURE = "accept-edits"   # the view make screenshots copies into docs/images

HERD = '''"""Herd counts from the reserve's field log."""

LIMIT = 1


def count(herd: list[str]) -> int:
    # Rhinos seen today, capped at LIMIT
    return min(len(herd), LIMIT)
'''
ANSWER = """Done: `LIMIT` is now **2**, and the tests pass. To weigh each rhino only once, cache the lookup:

```python
@cache
def weigh(name: str) -> float:
    \"\"\"A rhino's last weight, in tonnes.\"\"\"
    # from the field log
    return 2.3 if name == "Kifaru" else 0.0
```

## Next

- Count calves separately
- Group sightings by reserve"""


# --- the made-up world -------------------------------------------------------------

def conversation(project: Path, version: str) -> list[dict]:
    """The transcript Claude Code resumes: one turn with tools, then a bash command."""
    entries: list[dict] = []
    herd = project / "herd.py"

    def add(kind: str, message: dict, **extra) -> str:
        n = len(entries) + 1
        entry = {"parentUuid": entries[-1]["uuid"] if entries else None, "isSidechain": False, "type": kind,
                 "message": message, "uuid": str(uuid.UUID(int=n)), "timestamp": f"2026-11-25T10:00:{n:02d}.000Z",
                 "userType": "external", "entrypoint": "cli", "cwd": str(project), "sessionId": SESSION,
                 "version": version, "gitBranch": "main", **extra}
        entries.append(entry)
        return entry["uuid"]

    def assistant(*content: dict) -> str:
        stop = "tool_use" if content[-1]["type"] == "tool_use" else "end_turn"
        return add("assistant", {"model": "claude-sonnet-5-5", "id": f"msg_{len(entries):02d}", "type": "message",
                                 "role": "assistant", "content": list(content), "stop_reason": stop,
                                 "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})

    def tool(name: str, args: dict, result: str, structured: dict) -> None:
        tool_id = f"toolu_{len(entries):02d}"
        asked = assistant({"type": "tool_use", "id": tool_id, "name": name, "input": args})
        add("user", {"role": "user", "content": [{"tool_use_id": tool_id, "type": "tool_result", "content": result}]},
            toolUseResult=structured, sourceToolAssistantUUID=asked)

    edited = HERD.replace("LIMIT = 1", "LIMIT = 2")
    add("user", {"role": "user", "content": "Raise the herd limit to 2, then run the tests."})
    tool("Read", {"file_path": str(herd)}, "".join(f"{i}\t{line}\n" for i, line in enumerate(HERD.splitlines(), 1)),
         {"type": "text", "file": {"filePath": str(herd), "content": HERD, "numLines": 8, "startLine": 1,
                                   "totalLines": 8}})
    patch = [" " + line for line in HERD.splitlines()[:2]] + ["-LIMIT = 1", "+LIMIT = 2"] + \
            [" " + line for line in HERD.splitlines()[3:6]]
    tool("Edit", {"replace_all": False, "file_path": str(herd), "old_string": "LIMIT = 1\n", "new_string": "LIMIT = 2\n"},
         f"The file {herd} has been updated successfully.",
         {"filePath": str(herd), "oldString": "LIMIT = 1\n", "newString": "LIMIT = 2\n", "originalFile": HERD,
          "structuredPatch": [{"oldStart": 1, "oldLines": 6, "newStart": 1, "newLines": 6, "lines": patch}],
          "userModified": False, "replaceAll": False})
    tool("Bash", {"command": "python3 -m pytest -q", "description": "Run the tests"}, "3 passed in 0.02s",
         {"stdout": "3 passed in 0.02s", "stderr": "", "interrupted": False, "isImage": False,
          "noOutputExpected": False})
    assistant({"type": "text", "text": ANSWER})
    add("user", {"role": "user", "content": "<bash-input>git diff --stat</bash-input>"})
    add("user", {"role": "user", "content": "<bash-stdout> herd.py | 2 +-\n 1 file changed, 1 insertion(+), "
                                            "1 deletion(-)\n</bash-stdout><bash-stderr></bash-stderr>"})
    herd.write_text(edited)
    return entries


def make_world(work: Path, variant: str, version: str) -> tuple[dict[str, str], Path]:
    """A throwaway home with Claude Code set up to resume the conversation in one theme.
    Returns the environment to run it in, and the project folder."""
    home = work / "home"
    config = home / ".claude"
    project = home / "projects" / "rhino-app"
    project.mkdir(parents=True)
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("[user]\n\tname = Ada Lovelace\n\temail = ada@example.com\n"
                         "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
    (config / "themes").mkdir(parents=True)
    for theme in sorted(build_claude_code.THEME_DIR.glob("critical-rhino-*.json")):
        shutil.copy(theme, config / "themes")
    (config / "settings.json").write_text(json.dumps({"theme": f"custom:critical-rhino-{variant}"}))
    # Onboarding, the release notes and the question whether to trust the folder, answered.
    (config / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "lastOnboardingVersion": version, "lastReleaseNotesSeen": version,
        "numStartups": 20, "autoUpdates": False, "installMethod": "native",
        "projects": {str(project): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True,
                                    "projectOnboardingSeenCount": 4}}}))
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "LANG": "C.UTF-8", "COLORTERM": "truecolor",
           "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
           "CLAUDE_CONFIG_DIR": str(config), "CLAUDE_CODE_TMUX_TRUECOLOR": "1", "DISABLE_AUTOUPDATER": "1",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1"}
    # The file as it was before the edit, committed; the conversation leaves the edit uncommitted.
    (project / "herd.py").write_text(HERD)
    for args in (("init", "-q"), ("add", "herd.py"), ("commit", "-q", "-m", "Count the herd")):
        audit_terminal.git(project, env, *args)
    transcripts = config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project))
    transcripts.mkdir(parents=True)
    lines = "".join(json.dumps(e) + "\n" for e in conversation(project, version))
    (transcripts / f"{SESSION}.jsonl").write_text(lines)
    return env, project


# --- driving Claude Code -------------------------------------------------------------

def plain(capture: str) -> str:
    return SGR.sub("", OSC.sub("", capture))


def wait_for(tmux: audit_terminal.Tmux, ready: Callable[[str], bool], what: str, timeout: float = 30.0) -> str:
    """Capture until the screen is ready, and let it settle."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if ready(plain(tmux.capture())):
            time.sleep(0.6)
            return tmux.capture()
        time.sleep(0.25)
    raise RuntimeError(f"Claude Code didn't show {what} within {timeout:g}s:\n{plain(tmux.capture())}")


def click(tmux: audit_terminal.Tmux, label: str) -> None:
    """Click the middle of a label on screen, as an SGR mouse press and release."""
    screen = plain(tmux.capture()).splitlines()
    row = next(i for i, line in enumerate(screen) if label in line)
    col = screen[row].index(label) + len(label) // 2
    at = f"{col + 1};{row + 1}"
    tmux.keys("-l", f"\x1b[<0;{at}M\x1b[<0;{at}m", wait=0.3)


def mode(screen: str) -> str | None:
    return next((name for label, name in MODES.items() if label in screen), None)


def resumed(screen: str) -> bool:
    """The whole conversation is showing, in the default mode."""
    return "manual mode on" in screen and "Group sightings" in screen


def play(tmux: audit_terminal.Tmux) -> list[tuple[str, str]]:
    """Every view: the conversation in each permission mode, the slash menu, /theme, /diff."""
    views = [("manual", wait_for(tmux, resumed, "the resumed conversation"))]
    for _ in MODES:
        before = views[-1][0]
        tmux.keys("BTab", wait=0.1)
        capture = wait_for(tmux, lambda s, b=before: mode(s) not in (None, b), "the next permission mode")
        name = mode(plain(capture))
        if name in dict(views):   # round to the start
            break
        views.append((name, capture))
    while mode(plain(tmux.capture())) != "manual":   # back to the default mode for the menus
        tmux.keys("BTab", wait=0.5)
    tmux.keys("-l", "/", wait=0.1)
    views.append(("slash-menu", wait_for(tmux, lambda s: len(MENU_ROW.findall(s)) >= 3, "the slash-command menu")))
    tmux.keys("Escape", wait=0.3)
    tmux.keys("C-u", wait=0.3)
    tmux.keys("-l", "/theme", wait=0.3)
    tmux.keys("Enter", wait=0.1)
    views.append(("theme-picker", wait_for(tmux, lambda s: "Critical Rhino Light" in s and "Syntax theme" in s,
                                           "the theme picker")))
    tmux.keys("Escape", wait=0.3)
    # /diff opens a panel beside the conversation; its file list opens with a click.
    tmux.keys("-l", "/diff", wait=0.3)
    tmux.keys("Enter", wait=0.1)
    wait_for(tmux, lambda s: "(show)" in s, "the /diff panel")
    click(tmux, "(show)")
    views.append(("diff-panel", wait_for(tmux, lambda s: bool(COUNTS.search(s)), "the /diff panel's file list")))
    return views


# --- main ----------------------------------------------------------------------------

def noting(own: list[str]) -> Callable[[str, audit_terminal.Style], bool]:
    """Skip what Claude Code draws that no setting reaches, noting each run in `own`."""
    def ignore(text: str, style: audit_terminal.Style) -> bool:
        if build_claude_code.own_style(text, style):
            own.append(text)
            return True
        return False
    return ignore


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure what Claude Code draws with the themes, in a private tmux.")
    parser.add_argument("--shots", action="store_true", help="save each view to build/claude-code-audit/")
    parser.add_argument("--verbose", action="store_true", help="list every failing run, not just the first few")
    args = parser.parse_args()
    for tool, why in (("tmux", "the audit runs Claude Code in a private tmux server"), ("claude", "it audits Claude Code"),
                      ("git", "the made-up project is a git repository, for /diff")):
        if not shutil.which(tool):
            print(f"{tool} isn't installed; {why}.")
            return 1
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=True).stdout.split()[0]
    print(f"Claude Code {version}")
    data = rhino.load()
    terms = {n: build_ghostty.Terminal(rhino.load_variant(data, n)) for n in rhino.appearances(data)}
    if args.shots:
        OUT.mkdir(parents=True, exist_ok=True)
    failures, total = [], 0
    with tempfile.TemporaryDirectory(prefix="rhino-claude-audit-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        for name, term in terms.items():
            env, project = make_world(work / name, name, version)
            tmux = audit_terminal.Tmux(work, *SIZE, socket=SOCKET, env=env, options="set -g focus-events on\n")
            command = f"cd {shlex.quote(str(project))} && exec claude --resume {SESSION} --permission-mode default"
            try:
                tmux.start(command, {})
                views = play(tmux)
                # Again with highlighting off, as Ctrl+T in /theme leaves it: code in the text color.
                settings = Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"
                settings.write_text(json.dumps(json.loads(settings.read_text()) | {"syntaxHighlightingDisabled": True}))
                tmux.start(command, {})
                views.append(("highlighting-off", wait_for(tmux, resumed, "the resumed conversation")))
                for view, capture in views:
                    capture = OSC.sub("", capture)
                    rows = audit_terminal.parse(capture)
                    own: list[str] = []
                    n, found = audit_terminal.measure(view, rows, term, noting(own))
                    total += n
                    failures += found
                    if args.shots:
                        stem = f"{view}-{name}"
                        (OUT / f"{stem}.ans").write_text(capture, encoding="utf-8")
                        (OUT / f"{stem}.html").write_text(audit_terminal.to_html(rows, term), encoding="utf-8")
                        audit_terminal.screenshot(OUT / f"{stem}.html", OUT / f"{stem}.png", SIZE[0], len(rows))
                    note = f"; {len(own)} Claude Code's own, not measured: {', '.join(map(repr, own))}" if own else ""
                    print(f"   {view} [{name}]: {n} runs, {len(found)} below their minimum{note}")
            finally:
                tmux.stop()
    return audit_terminal.summarize(failures, total, "theme", args.verbose)


if __name__ == "__main__":
    sys.exit(main())
