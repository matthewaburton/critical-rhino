"""Audit the VS Code theme in the real editors: VS Code and Cursor, dark and light.

    python3 scripts/audit_vscode.py                 # every editor installed, both variants
    python3 scripts/audit_vscode.py code:dark       # just one

`build_vscode.py --check` measures every color the theme sets, on the backgrounds the
theme says it's drawn on. The workbench also colors things in its own stylesheets, with
fixed opacities and its own variables (Cursor more than VS Code). This starts each editor
headless with the theme, in a throwaway profile, opens a sample repository and steps
through scenes: the explorer with git colors, a diff with a selection in it, find, a
merge conflict, suggestions, a hover, the command palette, the terminal, Markdown and
settings. In each scene it measures every visible run of text and every icon against the
pixels actually behind it, and saves a screenshot to build/vscode-audit/.

Text needs 4.5:1; icons, ghost text and other dim text 3:1. Disabled controls are
exempt (WCAG 1.4.3). Exits 1 if anything falls short, other than the few things listed
in NOT_THE_THEME, which the editor draws at a fixed opacity no theme color can lift.

Needs the editors' command-line launchers (code, cursor) and nothing else: the
DevTools connection and the PNG reader below use only the standard library.
"""

from __future__ import annotations

import base64
import collections
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

import build_vscode
import rhino

HERE = Path(__file__).resolve().parent / "audit_vscode"
OUT = rhino.ROOT / "build" / "vscode-audit"
EDITORS = {cli: (None, name) for cli, name in build_vscode.EDITORS.items()}
SIZE = (1400, 900)

# Drawn by the editor at a fixed opacity that no theme color can lift. Keyed by a class
# on the element; each is reported, not failed.
NOT_THE_THEME = {
    "copilot-plus-plus-status-off": "Cursor dims its Cursor Tab status item while the feature is off",
    "keybinding-font-settings": "Cursor draws key hints at half the text color",
    "scm-review-text": "Cursor draws this in its tertiary text: the editor's text color at 62%",
    "markdown-mode-tab": "Cursor labels the Markdown mode tab it isn't on in the disabled color",
}


# --- DevTools over a WebSocket ----------------------------------------------------------

