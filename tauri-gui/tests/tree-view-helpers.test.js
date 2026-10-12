import assert from "node:assert/strict";
import test from "node:test";

import {
  colorRuns,
  leafPalette,
  msaBackgroundPaths,
  msaRulerTicks,
} from "../src/tree-view.js";

test("colorRuns returns nothing for an empty sequence", () => {
  assert.deepEqual(colorRuns("", () => "x"), []);
});

test("colorRuns closes a single run at the sequence end", () => {
  assert.deepEqual(colorRuns("AA", () => "red"), [
    { start: 0, end: 2, color: "red" },
  ]);
});

test("colorRuns splits runs and keeps null-coloured runs", () => {
  const colorAt = (index) => (index < 2 ? "a" : index < 3 ? null : "b");
  assert.deepEqual(colorRuns("AAXXBB", colorAt), [
    { start: 0, end: 2, color: "a" },
    { start: 2, end: 3, color: null },
    { start: 3, end: 6, color: "b" },
  ]);
});

test("colorRuns calls colorAt only for valid indices", () => {
  const seen = [];
  colorRuns("AC", (index) => {
    seen.push(index);
    return index === 0 ? "a" : "b";
  });
  assert.deepEqual(seen, [0, 1]);
});

test("msaRulerTicks steps by 10 columns when cells are wide", () => {
  const ticks = msaRulerTicks({
    alignmentCellWidth: 2.4,
    alignmentLength: 25,
    alignmentStart: 816,
  });
  assert.deepEqual(ticks, [816 + 9 * 2.4, 816 + 19 * 2.4]);
});

test("msaRulerTicks widens the step for narrow cells and can be empty", () => {
  const ticks = msaRulerTicks({
    alignmentCellWidth: 1.44,
    alignmentLength: 45,
    alignmentStart: 800,
  });
  assert.deepEqual(ticks, [800 + 19 * 1.44, 800 + 39 * 1.44]);
  assert.deepEqual(
    msaRulerTicks({ alignmentCellWidth: 2.4, alignmentLength: 0, alignmentStart: 0 }),
    [],
  );
});

test("leafPalette switches every leaf colour knob at once", () => {
  assert.deepEqual(leafPalette(false), {
    fill: "#446b88",
    stroke: "#fff",
    strokeWidth: 1,
    labelFill: "#2e4f66",
    fontWeight: 400,
    labelClass: "tree-node-label",
  });
  assert.deepEqual(leafPalette(true), {
    fill: "#d76735",
    stroke: "#d76735",
    strokeWidth: 2.5,
    labelFill: "#d76735",
    fontWeight: 700,
    labelClass: "tree-node-label tree-node-label-match",
  });
});

function backgroundFixture(sequences, names = [...sequences.keys()]) {
  const leaves = names.map((name) => ({ name, children: [] }));
  const positions = new Map();
  leaves.forEach((leaf, index) => positions.set(leaf, { y: 20 + index * 28 }));
  return {
    tree: { leaves, positions },
    alignment: sequences,
    alignmentStart: 816,
    alignmentCellWidth: 2,
  };
}

test("msaBackgroundPaths groups runs by colour in first-seen order", () => {
  const data = backgroundFixture(new Map([["A", "AC"], ["B", "AG"]]));
  const agreement = [
    new Map([["A", 100]]),
    new Map([["C", 50], ["G", 50]]),
  ];
  assert.deepEqual(
    [...msaBackgroundPaths(data, agreement, 100)],
    [
      ["#3879b9", ["M818 11h2v18h-2v-18z"]],
      ["#d08a25", ["M818 39h2v18h-2v-18z"]],
    ],
  );
});

test("msaBackgroundPaths skips leaves without a sequence", () => {
  const data = backgroundFixture(new Map([["A", "AC"]]), ["A", "B"]);
  const agreement = [new Map(), new Map([["C", 0]])];
  assert.deepEqual([...msaBackgroundPaths(data, agreement, 100).keys()], [
    "#3b8f5a",
    "#3879b9",
  ]);
});

test("msaBackgroundPaths guards column 0 but not later columns on ragged agreement", () => {
  const single = backgroundFixture(new Map([["A", "A"]]));
  // Column 0 falls back to an empty Map and renders.
  assert.deepEqual([...msaBackgroundPaths(single, [], 100)], [
    ["#3b8f5a", ["M816 11h2v18h-2v-18z"]],
  ]);
  // Column 1 dereferences agreement[1] and throws instead.
  const two = backgroundFixture(new Map([["A", "AC"]]));
  assert.throws(() => msaBackgroundPaths(two, [], 100), TypeError);
});
