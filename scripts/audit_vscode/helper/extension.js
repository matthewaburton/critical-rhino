// Drives the editor for screenshots and the contrast audit: runs commands it's sent over a
// local port. Only loaded into throwaway profiles by the audit.
const vscode = require("vscode");
const http = require("http");

function pos(a) { return new vscode.Position(a[0], a[1]); }
function sel(s) { return new vscode.Selection(pos([s[0], s[1]]), pos([s[2], s[3]])); }
function uri(p) { return vscode.Uri.file(p); }

async function handle(msg) {
  switch (msg.op) {
    case "command":
      return await vscode.commands.executeCommand(msg.command, ...(msg.args || []));
    case "open": {
      const doc = await vscode.workspace.openTextDocument(uri(msg.path));
      const ed = await vscode.window.showTextDocument(doc, { preview: false, viewColumn: msg.column || vscode.ViewColumn.Active });
      if (msg.selections) ed.selections = msg.selections.map(sel);
      const top = pos(msg.reveal || [0, 0]);
      ed.revealRange(new vscode.Range(top, top), vscode.TextEditorRevealType.AtTop);
      return doc.languageId;
    }
    case "diff":
      await vscode.commands.executeCommand("vscode.diff", uri(msg.left), uri(msg.right), msg.title || "diff");
      return "ok";
    case "select": {
      const ed = vscode.window.activeTextEditor;
      if (!ed) return "no editor";
      ed.selections = msg.selections.map(sel);
      return "ok";
    }
    case "terminal": {
      const term = vscode.window.createTerminal({ name: msg.name || "colors", shellPath: "/bin/bash", shellArgs: ["--norc", "--noprofile"] });
      term.show(false);
      for (const line of msg.lines || []) term.sendText(line);
      return "ok";
    }
    case "config":
      await vscode.workspace.getConfiguration().update(msg.key, msg.value, vscode.ConfigurationTarget.Global);
      return "ok";
    default:
      throw new Error("unknown op " + msg.op);
  }
}

function activate() {
  const port = Number(process.env.RHINO_SCENE_PORT || 0);
  if (!port) return;
  http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => (body += d));
    req.on("end", async () => {
      try {
        const out = await handle(JSON.parse(body || "{}"));
        res.end(JSON.stringify({ ok: true, out: out === undefined ? null : String(out) }));
      } catch (e) {
        res.statusCode = 500;
        res.end(JSON.stringify({ ok: false, error: String((e && e.stack) || e) }));
      }
    });
  }).listen(port, "127.0.0.1");
}

module.exports = { activate };
