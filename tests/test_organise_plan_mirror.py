"""The page's prediction of Organise's draft (frontend/pages/organise-plan.js)
against Python's (src/lector/features/reading/page_plan.py), which stays the
one authority.

The grid shows each action from the prediction before Python answers
(docs/ARCHITECTURE.md § "Optimistic Organise"), so the two must agree: every
action kind, refusals included, then Undo and Redo, over seeded random runs.
When they agree the reader never sees a correction. Each run is played in
Python as it is made up (so its actions fit the plan as it is by then), then
in Node, and the plan, the selection and the history counts are compared
after every step.

Skipped when Node is not installed.
"""
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.features.reading import page_plan  # noqa: E402

PLAN_JS = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "organise-plan.js"
SEEDS = range(40)
STEPS = 30
INSERT_COUNT = 3

RUNNER = r"""
const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\nthis.P = OrganisePlan; this.E = OrganisePlanError;", context);
const { P, E } = context;
const runs = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const page = (i) => ({ key: `doc:${i}`, source: "doc", index: i, turn: 0, new: false });
const out = runs.map(({ pages, steps }) => {
  let h = P.history({ pages: Array.from({ length: pages }, (_, i) => page(i)), steps: 0, can_redo: false });
  return steps.map(([kind, args]) => {
    if (kind === "undo" || kind === "redo") {
      const moved = P[kind](h);
      if (moved) h = moved;
      return { ok: Boolean(moved), plan: P.current(h).map((p) => [p.key, p.turn]), ...P.counts(h) };
    }
    try {
      const { plan, selection } = P.apply(P.current(h), kind, args);
      h = P.push(h, plan).history;
      return { ok: true, plan: plan.map((p) => [p.key, p.turn]), selection, ...P.counts(h) };
    } catch (err) {
      if (!(err instanceof E)) throw err;
      return { ok: false, reason: err.reason, plan: P.current(h).map((p) => [p.key, p.turn]), ...P.counts(h) };
    }
  });
});
process.stdout.write(JSON.stringify(out));
"""


def _random_step(rng: random.Random, size: int, inserted: int):
    """An action as the page sends it for a plan of `size` pages, valid or
    not (refusals are compared too), or an undo/redo."""
    def some_positions():
        if rng.random() < 0.08:
            return [rng.randrange(-1, size + 2)]  # out of range
        return sorted(rng.sample(range(size), rng.randint(1, min(size, 4))))

    kind = rng.choice(["move", "move", "shift", "shift", "rotate", "delete", "insert", "undo", "redo"])
    if kind == "move":
        return kind, {"positions": some_positions(), "gap": rng.randint(-1, size + 1)}
    if kind == "shift":
        return kind, {"positions": some_positions(), "step": rng.choice([-1, 1])}
    if kind == "rotate":
        return kind, {"positions": some_positions(), "degrees": rng.choice([90, -90])}
    if kind == "delete":
        return kind, {"positions": list(range(size)) if rng.random() < 0.1 else some_positions()}
    if kind == "insert":
        source = f"insert-{rng.randrange(inserted + 1)}"  # sometimes one already in
        return kind, {"source": source, "gap": rng.randint(0, size), "count": INSERT_COUNT}
    return kind, None


def _python_step(history: page_plan.PlanHistory, kind: str, args) -> dict:
    """One step in Python, as `Api.organise_action` / `organise_undo` take it."""
    def snapshot(**extra):
        return {
            "plan": [[p.key, p.turn] for p in history.current],
            "steps": history.steps, "can_undo": history.can_undo, "can_redo": history.can_redo, **extra,
        }

    if kind in ("undo", "redo"):
        return snapshot(ok=getattr(history, kind)() is not None)
    plan = history.current
    try:
        if kind == "rotate":
            new = page_plan.rotate(plan, args["positions"], args["degrees"])
            selection = page_plan.checked_positions(plan, args["positions"])
        elif kind == "delete":
            new, selection = page_plan.delete(plan, args["positions"]), ()
        elif kind == "move":
            new, selection = page_plan.move(plan, args["positions"], args["gap"])
        elif kind == "shift":
            new, selection = page_plan.shift(plan, args["positions"], args["step"])
        else:
            new, selection = page_plan.insert(plan, args["gap"], args["source"], args["count"])
    except page_plan.PlanError as exc:
        return snapshot(ok=False, reason=exc.reason)
    history.push(new)
    return snapshot(ok=True, selection=list(selection))


def _make_run(seed: int) -> tuple[dict, list]:
    """A seeded run (its pages and steps) and Python's result after each."""
    rng = random.Random(seed)
    pages = rng.randint(1, 9)
    history = page_plan.PlanHistory(page_plan.initial(pages))
    steps, results = [], []
    for _ in range(STEPS):
        inserted = len({p.source for plan in [history.current] for p in plan if p.source != page_plan.DOC})
        step = _random_step(rng, len(history.current), inserted)
        steps.append(step)
        results.append(_python_step(history, *step))
    return {"pages": pages, "steps": steps}, results


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class PlanMirrorTests(unittest.TestCase):
    def test_the_prediction_matches_python_after_every_step(self):
        made = [_make_run(seed) for seed in SEEDS]
        with tempfile.TemporaryDirectory() as tmp:
            runner = Path(tmp) / "mirror.js"
            runner.write_text(RUNNER, encoding="utf-8")
            data = Path(tmp) / "runs.json"
            data.write_text(json.dumps([run for run, _ in made]), encoding="utf-8")
            result = subprocess.run(
                ["node", str(runner), str(PLAN_JS), str(data)], capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        predicted = json.loads(result.stdout)
        kinds_seen = set()
        for seed, (run, python_side), page_side in zip(SEEDS, made, predicted):
            for number, (step, js, py) in enumerate(zip(run["steps"], page_side, python_side)):
                kinds_seen.add((step[0], py["ok"]))
                with self.subTest(seed=seed, step=number, action=step):
                    self.assertEqual(js, py)
        # Every kind was played accepted and, where it can be, refused.
        for kind in ("move", "shift", "rotate", "delete", "insert", "undo", "redo"):
            self.assertIn((kind, True), kinds_seen)
        for kind in ("move", "rotate", "delete", "insert", "undo", "redo"):
            self.assertIn((kind, False), kinds_seen)


if __name__ == "__main__":
    unittest.main()
