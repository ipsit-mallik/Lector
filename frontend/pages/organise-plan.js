// Organise pages' draft, as the page predicts it (docs/ARCHITECTURE.md §
// "Optimistic Organise"). The draft itself lives in Python
// (src/lector/features/reading/page_plan.py), which stays the one authority:
// these are the same operations, written again so the grid can show an
// action in the frame of its input, before Python has answered. organise.js
// then checks each prediction against Python's answer and, when they differ,
// shows Python's. tests/test_organise_plan_mirror.py runs both on the same
// actions and asserts the same plans.
//
// A page is `{key, source, index, turn, new}`, as `Api._organise_view` sends
// it; a plan is an array of them, never changed in place. Positions and gaps
// are as in page_plan.py.
"use strict";

const ORGANISE_DOC_SOURCE = "doc";
const ORGANISE_QUARTER_TURN = 90;
const ORGANISE_FULL_TURN = 360;

class OrganisePlanError extends Error {
  constructor(reason) {
    super(reason);
    this.reason = reason;
  }
}

const OrganisePlan = (() => {
  const isInt = (value) => Number.isInteger(value);

  // `positions` sorted, or an error unless they are distinct places in
  // `plan` and there is at least one.
  function checkedPositions(plan, positions) {
    if (!Array.isArray(positions) || !positions.every(isInt)) throw new OrganisePlanError("bad_positions");
    const unique = [...new Set(positions)].sort((a, b) => a - b);
    if (!unique.length || unique.length !== positions.length || unique[0] < 0 || unique[unique.length - 1] >= plan.length) {
      throw new OrganisePlanError("bad_positions");
    }
    return unique;
  }

  function checkedGap(plan, gap) {
    if (!isInt(gap) || gap < 0 || gap > plan.length) throw new OrganisePlanError("bad_gap");
    return gap;
  }

  const range = (from, count) => Array.from({ length: count }, (_, i) => from + i);

  function rotate(plan, positions, degrees) {
    const chosen = checkedPositions(plan, positions);
    if (!isInt(degrees) || degrees % ORGANISE_QUARTER_TURN) throw new OrganisePlanError("bad_positions");
    const set = new Set(chosen);
    const turned = (turn) => (((turn + degrees) % ORGANISE_FULL_TURN) + ORGANISE_FULL_TURN) % ORGANISE_FULL_TURN;
    return { plan: plan.map((page, i) => (set.has(i) ? { ...page, turn: turned(page.turn) } : page)), selection: chosen };
  }

  function remove(plan, positions) {
    const chosen = new Set(checkedPositions(plan, positions));
    if (chosen.size === plan.length) throw new OrganisePlanError("every_page");
    return { plan: plan.filter((_, i) => !chosen.has(i)), selection: [] };
  }

  function move(plan, positions, gap) {
    const chosen = checkedPositions(plan, positions);
    checkedGap(plan, gap);
    const taken = new Set(chosen);
    const block = chosen.map((i) => plan[i]);
    const rest = plan.filter((_, i) => !taken.has(i));
    const at = gap - chosen.filter((i) => i < gap).length;
    return { plan: [...rest.slice(0, at), ...block, ...rest.slice(at)], selection: range(at, block.length) };
  }

  function shift(plan, positions, step) {
    const chosen = checkedPositions(plan, positions);
    const gap = step < 0 ? Math.max(chosen[0] - 1, 0) : Math.min(chosen[chosen.length - 1] + 2, plan.length);
    return move(plan, chosen, gap);
  }

  function insert(plan, gap, source, count) {
    checkedGap(plan, gap);
    if (source === ORGANISE_DOC_SOURCE || typeof source !== "string" || !isInt(count) || count < 1) {
      throw new OrganisePlanError("bad_source");
    }
    if (plan.some((page) => page.source === source)) throw new OrganisePlanError("bad_source");
    const added = range(0, count).map((index) => ({ key: `${source}:${index}`, source, index, turn: 0, new: true }));
    return { plan: [...plan.slice(0, gap), ...added, ...plan.slice(gap)], selection: range(gap, count) };
  }

  // One action, as Api.organise_action takes it (an insert also says how
  // many pages its file has, `count`): `{plan, selection}`, or an
  // OrganisePlanError for one the plan refuses.
  function apply(plan, kind, args) {
    if (kind === "rotate") return rotate(plan, args.positions, args.degrees);
    if (kind === "delete") return remove(plan, args.positions);
    if (kind === "move") return move(plan, args.positions, args.gap);
    if (kind === "shift") return shift(plan, args.positions, args.step);
    if (kind === "insert") return insert(plan, args.gap, args.source, args.count);
    throw new OrganisePlanError("bad_positions");
  }

  function samePlan(a, b) {
    return a.length === b.length && a.every((page, i) => page.key === b[i].key && page.turn === b[i].turn);
  }

  // ---- History: Python's PlanHistory as far as the page has seen it ------
  //
  // `plans` and `at` are the plans this page has seen since it last took
  // Python's word for the draft; `before` is how many steps Python already
  // had then and `moreRedo` whether it could redo further. An Undo past the
  // start (or a Redo past the end) of what the page has seen can't be
  // predicted: undo/redo return null and the page waits for Python.

  function history(view) {
    return { plans: [view.pages], at: 0, before: view.steps || 0, moreRedo: Boolean(view.can_redo) };
  }

  const current = (h) => h.plans[h.at];

  function push(h, plan) {
    if (samePlan(plan, current(h))) return { history: h, changed: false };
    const plans = [...h.plans.slice(0, h.at + 1), plan];
    return { history: { plans, at: h.at + 1, before: h.before, moreRedo: false }, changed: true };
  }

  function undo(h) {
    return h.at > 0 ? { ...h, at: h.at - 1 } : null;
  }

  function redo(h) {
    return h.at < h.plans.length - 1 ? { ...h, at: h.at + 1 } : null;
  }

  const counts = (h) => ({
    steps: h.before + h.at,
    can_undo: h.before + h.at > 0,
    can_redo: h.at < h.plans.length - 1 || h.moreRedo,
  });

  return { apply, samePlan, history, current, push, undo, redo, counts };
})();
