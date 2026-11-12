// Local programs: the search path (desktop launchers start apps with a minimal PATH), and starting the code editor.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function searchPath(): string {
  const home = os.homedir();
  const sep = path.delimiter;
  const dirs = (process.env.PATH ?? "").split(sep).filter(Boolean);
  const extra = process.platform === "win32" ? [] : [`${home}/.local/bin`, `${home}/bin`, `${home}/.cargo/bin`, "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", "/snap/bin"];
  for (const d of extra) if (!dirs.includes(d)) dirs.push(d);
  return dirs.join(sep);
}

export function which(cmd: string): string | undefined {
  if (cmd.includes("/") || cmd.includes("\\")) return fs.existsSync(cmd) ? cmd : undefined;
  const exts = process.platform === "win32" ? (process.env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";") : [""];
  for (const dir of searchPath().split(path.delimiter)) {
    for (const ext of exts) {
      const p = path.join(dir, cmd + ext);
      try {
        fs.accessSync(p, fs.constants.X_OK);
        if (fs.statSync(p).isFile()) return p;
      } catch {
        /* next */
      }
    }
  }
  return undefined;
}

/** Split a command line into words: whitespace separates, '…' and "…" group, \ escapes the next character. */
export function splitCommand(s: string): string[] {
  const words: string[] = [];
  let word = "";
  let inWord = false;
  let quote = "";
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quote) {
      if (c === quote) quote = "";
      else if (c === "\\" && quote === '"' && i + 1 < s.length) word += s[++i];
      else word += c;
    } else if (c === "'" || c === '"') {
      quote = c;
      inWord = true;
    } else if (c === "\\" && i + 1 < s.length) {
      word += s[++i];
      inWord = true;
    } else if (/\s/.test(c)) {
      if (inWord) words.push(word);
      word = "";
      inWord = false;
    } else {
      word += c;
      inWord = true;
    }
  }
  if (quote) throw new Error(`The command has an unclosed ${quote} quote.`);
  if (inWord) words.push(word);
  return words;
}

/** Start a GUI program (e.g. a code editor) without waiting for it. */
export function spawnDetached(argv: string[], cwd: string) {
  const exe = which(argv[0]);
  if (!exe) throw new Error(`“${argv[0]}” isn't installed, or not on the PATH.`);
  const child = spawn(exe, argv.slice(1), { cwd, detached: true, stdio: "ignore", env: { ...process.env, PATH: searchPath() } });
  child.on("error", () => {});
  child.unref();
}
