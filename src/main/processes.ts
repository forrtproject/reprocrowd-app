// Local programs: the search path (desktop launchers start apps with a minimal PATH), and starting the code editor.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function searchPath(): string {
  const home = os.homedir();
  const sep = path.delimiter;
  const dirs = (process.env.PATH ?? "").split(sep).filter(Boolean);
  // Desktop launchers start apps with a minimal PATH, so add the places the templates' commands (bash, R, Python,
  // Snakemake, git) usually live: Git for Windows and Rtools (both per-user and per-machine installs).
  const extra = process.platform === "win32"
    ? [
        path.join(home, "AppData", "Local", "Programs", "Git", "bin"),
        path.join(home, "AppData", "Local", "Programs", "Git", "cmd"),
        "C:\\Program Files\\Git\\bin", "C:\\Program Files\\Git\\cmd",
        "C:\\rtools43\\usr\\bin", "C:\\RBuildTools\\4.4\\usr\\bin",
      ]
    : [`${home}/.local/bin`, `${home}/bin`, `${home}/.cargo/bin`, "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", "/snap/bin"];
  for (const d of extra) if (d && !dirs.includes(d)) dirs.push(d);
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

const shQuote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

/** Run a command in a new, visible terminal window (for interactive CLI steps such as a login). The window stays
 * open when the command ends, so its last message can be read. False if no terminal could be found. */
export function openInTerminal(argv: string[], cwd: string): boolean {
  const env = { ...process.env, PATH: searchPath() };
  const start = (exe: string, args: string[]) => {
    const child = spawn(exe, args, { cwd, detached: true, stdio: "ignore", env });
    child.on("error", () => {});
    child.unref();
    return true;
  };
  if (process.platform === "win32") {
    return start("cmd.exe", ["/c", "start", "", "cmd", "/k", argv.map((a) => `"${a}"`).join(" ")]);
  }
  const line = argv.map(shQuote).join(" ");
  if (process.platform === "darwin") {
    const script = `${line}; echo; read -n 1 -s -r -p "Press any key to close this window."`;
    return start("osascript", ["-e", `tell application "Terminal" to do script ${JSON.stringify(script)}`, "-e", 'tell application "Terminal" to activate']);
  }
  const script = `${line}; echo; printf "Press Enter to close this window."; read _`;
  const sh = ["sh", "-c", script];
  // The user's choice ($TERMINAL) first, then the common terminals with the way each takes a command.
  const candidates: [string, string[]][] = [
    ...(process.env.TERMINAL ? [[process.env.TERMINAL, ["-e", ...sh]] as [string, string[]]] : []),
    ["gnome-terminal", ["--", ...sh]],
    ["konsole", ["-e", ...sh]],
    ["xfce4-terminal", ["-x", ...sh]],
    ["ptyxis", ["--", ...sh]],
    ["kgx", ["--", ...sh]],
    ["kitty", sh],
    ["alacritty", ["-e", ...sh]],
    ["wezterm", ["start", "--", ...sh]],
    ["foot", sh],
    ["x-terminal-emulator", ["-e", ...sh]],
    ["xterm", ["-e", ...sh]],
  ];
  for (const [name, args] of candidates) {
    const exe = which(name);
    if (exe) return start(exe, args);
  }
  return false;
}
