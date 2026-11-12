// What the window remembers about a package between sessions (in the app's data folder, not in the package).
import type { TextMode } from "./reader";
import type { CompareSettings } from "./types";

export interface Scope { sections: string[]; items: string[] }

/** Height of the figures in the comparison table of step 7 (px). */
export const FIGURE_HEIGHT = { min: 160, max: 900, default: 320 };

export interface UiState {
  textMode: TextMode;
  extraction: string;
  rawSource: string;
  pdf: string; // "ft:<resource>"
  step: number; // 0 is the text, 1… the reproduction
  /** Extractions chosen in step 2 as the text sources targets and actions are extracted from. */
  sources: string[];
  /** Last scope per "<kind>:<extraction>" chosen in the LLM dialogs. */
  scopes: Record<string, Scope>;
  llmProfile: string;
  agentProfile: string;
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
    textMode: ["tree", "reader", "raw"].includes(s.textMode) ? s.textMode : "reader",
    extraction: String(s.extraction ?? ""),
    rawSource: String(s.rawSource ?? ""),
    pdf: String(s.pdf ?? ""),
    // Older tabs kept the reader as a separate view and counted the reproduction steps from 0.
    step: "view" in s ? (s.view === "repro" && Number.isInteger(s.step) ? s.step + 1 : 0) : Number.isInteger(s.step) ? s.step : 0,
    sources: Array.isArray(s.sources) ? s.sources.map(String) : [],
    scopes: s.scopes && typeof s.scopes === "object" ? s.scopes : {},
    llmProfile: String(s.llmProfile ?? ""),
    agentProfile: String(s.agentProfile ?? ""),
    allowExec: s.allowExec !== false,
    agentInstructions: String(s.agentInstructions ?? ""),
    template: String(s.template ?? ""),
    compare: { rounding: s.compare?.rounding === "reported" || Number.isInteger(s.compare?.rounding) ? s.compare.rounding : "reported", threshold: Number(s.compare?.threshold ?? 5) || 5 },
    figureHeight: Math.max(FIGURE_HEIGHT.min, Math.min(FIGURE_HEIGHT.max, Number(s.figureHeight) || FIGURE_HEIGHT.default)),
    collapsed: Array.isArray(s.collapsed) ? s.collapsed.map(String) : [],
  };
}
