// Changes to what the app runs are confirmed in a native dialog shown by the main process (#91). The window could
// otherwise set a command line on its own: a single renderer bug (or a "paste this into the console" trick) would be
// enough to run any program.
import { BrowserWindow, dialog } from "electron";
import fs from "node:fs";
import path from "node:path";
import { parse } from "smol-toml";

/** Keys of the backend configuration that decide what runs, or with which tools and environment. */
const COMMAND_KEY = /(^|_)(argv|env)$|^(exec_)?tools$|^env_pass$/;

/** Every command-bearing value of a TOML text, by its dotted key (unparseable text has none). */
export function commandEntries(text: string): Map<string, string> {
  const out = new Map<string, string>();
  let data: unknown;
  try {
    data = parse(text);
  } catch {
    return out;
  }
  const walk = (v: unknown, at: string[]) => {
    if (!v || typeof v !== "object" || Array.isArray(v)) return;
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
      if (COMMAND_KEY.test(k)) out.set([...at, k].join("."), JSON.stringify(x));
      else walk(x, [...at, k]);
    }
  };
  walk(data, []);
  return out;
}

/** The command-bearing values that are new or different in `next`. Removed ones fall back to the built-in defaults. */
export function changedCommands(previous: string, next: string): string[] {
  const before = commandEntries(previous);
  return [...commandEntries(next)].filter(([k, v]) => before.get(k) !== v).map(([k, v]) => `${k} = ${v}`);
}

export class NotConfirmed extends Error {
  constructor() {
    super("Not saved: the new command was not confirmed.");
    this.name = "NotConfirmed";
  }
}

/** Ask before saving new commands; throws NotConfirmed when the user declines. No changes, no dialog. */
export async function confirmCommands(win: BrowserWindow | null, changes: string[]): Promise<void> {
  if (!changes.length) return;
  const opts = {
    type: "warning" as const,
    title: "Confirm new commands",
    message: changes.length === 1 ? "ReproCrowd will run this command from now on:" : "ReproCrowd will run these commands from now on:",
    detail: `${changes.join("\n\n")}\n\nOnly confirm a change you made yourself in Settings.`,
    buttons: ["Cancel", "Use the new command"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  };
  const { response } = await (win ? dialog.showMessageBox(win, opts) : dialog.showMessageBox(opts));
  if (response !== 1) throw new NotConfirmed();
}

// Project settings of coding tools that a package folder from someone else may carry (#91). The agents ReproCrowd
// starts are told to skip the first group (backend: config.isolate_argv); OpenCode documents no switch for its
// project config, and Codex's project config depends on its trust list.
const SKIPPED = [".claude", ".mcp.json", "CLAUDE.md", "AGENTS.md"];
const READ = ["opencode.json", "opencode.jsonc", ".opencode", ".codex"];

/** Before adopting a folder: name the tool settings it carries and ask. True when there are none or the user goes on. */
export async function confirmToolSettings(win: BrowserWindow | null, dir: string): Promise<boolean> {
  const present = (names: string[]) => names.filter((n) => fs.existsSync(path.join(dir, n)));
  const skipped = present(SKIPPED);
  const read = present(READ);
  if (!skipped.length && !read.length) return true;
  const lines = [
    ...(read.length ? [`OpenCode or Codex may still use: ${read.join(", ")}. Don't use those agents for this package unless you have checked these files, or remove them first.`] : []),
    ...(skipped.length ? [`The coding agents that ReproCrowd starts ignore: ${skipped.join(", ")}.`] : []),
    "These files can make a coding tool run commands or follow instructions. If you open this folder in a coding tool yourself, they take effect there.",
  ];
  const opts = {
    type: "warning" as const,
    title: "Settings for coding tools in this folder",
    message: "This folder holds settings for AI coding tools that came with the package.",
    detail: lines.join("\n\n"),
    buttons: ["Cancel", "Open the folder"],
    defaultId: 1,
    cancelId: 0,
    noLink: true,
  };
  const { response } = await (win ? dialog.showMessageBox(win, opts) : dialog.showMessageBox(opts));
  return response === 1;
}
