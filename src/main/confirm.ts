// Changes to what the app runs are confirmed in a native dialog shown by the main process (#91). The window could
// otherwise set a command line on its own: a single renderer bug (or a "paste this into the console" trick) would be
// enough to run any program.
import { BrowserWindow, dialog } from "electron";
import { parse } from "smol-toml";

/** Keys of the backend configuration that decide what runs, or with which tools and environment. */
const COMMAND_KEY = /(^|_)(argv|env)$|^(exec_)?tools$/;

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
