// What the window remembers about a package between sessions (in the app's data folder, not in the package).
import type { CompareSettings } from "./types";

export interface Scope { sections: string[]; items: string[] }

/** Height of the figures in the comparison table of step 7 (px). */
export const FIGURE_HEIGHT = { min: 160, max: 900, default: 320 };

export interface UiState {
  layout: 2;
  step: number; // 0: inputs, 1: targets, 2: analysis plan, …
  /** Targets and analysis plan: the paper with the items aligned next to it, or the table. */
  curationView: "reader" | "table";
  /** Last scope per "<kind>:<extraction>" chosen in the LLM dialogs. */
  scopes: Record<string, Scope>;
  llmProfile: string;
  agentProfile: string;
  agentModel: string;
  allowExec: boolean;
  agentInstructions: string;
  template: string;
  compare: CompareSettings;
  figureHeight: number;
  collapsed: string[]; // LLM run groups collapsed in the tables ("targets:run1")
}

export function loadUiState(raw: any): UiState {
  const s = raw && typeof raw === "object" ? raw : {};
  return {
    layout: 2,
    // Windows saved before layout 2 had the text (with the PDF) as step 0.
    step: Number.isInteger(s.step) ? Math.max(0, s.layout === 2 ? s.step : s.step - 1) : 0,
    curationView: s.curationView === "table" ? "table" : "reader",
    scopes: s.scopes && typeof s.scopes === "object" ? s.scopes : {},
    llmProfile: String(s.llmProfile ?? ""),
    agentProfile: String(s.agentProfile ?? ""),
    agentModel: String(s.agentModel ?? ""),
    allowExec: s.allowExec !== false,
    agentInstructions: String(s.agentInstructions ?? ""),
    template: String(s.template ?? ""),
    compare: { rounding: s.compare?.rounding === "reported" || Number.isInteger(s.compare?.rounding) ? s.compare.rounding : "reported", threshold: Number(s.compare?.threshold ?? 5) || 5 },
    figureHeight: Math.max(FIGURE_HEIGHT.min, Math.min(FIGURE_HEIGHT.max, Number(s.figureHeight) || FIGURE_HEIGHT.default)),
    collapsed: Array.isArray(s.collapsed) ? s.collapsed.map(String) : [],
  };
}
