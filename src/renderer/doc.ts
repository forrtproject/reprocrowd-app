// Helpers over the backend's common document schema (sections > paragraphs > sentences, figures, tables).
import type { Box, Document, Figure, Paragraph, Section, Sentence, Table } from "./types";

export type Float = (Figure & { kind: "figure" }) | (Table & { kind: "table" });

/** A section with the sections nested below it (by level) and the figures/tables that appear in it. */
export interface SectionNode {
  section: Section;
  children: SectionNode[];
  floats: Float[];
}

export interface DocIndex {
  sentences: Map<string, { sentence: Sentence; section?: Section; paragraph?: Paragraph; float?: Float }>;
  floats: Map<string, Float>;
  sections: Map<string, Section>;
  paragraphs: Map<string, { paragraph: Paragraph; section: Section }>;
}

const cache = new WeakMap<Document, DocIndex>();

export function floatsOf(doc: Document): Float[] {
  return [...doc.figures.map((f) => ({ ...f, kind: "figure" as const })), ...doc.tables.map((t) => ({ ...t, kind: "table" as const }))];
}

export function indexDoc(doc: Document): DocIndex {
  const hit = cache.get(doc);
  if (hit) return hit;
  const idx: DocIndex = { sentences: new Map(), floats: new Map(), sections: new Map(), paragraphs: new Map() };
  for (const section of doc.sections) {
    idx.sections.set(section.id, section);
    for (const paragraph of section.paragraphs) {
      idx.paragraphs.set(paragraph.id, { paragraph, section });
      for (const sentence of paragraph.sentences) idx.sentences.set(sentence.id, { sentence, section, paragraph });
    }
  }
  for (const f of floatsOf(doc)) {
    idx.floats.set(f.id, f);
    for (const sentence of f.caption) idx.sentences.set(sentence.id, { sentence, float: f, section: f.section ? idx.sections.get(f.section) : undefined });
  }
  cache.set(doc, idx);
  return idx;
}

/** Sections nested by level; floats attached to their section. Floats without one are returned separately. */
export function sectionTree(doc: Document): { roots: SectionNode[]; loose: Float[] } {
  const roots: SectionNode[] = [];
  const stack: SectionNode[] = [];
  const byId = new Map<string, SectionNode>();
  for (const section of doc.sections) {
    const node: SectionNode = { section, children: [], floats: [] };
    byId.set(section.id, node);
    while (stack.length && stack[stack.length - 1].section.level >= section.level) stack.pop();
    (stack.length ? stack[stack.length - 1].children : roots).push(node);
    stack.push(node);
  }
  const loose: Float[] = [];
  for (const f of floatsOf(doc)) (f.section && byId.get(f.section) ? byId.get(f.section)!.floats : loose).push(f);
  return { roots, loose };
}

/** Figures and tables in a section, including those in its subsections at any depth. */
export const countFloats = (n: SectionNode): number => n.children.reduce((sum, c) => sum + countFloats(c), n.floats.length);

export const countSentences = (doc: Document) =>
  doc.sections.reduce((n, s) => n + s.paragraphs.reduce((k, p) => k + p.sentences.length, 0), 0) + floatsOf(doc).reduce((n, f) => n + f.caption.length, 0);

export const hasCoords = (doc: Document) =>
  doc.sections.some((s) => s.paragraphs.some((p) => p.boxes?.length || p.sentences.some((x) => x.boxes?.length))) || floatsOf(doc).some((f) => f.boxes?.length);

/** Text of referenced sentences / figure and table captions, in the given order. */
export function refText(doc: Document | undefined, ids: string[]): string {
  if (!doc) return "";
  const idx = indexDoc(doc);
  const parts: string[] = [];
  for (const id of ids) {
    const s = idx.sentences.get(id);
    const f = idx.floats.get(id);
    if (s) parts.push(s.sentence.text);
    else if (f) parts.push(`${f.label}: ${f.caption.map((c) => c.text).join(" ")}`);
  }
  return parts.join(" ");
}

/** What hovering an element highlights in the PDF: exact boxes, or coarser ones (paragraph/float) as a fallback. */
export interface HighlightTarget { text: string; boxes?: Box[]; fallback?: Box[] }

export function highlightTargets(doc: Document): Map<string, HighlightTarget> {
  const out = new Map<string, HighlightTarget>();
  const nonEmpty = (b?: Box[] | null) => (b?.length ? b : undefined);
  for (const sec of doc.sections) {
    out.set(sec.id, { text: sec.title });
    for (const p of sec.paragraphs) {
      const all = p.sentences.flatMap((s) => s.boxes ?? []);
      out.set(p.id, { text: p.sentences.map((s) => s.text).join(" "), boxes: nonEmpty(p.boxes) ?? nonEmpty(all) });
      for (const s of p.sentences) out.set(s.id, { text: s.text, boxes: nonEmpty(s.boxes), fallback: nonEmpty(p.boxes) });
    }
  }
  for (const f of floatsOf(doc)) {
    out.set(f.id, { text: f.caption.map((s) => s.text).join(" ") || f.label, boxes: nonEmpty(f.boxes) });
    for (const s of f.caption) out.set(s.id, { text: s.text, boxes: nonEmpty(s.boxes), fallback: nonEmpty(f.boxes) });
  }
  return out;
}