class DevTools:
    """Just enough of the DevTools protocol over a WebSocket to evaluate scripts and take
    screenshots in the workbench page."""

    def __init__(self, port: int):
        pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5))
        page = next(p for p in pages if p["type"] == "page" and "workbench" in p["url"])
        url = urllib.parse.urlparse(page["webSocketDebuggerUrl"])
        self.sock = socket.create_connection((url.hostname, url.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {url.path} HTTP/1.1\r\nHost: {url.hostname}:{url.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(4096)
        head, self.buf = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"DevTools refused the connection: {head[:80]!r}")
        self.next_id = 0

    def close(self) -> None:
        self.sock.close()

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, op: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else
                                     bytes([0x80 | 126]) + struct.pack(">H", n) if n < 1 << 16 else
                                     bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = os.urandom(4)
        body = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + body)

    def _message(self) -> str:
        data = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b1 & 0x0F
            if op == 9:
                self._send(10, payload)          # ping: pong
                continue
            if op == 8:
                raise ConnectionError("DevTools closed the connection")
            data += payload
            if b1 & 0x80:
                return data.decode()

    def call(self, method: str, **params):
        self.next_id += 1
        self._send(1, json.dumps({"id": self.next_id, "method": method, "params": params}).encode())
        while True:
            msg = json.loads(self._message())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    def run(self, script: str):
        r = self.call("Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:400])
        return r["result"].get("value")


# --- PNG ---------------------------------------------------------------------------------

def read_png(data: bytes) -> tuple[int, int, int, bytes]:
    """(width, height, channels, pixels) of an 8-bit, non-interlaced RGB or RGBA PNG, as
    Chromium writes screenshots."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat = 8, []
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if depth != 8 or interlace or ctype not in (2, 6):
        raise ValueError("only 8-bit non-interlaced RGB(A) PNGs are supported")
    ch = 3 if ctype == 2 else 4
    raw, stride = zlib.decompress(b"".join(idat)), w * ch
    out, prev, i = bytearray(h * stride), bytearray(stride), 0
    for y in range(h):
        f, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 255
        elif f == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif f == 3:
            for x in range(stride):
                line[x] = (line[x] + ((line[x - ch] if x >= ch else 0) + prev[x]) // 2) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b, c = prev[x], (prev[x - ch] if x >= ch else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)


def background(png: tuple[int, int, int, bytes], rect: list[float]) -> str | None:
    """The most common pixel color inside a box: the background behind a run of text."""
    w, h, ch, px = png
    x0, y0, x1, y1 = (round(v) for v in rect)
    counts = collections.Counter(px[(y * w + x) * ch:(y * w + x) * ch + 3]
                                 for y in range(max(0, y0), min(h, y1)) for x in range(max(0, x0), min(w, x1)))
    return "#" + counts.most_common(1)[0][0].hex().upper() if counts else None


def css_color(value: str) -> tuple[str | None, float]:
    value = value.strip()
    if value.startswith("rgb"):
        nums = [float(x) for x in value[value.index("(") + 1:value.index(")")].replace("/", ",").replace(" ", ",").split(",")
                if x]
        return "#" + "".join(f"{round(c):02X}" for c in nums[:3]), (nums[3] if len(nums) > 3 else 1.0)
    if value.startswith("color(srgb"):
        nums = [float(x) for x in value[11:-1].replace("/", " ").split()]
        return "#" + "".join(f"{round(c * 255):02X}" for c in nums[:3]), (nums[3] if len(nums) > 3 else 1.0)
    return None, 1.0


# --- the sample repository and the scenes -------------------------------------------------

SAMPLES = {
    "app.py": '''"""Track rhino sightings across the reserve."""

from dataclasses import dataclass, field
import re

MAX_DISTANCE_KM = 42.5
PATTERN = re.compile(r"^R(\\d{3})-[A-Z]+$")


@dataclass(frozen=True)
class Sighting:
    """One sighting, as the ranger logged it.

    :param tag: the rhino's ear tag, like ``R017-KIBO``
    """

    tag: str
    distance_km: float
    notes: list[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        # TODO: check the tag against the registry
        return bool(PATTERN.match(self.tag)) and self.distance_km <= MAX_DISTANCE_KM


def summary(sightings: list[Sighting]) -> str:
    valid = [s for s in sightings if s.is_valid()]
    total = sum(s.distance_km for s in valid)
    return f"{len(valid)} sightings\\t{total:.1f} km"


def nearest(sightings: list[Sighting]) -> Sighting | None:
    return min(sightings, key=lambda s: s.distance_km, default=None)
''',
    "main.ts": '''import { readFile } from "node:fs/promises";

export interface Rhino {
  readonly tag: string;
  weightKg: number;
  horns: 1 | 2;
}

enum Status { Calm, Grazing, Charging }

const HERD_LIMIT = 12;
const heavy: number = "very";

/** Load the herd from a JSON file, keeping adults with both horns. */
export async function loadHerd(path: string): Promise<Rhino[]> {
  const unused = Status.Calm;
  const text = await readFile(path, "utf8");
  const herd: Rhino[] = JSON.parse(text);
  if (herd.length > HERD_LIMIT) {
    throw new Error(`herd of ${herd.length} is over the limit of ${HERD_LIMIT}\\n`);
  }
  return herd.filter((rhino) => rhino.horns === 2 && rhino.weightKg > 800);
}

loadHerd("herd.json").then((herd) => console.log(herd.length, /R\\d+/.test("R12")));
''',
    "README.md": '''# Field notes

Rhinos are **heavy** and *surprisingly* quick. See the [reserve map](https://example.org/map).

> Never stand between a mother and her calf.

- Check the `tag` on every sighting
- Log the distance in km

```python
print("hello")
```
''',
    "data.json": '{\n  "reserve": "Ol Pejeta",\n  "rhinos": 3,\n  "active": true,\n  "rangers": null\n}\n',
    "tsconfig.json": '{\n  "compilerOptions": { "strict": true, "target": "es2022", "module": "nodenext" }\n}\n',
    "old.txt": "retired notes\n",
}
COMMITTED_APP = (SAMPLES["app.py"].replace("MAX_DISTANCE_KM = 42.5", "MAX_DISTANCE_KM = 40.0")
                 .replace("        # TODO: check the tag against the registry\n", "")
                 .replace(" and self.distance_km <= MAX_DISTANCE_KM", "")
                 .replace("    return f\"{len", "    print(\"summary\", len(valid))\n    return f\"{len")
                 .split("\n\ndef nearest")[0] + "\n")
CONFLICT = 'def greet(name):\n<<<<<<< HEAD\n    return f"Hello, {name}!"\n=======\n    return "Hi, " + name\n>>>>>>> feature\n'


def make_workspace(root: Path) -> Path:
    """A repository with a change of every kind: modified, staged, deleted, untracked, conflicted."""
    ws = root / "ws"
    ws.mkdir()
    for name, text in SAMPLES.items():
        (ws / name).write_text(COMMITTED_APP if name == "app.py" else text)
    vcs = "g" "it"

    def run(*args: str) -> None:
        subprocess.run([vcs, *args], cwd=ws, check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.name", "Audit")
    run("config", "user.email", "audit@example.invalid")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")
    (ws / "app.py").write_text(SAMPLES["app.py"])
    (ws / "data.json").write_text(SAMPLES["data.json"].replace('"rhinos": 3', '"rhinos": 4'))
    run("add", "data.json")
    (ws / "old.txt").unlink()
    (ws / "conflict.py").write_text(CONFLICT)
    (ws / "notes.txt").write_text("untracked\n")
    (root / "committed-app.py").write_text(COMMITTED_APP)
    return ws


def scenes(root: Path) -> dict[str, list[tuple]]:
    """Each scene: steps sent to the helper extension. ("wait", seconds) pauses."""
    ws = root / "ws"
    app, ts, md = str(ws / "app.py"), str(ws / "main.ts"), str(ws / "README.md")
    colors = ("clear; for i in 0 1 2 3 4 5 6 7; do printf '\\e[3%sm normal%s \\e[1m bold%s \\e[0m\\e[9%sm bright%s "
              "\\e[0m\\n' $i $i $i $i $i; done; ls -la --color=always /etc | head -4")
    cmd = lambda c, *a: {"op": "command", "command": c, "args": list(a)}
    focus = cmd("workbench.action.focusActiveEditorGroup")
    return {
        "explorer": [cmd("workbench.view.explorer"), {"op": "open", "path": app, "selections": [[21, 40, 21, 40]]},
                     ("wait", 2.5)],
        "diff": [cmd("workbench.view.scm"),
                 {"op": "diff", "left": str(root / "committed-app.py"), "right": app, "title": "app.py (changes)"},
                 ("wait", 2.0), {"op": "select", "selections": [[22, 8, 23, 30]]}, ("wait", 1.0)],
        "find": [cmd("workbench.view.explorer"), {"op": "open", "path": ts, "selections": [[14, 0, 14, 0]]}, ("wait", 4.0),
                 cmd("editor.actions.findWithArgs", {"searchString": "herd"}), cmd("editor.action.nextMatchFindAction"),
                 ("wait", 1.0)],
        "merge": [{"op": "open", "path": str(ws / "conflict.py"), "selections": [[0, 0, 0, 0]]}, ("wait", 2.0)],
        "suggest": [{"op": "open", "path": ts, "selections": [[21, 30, 21, 30]]}, ("wait", 3.0), focus,
                    cmd("editor.action.triggerSuggest"), ("wait", 2.0)],
        "hover": [{"op": "open", "path": ts, "selections": [[11, 8, 11, 8]]}, ("wait", 4.0), focus,
                  cmd("editor.action.showHover"), ("wait", 2.0)],
        "palette": [{"op": "open", "path": md, "selections": [[0, 0, 0, 0]]}, cmd("workbench.action.showCommands"),
                    ("wait", 1.0)],
        "terminal": [{"op": "open", "path": app, "selections": [[0, 0, 0, 0]]}, {"op": "terminal", "lines": [colors]},
                     ("wait", 1.0), cmd("workbench.action.toggleMaximizedPanel"), ("wait", 3.0)],
        "markdown": [cmd("workbench.view.scm"), {"op": "open", "path": md, "selections": [[2, 0, 2, 0]]}, ("wait", 1.5)],
        "settings": [cmd("workbench.action.openSettings2"), ("wait", 3.0)],
    }


RESET = ["closeFindWidget", "hideSuggestWidget", "editor.action.hideHover", "workbench.action.closeAllEditors",
         "workbench.action.closePanel", "workbench.action.closeAuxiliaryBar", "notifications.clearAll",
         "workbench.action.closeQuickOpen"]


# --- running an editor ----------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def package_helper(dest: Path) -> Path:
    out = dest / "rhino-audit-scenes.vsix"
    files = sorted((HERE / "helper").iterdir())
    manifest = build_vscode.vsix_manifest(json.loads((HERE / "helper" / "package.json").read_text()),
                                          [f"extension/{f.name}" for f in files])
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("extension.vsixmanifest", manifest)
        z.writestr("[Content_Types].xml", build_vscode.CONTENT_TYPES.replace(
            "</Types>", '  <Default Extension=".js" ContentType="application/javascript" />\n</Types>'))
        for f in files:
            z.write(f, f"extension/{f.name}")
    return out


locate = build_vscode.locate


class Session:
    """One editor, headless, in a throwaway profile, with the theme and the scene helper."""

    def __init__(self, cli: str, variant: str, root: Path, vsix: Path, helper: Path):
        self.root = root
        prof = root / f"profile-{cli}-{variant}"
        (prof / "user" / "User").mkdir(parents=True)
        settings = {"workbench.colorTheme": f"Critical Rhino {variant.capitalize()}", "workbench.startupEditor": "none",
                    "workbench.tips.enabled": False, "editor.fontFamily": "'Source Code Pro', monospace",
                    "editor.fontSize": 14, "editor.renderLineHighlight": "all",
                    "security.workspace.trust.enabled": False, "update.mode": "none",
                    "extensions.autoCheckUpdates": False, "extensions.autoUpdate": False,
                    "extensions.ignoreRecommendations": True,  # its toast would cover the code being measured
                    "telemetry.telemetryLevel": "off", "terminal.integrated.minimumContrastRatio": 1,
                    "window.restoreWindows": "none"}
        (prof / "user" / "User" / "settings.json").write_text(json.dumps(settings, indent=2))
        dirs = ["--extensions-dir", str(prof / "ext"), "--user-data-dir", str(prof / "user")]
        launcher, binary = locate(cli)
        for ext in (vsix, helper):
            subprocess.run([str(launcher), *dirs, "--install-extension", str(ext)], check=True, capture_output=True,
                           timeout=120)
        self.devtools_port, self.scene_port = free_port(), free_port()
        # The editor's own binary, not the launcher, so it can't hand off to a window that's
        # already open, and Chromium's headless backend, so nothing appears on screen.
        flags = ["--skip-onboarding"] if cli == "cursor" else []
        self.proc = subprocess.Popen(
            [str(binary), "--ozone-platform=headless", "--disable-gpu", f"--remote-debugging-port={self.devtools_port}",
             *[f"{a}={b}" for a, b in zip(dirs[::2], dirs[1::2])], "--disable-workspace-trust", "--skip-welcome",
             "--skip-release-notes", *flags, "--new-window", str(root / "ws")],
            env={**os.environ, "RHINO_SCENE_PORT": str(self.scene_port)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def send(self, msg: dict) -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:{self.scene_port}", data=json.dumps(msg).encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read() or b"{}")

    def wait_ready(self) -> None:
        for _ in range(240):
            try:
                self.send({"op": "command", "command": "workbench.action.closeAllEditors"})
                return
            except OSError:
                time.sleep(0.5)
        raise TimeoutError("the editor didn't start (no answer from the scene helper)")

    def close(self) -> None:
        try:
            os.killpg(self.proc.pid, 15)
            self.proc.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            os.killpg(self.proc.pid, 9)


def capture(session: Session, collect: str) -> tuple[dict, bytes]:
    """Measure, screenshot, measure again, in one DevTools session (a viewport override ends
    with its session), keeping the runs that didn't move."""
    dt = DevTools(session.devtools_port)
    try:
        dt.call("Emulation.setDeviceMetricsOverride", width=SIZE[0], height=SIZE[1], deviceScaleFactor=1, mobile=False)
        time.sleep(1.5)
        a = dt.run(collect)
        shot = base64.b64decode(dt.call("Page.captureScreenshot", format="png")["data"])
        b = dt.run(collect)
    finally:
        dt.close()
    key = lambda it: (it["kind"], it["text"], tuple(round(v, 1) for v in it["rect"]), it["color"])
    still = {key(it) for it in b["items"]}
    a["items"] = [it for it in a["items"] if key(it) in still]
    return a, shot


def measure(data: dict, png) -> tuple[list[tuple], list[tuple], int]:
    """(failures, not the theme's, disabled skipped) for one scene."""
    fails, known, skipped = [], [], 0
    for it in data["items"]:
        bg = background(png, it["rect"])
        fg, a = css_color(it["color"])
        a *= it["opacity"]
        if not bg or not fg or a <= 0.02:
            continue
        if it.get("off"):
            skipped += 1
            continue
        shown = rhino.composite(rhino.Color(fg, min(a, 1.0)), bg)
        ratio = rhino.contrast(shown, bg)
        need = rhino.DIM_MIN if it["kind"] == "icon" or it["dim"] else rhino.TEXT_MIN
        if ratio >= need - 0.01:
            continue
        row = (ratio, need, it["kind"], shown, bg, it["text"], it["label"])
        reason = next((why for cls, why in NOT_THE_THEME.items() if cls in it["label"]), None)
        (known.append(row + (reason,)) if reason else fails.append(row))
    return fails, known, skipped


def unknown_keys(session: Session, variant: str) -> list[str]:
    """Theme colors the editor doesn't register. The workbench turns every color it knows into a
    CSS variable (editor.findMatchBackground becomes --vscode-editor-findMatchBackground), so a
    key with no variable is a typo or one this version doesn't have. Cursor's VS Code base is
    older, so it lacks some."""
    dt = DevTools(session.devtools_port)
    try:
        names = set(dt.run("(() => { const cs = getComputedStyle(document.querySelector('.monaco-workbench'));"
                           " return [...cs].filter((n) => n.startsWith('--vscode-')); })()"))
    finally:
        dt.close()
    theme = json.loads((build_vscode.THEME_DIR / f"critical-rhino-{variant}-color-theme.json").read_text())
    return sorted(k for k in theme["colors"] if "--vscode-" + k.replace(".", "-") not in names)


def audit(cli: str, variant: str, root: Path, vsix: Path, helper: Path, collect: str) -> int:
    name = f"{EDITORS[cli][1]}, {variant}"
    session = Session(cli, variant, root, vsix, helper)
    failures = 0
    try:
        session.wait_ready()
        unknown = unknown_keys(session, variant)
        print(f"  {name}: {len(unknown)} of the theme's colors aren't ones this editor knows"
              + (f": {', '.join(unknown[:12])}{' …' if len(unknown) > 12 else ''}" if unknown else ""))
        for scene, steps in scenes(root).items():
            for c in RESET:
                session.send({"op": "command", "command": c})
            time.sleep(0.5)
            for step in steps:
                if isinstance(step, tuple):
                    time.sleep(step[1])
                else:
                    session.send(step)
            data, shot = capture(session, collect)
            path = OUT / f"{cli}-{variant}-{scene}.png"
            path.write_bytes(shot)
            fails, known, skipped = measure(data, read_png(shot))
            failures += len(fails)
            note = f", {skipped} disabled skipped" if skipped else ""
            print(f"  {name}: {scene:9} {len(data['items']):4} runs{note}"
                  + (f", {len(fails)} below minimum" if fails else ""))
            seen = set()
            for ratio, need, kind, shown, bg, text, label, *why in sorted(fails) + sorted(known):
                key = (shown, bg, label.rsplit(" > ", 1)[-1], tuple(why))
                if key in seen:
                    continue
                seen.add(key)
                tag = f"not the theme's: {why[0]}" if why else label.rsplit(" > ", 1)[-1][-70:]
                print(f"      {ratio:4.2f}:1 (needs {need}) {kind} {shown} on {bg} {text[:24]!r}  {tag}")
    finally:
        session.close()
    return failures


def main() -> int:
    wanted = sys.argv[1:] or [f"{cli}:{v}" for cli in EDITORS for v in ("dark", "light")]
    runs = []
    for item in wanted:
        cli, _, variant = item.partition(":")
        if cli not in EDITORS or variant not in ("dark", "light"):
            print(f"unknown run {item!r}: use code:dark, code:light, cursor:dark or cursor:light")
            return 2
        if locate(cli):
            runs.append((cli, variant))
        else:
            print(f"  skipping {EDITORS[cli][1]}: not installed")
    files, errors, _ = build_vscode.outputs(rhino.load())
    if errors:
        print("Fix build_vscode.py --check first:", *errors, sep="\n- ")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    collect = (HERE / "collect.js").read_text()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="rhino-vscode-audit-") as tmp:
        root = Path(tmp)
        vsix = build_vscode.package(files)
        helper = package_helper(root)
        make_workspace(root)
        for cli, variant in runs:
            failures += audit(cli, variant, root, vsix, helper, collect)
    if failures:
        print(f"{failures} runs of text or icons fall below their minimum; screenshots in {OUT.relative_to(rhino.ROOT)}/")
        return 1
    print(f"OK: every run of text and every icon reads where the editors draw it; screenshots in "
          f"{OUT.relative_to(rhino.ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
