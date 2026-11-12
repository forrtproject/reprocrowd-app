// Syntax highlighting for the raw view: XML (TEI, JATS) and JSON (Docling, the common document schema). The source is
// tokenized with regular expressions and every token is escaped, so markup in the file can't reach the page.
import { esc } from "./util";

const span = (cls: string, s: string) => `<span class="${cls}">${esc(s)}</span>`;

function attrs(s: string): string {
  let out = "";
  let last = 0;
  for (const m of s.matchAll(/([\w:.-]+)(\s*=\s*)("[^"]*"|'[^']*')/g)) {
    out += esc(s.slice(last, m.index)) + span("hx-attr", m[1]) + esc(m[2]) + span("hx-val", m[3]);
    last = m.index! + m[0].length;
  }
  return out + esc(s.slice(last));
}

export function highlightXml(src: string): string {
  const re = /(<!--[\s\S]*?-->)|(<!\[CDATA\[[\s\S]*?\]\]>)|(<\?[\s\S]*?\?>)|(<!DOCTYPE[\s\S]*?>)|(<\/?)([\w:.-]+)([^<>]*?)(\/?>)/gi;
  let out = "";
  let last = 0;
  for (const m of src.matchAll(re)) {
    out += span("hx-text", src.slice(last, m.index));
    if (m[1]) out += span("hx-com", m[1]);
    else if (m[2]) out += span("hx-cdata", m[2]);
    else if (m[3] || m[4]) out += span("hx-pi", m[3] || m[4]);
    else out += `<span class="hx-tag">${esc(m[5])}<span class="hx-name">${esc(m[6])}</span>${attrs(m[7])}${esc(m[8])}</span>`;
    last = m.index! + m[0].length;
  }
  return out + span("hx-text", src.slice(last));
}

export function highlightJson(src: string): string {
  let text = src;
  try {
    text = JSON.stringify(JSON.parse(src), null, 2);
  } catch { /* show as it is */ }
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let out = "";
  let last = 0;
  for (const m of text.matchAll(re)) {
    out += esc(text.slice(last, m.index));
    if (m[1]) out += m[2] ? span("hx-key", m[1]) + esc(m[2]) : span("hx-str", m[1]);
    else if (m[3]) out += span("hx-lit", m[3]);
    else out += span("hx-num", m[4]);
    last = m.index! + m[0].length;
  }
  return out + esc(text.slice(last));
}

/** Highlighted HTML for a file, chosen by its content; very large files are shown without highlighting. */
export function highlight(src: string, hint = ""): string {
  if (src.length > 4_000_000) return esc(src);
  const head = src.trimStart().slice(0, 1);
  if (hint === "json" || (hint !== "xml" && (head === "{" || head === "["))) return highlightJson(src);
  if (hint === "xml" || head === "<") return highlightXml(src);
  return esc(src);
}
