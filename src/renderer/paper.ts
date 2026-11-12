// The paper as running text: the OpenAlex metadata, then the JATS extraction's sections (collapsible, nested by
// level) with their figures and tables where they appear. Every sentence, paragraph, heading and figure/table carries
// its id in data-hid, so items that refer to them can be placed next to them (aligned.ts).
import { reconstructAbstract } from "./api";
import { Float, SectionNode, sectionTree } from "./doc";
import type { Document, Sentence } from "./types";
import { esc } from "./util";

export function metaParts(meta: any, doc?: Document) {
  const text = reconstructAbstract(meta?.abstract_inverted_index);
  const abstract: Sentence[] = text ? (text.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) ?? [text]).map((t, i) => ({ id: `m-abs${i}`, text: t.trim() })) : [];
  return {
    title: (meta?.title as string | undefined) ?? doc?.title ?? undefined,
    authors: ((meta?.authorships ?? []) as any[]).map((a) => a.author?.display_name).filter(Boolean) as string[],
    venue: [meta?.primary_location?.source?.display_name, meta?.publication_year, meta?.doi].filter(Boolean).join(" · "),
    abstract,
  };
}

const sent = (s: Sentence) => `<span class="sent" data-hid="${esc(s.id)}">${esc(s.text)}</span>`;

function floatHtml(f: Float): string {
  const cap = `<figcaption><b>${esc(f.label)}</b> ${f.caption.map(sent).join(" ")}</figcaption>`;
  if (f.kind === "figure") return `<figure class="fig" data-hid="${esc(f.id)}">${cap}</figure>`;
  const rows = f.rows.map((r, i) => `<tr>${r.map((c) => (i === 0 ? `<th>${esc(c)}</th>` : `<td>${esc(c)}</td>`)).join("")}</tr>`).join("");
  return `<figure class="tbl" data-hid="${esc(f.id)}">${cap}${rows ? `<div class="tbl-wrap"><table>${rows}</table></div>` : ""}</figure>`;
}

export function paperHtml(doc: Document | undefined, meta: any): string {
  const m = metaParts(meta, doc);
  let html = `<article class="reader"><div class="meta-card">`;
  if (m.title) html += `<h1>${esc(m.title)}</h1>`;
  if (m.authors.length) html += `<p class="authors">${esc(m.authors.join(", "))}</p>`;
  if (m.venue) html += `<p class="venue">${esc(m.venue)}</p>`;
  const hasAbstract = doc?.sections.some((s) => /abstract/i.test(s.title));
  if (m.abstract.length && !hasAbstract) html += `<details open class="r-sec"><summary><h2>Abstract <span class="r-note">(OpenAlex)</span></h2></summary><p>${m.abstract.map(sent).join(" ")}</p></details>`;
  html += `</div>`;
  if (!doc) return html + `<p class="muted">The paper's text couldn't be loaded.</p></article>`;
  const { roots, loose } = sectionTree(doc);
  const node = (n: SectionNode): string => {
    const h = Math.min(6, n.section.level + 1);
    const title = n.section.title ? esc(n.section.title) : `<span class="muted">(untitled section)</span>`;
    return `<details open class="r-sec r-l${Math.min(n.section.level, 4)}"><summary><h${h} data-hid="${esc(n.section.id)}">${title}</h${h}></summary>
      ${n.section.paragraphs.map((p) => `<p data-hid="${esc(p.id)}">${p.sentences.map(sent).join(" ")}</p>`).join("")}
      ${n.floats.map(floatHtml).join("")}${n.children.map(node).join("")}</details>`;
  };
  html += roots.map(node).join("");
  if (loose.length) html += `<details open class="r-sec"><summary><h2>Figures and tables</h2></summary>${loose.map(floatHtml).join("")}</details>`;
  return html + `</article>`;
}
