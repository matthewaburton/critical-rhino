// Every visible run of text and every icon in the workbench, with its computed color, the
// product of its ancestors' opacity, its box, and whether it's dim (3:1) or a disabled control.
// audit_vscode.py runs it in the editor and measures each run against the screenshot.
(() => {
  const vw = innerWidth, vh = innerHeight, out = [];
  const opacity = (el) => { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
  const label = (el) => {
    const parts = [];
    for (let e = el, i = 0; e && e.nodeType === 1 && i < 5; e = e.parentElement, i++) {
      let s = e.tagName.toLowerCase();
      if (e.id) s += "#" + e.id;
      const cls = [...e.classList].filter((c) => !/^mtk\d+$/.test(c)).slice(0, 3);
      if (cls.length) s += "." + cls.join(".");
      parts.unshift(s);
    }
    return parts.join(" > ");
  };
  const dimmed = (el) => !!el.closest(".ghost-text, .ghost-text-decoration, .ghost-text-decoration-preview, .suggest-preview-text, .squiggly-inline-unnecessary, .mtkw, .inline-completion-text-to-replace, .dimmed-line-number");
  // Placeholders count as dim when the editor draws them in the ghost-text color (Cursor does).
  const probe = document.createElement("span");
  probe.style.color = "var(--vscode-editorGhostText-foreground)";
  document.body.appendChild(probe);
  const ghost = getComputedStyle(probe).color;
  probe.remove();
  const clipped = (el, x, y) => {
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.overflow === "visible" && cs.overflowX === "visible" && cs.overflowY === "visible") continue;
      const r = e.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) return true;
    }
    return false;
  };
  const onTop = (el, x, y) => {
    if (clipped(el, x, y)) return false;               // scrolled out of a list or a hover
    const top = document.elementFromPoint(x, y);
    if (!top) return false;
    if (top === el || el.contains(top) || top.contains(el)) return true;
    const ed = el.closest(".monaco-editor");          // editor text sits under its overlay layers
    return !!ed && ed.contains(top) && !top.closest(".overlayWidgets, .contentWidgets, .monaco-hover, .suggest-widget");
  };
  const box = (r) => [Math.max(0, r.left), Math.max(0, r.top), Math.min(vw, r.right), Math.min(vh, r.bottom)];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    const text = n.textContent.replace(/\s+/g, " ").trim();
    const el = n.parentElement;
    if (!text || !el || el.closest("script, style, textarea, .monaco-aria-container, .xterm-accessibility")) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility !== "visible" || parseFloat(cs.fontSize) < 6) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) {
      const [x0, y0, x1, y1] = box(r);
      if (x1 - x0 < 3 || y1 - y0 < 6) continue;
      if (!onTop(el, (x0 + x1) / 2, (y0 + y1) / 2)) continue;
      out.push({ kind: "text", text: text.slice(0, 48), rect: [x0, y0, x1, y1], color: cs.color, opacity: opacity(el),
                 size: parseFloat(cs.fontSize), weight: cs.fontWeight, dim: dimmed(el) || (!!el.closest(".editorPlaceholder") && cs.color === ghost),
                 off: !!el.closest(".disabled, [aria-disabled='true']"), label: label(el) });
    }
  }
  for (const el of document.querySelectorAll(".codicon")) {
    const r = el.getBoundingClientRect();
    const [x0, y0, x1, y1] = box(r);
    if (x1 - x0 < 6 || y1 - y0 < 6) continue;
    const cs = getComputedStyle(el), before = getComputedStyle(el, "::before");
    if (cs.visibility !== "visible" || before.content === "none" || before.content === "normal") continue;
    if (!onTop(el, (x0 + x1) / 2, (y0 + y1) / 2)) continue;
    out.push({ kind: "icon", text: [...el.classList].find((c) => c.startsWith("codicon-")) || "codicon", rect: [x0, y0, x1, y1],
               color: before.color || cs.color, opacity: opacity(el), size: parseFloat(cs.fontSize), weight: "400",
               dim: dimmed(el) || (!!el.closest(".editorPlaceholder") && cs.color === ghost),
                 off: !!el.closest(".disabled, [aria-disabled='true']"), label: label(el) });
  }
  return { w: vw, h: vh, dpr: devicePixelRatio, theme: document.body.className.match(/vs-dark|vs|hc-black|hc-light/)?.[0], items: out };
})()
