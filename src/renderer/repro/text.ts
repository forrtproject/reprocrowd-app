// Step 1: the paper's text as ReproCrowd extracted it (JATS XML from Europe PMC, parsed into the common document
// schema), rendered in full right after extraction, so you can check that it is the right paper and that nothing is
// missing before claims are extracted from it. With "Show sentence ids" each sentence shows the id the LLM cites.
import { countSentences } from "../doc";
import { paperHtml } from "../paper";
import type { Document } from "../types";
import { esc } from "../util";
import type { Step, Wizard } from "./wizard";

/** OpenAlex work types that are notices about another article rather than research to reproduce. */
const NOTICE_TYPES: Record<string, string> = { erratum: "a correction notice", retraction: "a retraction notice", editorial: "an editorial" };
/** Below this many characters of running text, a research article has almost certainly not been extracted in full. */
const SHORT_TEXT = 3000;

export class TextStep implements Step {
  title = "Paper";
  private doc?: Document;
  private error?: string;
  private showIds = false;

  constructor(private w: Wizard) {}

  async load() {
    try {
      this.doc = await this.w.app.loadDoc(this.w.app.jats());
      this.error = undefined;
    } catch (e) {
      this.doc = undefined;
      this.error = (e as Error).message;
    }
  }

  /** Problems a reader should see before extracting claims: a notice instead of an article, or too little text. */
  private warnings(d: Document | undefined, meta: any): string[] {
    const out: string[] = [];
    const kind = NOTICE_TYPES[meta?.type ?? ""];
    if (kind) out.push(`OpenAlex lists this DOI as ${kind}, not a research article: it has no results to reproduce. Start a new reproduction with the DOI of the article it refers to.`);
    if (d) {
      const chars = d.sections.reduce((n, s) => n + s.paragraphs.reduce((m, p) => m + p.sentences.reduce((k, x) => k + x.text.length, 0), 0), 0);
      if (chars < SHORT_TEXT) out.push(`Only ${chars.toLocaleString()} characters of text were extracted. A research article is usually much longer, so claims extraction will find little or nothing.`);
    }
    return out;
  }

  draw(body: HTMLElement) {
    const app = this.w.app;
    const meta = app.meta();
    const ids = app.summary().identifiers ?? {};
    const d = this.doc;
    const counts = d ? `${d.sections.length} sections · ${countSentences(d)} sentences · ${d.figures.length} figures · ${d.tables.length} tables` : "";
    const source = [ids.doi && `DOI ${ids.doi}`, ids.pmcid && `Europe PMC ${ids.pmcid}`, meta?.type && `OpenAlex type: ${meta.type}`].filter(Boolean).map((x) => esc(x)).join(" · ");
    const warnings = this.warnings(d, meta);
    body.innerHTML = `
    <div class="card">
      <div class="card-head"><h3>The paper's text, as extracted</h3>
        <span class="${this.error ? "warn-inline" : "ok"} small">${this.error ? "✗ couldn't be loaded" : "✓ JATS XML, parsed"}</span>
        <label class="small" style="margin-left:auto"><input type="checkbox" id="tx-ids" ${this.showIds ? "checked" : ""} /> Show sentence ids</label></div>
      <p class="small">${source}</p>
      ${counts ? `<p class="small">${counts}</p>` : ""}
      ${this.error ? `<p class="warn">${esc(this.error)}</p>` : ""}
      ${warnings.map((x) => `<p class="warn">${esc(x)}</p>`).join("")}
      <p class="hint">This is exactly the text the LLM reads when it extracts claims (step 3), and every quote and value points into it. Check it is the right paper and that the abstract, results and tables are all here.</p>
    </div>
    <div class="card tx-paper ${this.showIds ? "tx-ids" : ""}">${paperHtml(d, meta)}</div>`;
    body.querySelector<HTMLInputElement>("#tx-ids")!.addEventListener("change", (e) => {
      this.showIds = (e.target as HTMLInputElement).checked;
      body.querySelector(".tx-paper")!.classList.toggle("tx-ids", this.showIds);
    });
  }
}
