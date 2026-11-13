// A claim's method is an action: the claim (or each of its sub-claims) names an A… id, and the same action often
// tests several of them. This is the index over the two lists — who uses what, and what role each action plays —
// used by the claims reader and by the Review view. Pure: no DOM, no API.
import type { Action, Claim, ItemOrigin } from "../types";

/** One testable unit of a claim: the claim itself, or one of its sub-claims. */
export interface Leaf { claim: string; id: string; text: string; method: string | null; evidence: string[] }
export type MethodRole = "method" | "preparation" | "unused" | "off-only";
export interface MethodInfo { action: Action; origin: ItemOrigin; role: MethodRole; usedBy: Leaf[]; offUsedBy: Leaf[] }

/** Testable leaves: the claim itself if atomic, else its sub-claims; [] for an untestable claim. */
export function leaves(c: Claim): Leaf[] {
  if (!c.testable) return [];
  if (c.sub_claims.length) return c.sub_claims.map((s) => ({ claim: c.id, id: s.id, text: s.text, method: s.method ?? null, evidence: s.evidence }));
  return [{ claim: c.id, id: c.id, text: c.text, method: c.method ?? null, evidence: c.evidence }];
}

/** Method action ids of a claim (own and sub-claims'), in order, without repeats. */
export function claimMethods(c: Claim): string[] {
  return [...new Set(leaves(c).map((l) => l.method).filter((m): m is string => !!m))];
}

/**
 * One entry per action, with the leaves that use it as their method. `usedBy` counts live claims only; an action used
 * only by switched-off claims is "off-only" (the backend refuses to delete it, and switching the claim back on must
 * not find its method gone). Preparation (transform) steps belong to every claim, so they are never "unused".
 */
export function methodIndex(actions: { item: Action; origin: ItemOrigin }[], claims: { claim: Claim; off: boolean }[]): Map<string, MethodInfo> {
  const index = new Map<string, MethodInfo>();
  for (const { item, origin } of actions) index.set(item.id, { action: item, origin, role: "unused", usedBy: [], offUsedBy: [] });
  for (const { claim, off } of claims)
    for (const leaf of leaves(claim)) {
      const info = leaf.method ? index.get(leaf.method) : undefined;
      if (info) (off ? info.offUsedBy : info.usedBy).push(leaf);
    }
  for (const info of index.values())
    info.role = info.action.kind === "transform" ? "preparation" : info.usedBy.length ? "method" : info.offUsedBy.length ? "off-only" : "unused";
  return index;
}
