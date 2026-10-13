import assert from "node:assert/strict";
import test from "node:test";

import {
  calculateAlignmentAgreement,
  formatTreeUnavailableMessage,
  parseFasta,
  parseNewick,
  renderTreeSVG,
  showMsaBackground,
} from "../src/tree-view.js";
import { el, flatten } from "./tree-view-dom-stub.js";

// Characterization helpers: render through the DOM stub and flatten the result
// into one line per element, in append order. Every attribute value is the
// exact string handed to setAttribute, so float formatting is pinned too.
function render(newick, entries = [], threshold = 100, clientWidth = 800) {
  const container = el("div");
  container.clientWidth = clientWidth;
  const controller = renderTreeSVG(
    parseNewick(newick),
    container,
    new Map(entries),
    threshold,
  );
  return { container, controller, lines: () => flatten(container) };
}

function withClass(lines, name) {
  return lines.filter((line) => attr(line, "class") === name);
}

function attr(line, key) {
  const match = line.match(new RegExp(`(?:^|\\|)${key}=([^|]*)`));
  return match ? match[1] : undefined;
}

test("parseFasta reads aligned records by FASTA ID", () => {
  assert.deepEqual(
    [...parseFasta(">leaf-1 description\nAC-G\n>leaf-2\nACTG\n").entries()],
    [["leaf-1", "AC-G"], ["leaf-2", "ACTG"]],
  );
});

test("parseFasta rejects sequences with different lengths", () => {
  assert.throws(() => parseFasta(">a\nAC\n>b\nA\n"), /not aligned/);
});

test("MSA backgrounds disappear at the selected agreement threshold", () => {
  const agreement = calculateAlignmentAgreement(
    new Map([
      ["a", "A-C"],
      ["b", "ATC"],
      ["c", "AGT"],
    ]),
  );
  assert.equal(agreement[0].get("A"), 100);
  assert.equal(agreement[1].get("T"), 50);
  assert.equal(showMsaBackground("T", agreement[1], 100), true);
  assert.equal(showMsaBackground("T", agreement[1], 50), false);
  assert.equal(showMsaBackground("-", agreement[1], 50), true);
});

test("tree skeleton keeps DFS order between sibling connector lines", () => {
  const { lines } = render("((A:1,B:2)0.95,C:3)root;");
  const branch = "stroke=#446b88|stroke-width=1.5|class=tree-node-branch";
  const leaf = (cx, cy, x, y, name) =>
    [
      "g|class=tree-node|listeners=mouseenter,mousemove,mouseleave",
      `circle|cx=${cx}|cy=${cy}|r=3.5|fill=#446b88|stroke=#fff|stroke-width=1|class=tree-node-point`,
      `text|x=${x}|y=${y}|fill=#2e4f66|font-family="Noto Sans JP", sans-serif|font-size=12|font-weight=400|class=tree-node-label|text=${name}`,
    ];
  assert.deepEqual(lines(), [
    "div",
    "svg|xmlns=http://www.w3.org/2000/svg|viewBox=0 0 800 220|width=800|height=220|role=img|aria-label=Phylogenetic tree|listeners=wheel,pointerdown,pointermove,pointerup,pointercancel,pointerleave",
    "title|text=Phylogenetic tree",
    `line|x1=20|y1=34|x2=20|y2=76|${branch}`,
    `line|x1=20|y1=34|x2=365|y2=34|${branch}`,
    `line|x1=365|y1=20|x2=365|y2=48|${branch}`,
    `line|x1=365|y1=20|x2=710|y2=20|${branch}`,
    ...leaf(710, 20, 717, 24, "A"),
    `line|x1=365|y1=48|x2=710|y2=48|${branch}`,
    ...leaf(710, 48, 717, 52, "B"),
    'text|x=371|y=28|fill=#7b4f2c|font-family="Noto Sans JP", sans-serif|font-size=10|font-weight=600|paint-order=stroke|stroke=#fff|stroke-width=3|stroke-linejoin=round|class=tree-node-support|text=0.95',
    `line|x1=20|y1=76|x2=365|y2=76|${branch}`,
    ...leaf(365, 76, 372, 80, "C"),
    "div|role=tooltip",
  ]);
  // The root is named "root", so nodeSupport() rejects it and no label follows.
  assert.equal(withClass(lines(), "tree-node-support").length, 1);
});

test("support labels appear only for named internal nodes below the root", () => {
  const { lines } = render("(((A,B)0.8,C)0.9,D);");
  assert.deepEqual(
    withClass(lines(), "tree-node-support").map((line) => attr(line, "text")),
    ["0.8", "0.9"],
  );
  // An unnamed internal node emits nothing and a numeric root is still skipped.
  assert.deepEqual(
    withClass(render("((A,B)0.8,C)0.95;").lines(), "tree-node-support").map(
      (line) => attr(line, "text"),
    ),
    ["0.8"],
  );
});

test("a single-leaf tree draws no connector lines", () => {
  const { lines } = render("A;");
  assert.deepEqual(lines(), [
    "div",
    "svg|xmlns=http://www.w3.org/2000/svg|viewBox=0 0 800 220|width=800|height=220|role=img|aria-label=Phylogenetic tree|listeners=wheel,pointerdown,pointermove,pointerup,pointercancel,pointerleave",
    "title|text=Phylogenetic tree",
    "g|class=tree-node|listeners=mouseenter,mousemove,mouseleave",
    "circle|cx=20|cy=20|r=3.5|fill=#446b88|stroke=#fff|stroke-width=1|class=tree-node-point",
    'text|x=27|y=24|fill=#2e4f66|font-family="Noto Sans JP", sans-serif|font-size=12|font-weight=400|class=tree-node-label|text=A',
    "div|role=tooltip",
  ]);
  assert.equal(withClass(lines(), "tree-node-branch").length, 0);
});

test("background paths group one run per colour in first-seen order", () => {
  const { lines } = render("(A,B);", [
    ["A", "ACGT"],
    ["B", "CGTA"],
  ]);
  assert.deepEqual(withClass(lines(), "msa-background"), [
    "path|d=M816 27h131v18h-131v-18zM1209 55h131v18h-131v-18z|fill=#3b8f5a|fill-opacity=0.24|class=msa-background",
    "path|d=M947 27h131v18h-131v-18zM816 55h131v18h-131v-18z|fill=#3879b9|fill-opacity=0.24|class=msa-background",
    "path|d=M1078 27h131v18h-131v-18zM947 55h131v18h-131v-18z|fill=#d08a25|fill-opacity=0.24|class=msa-background",
    "path|d=M1209 27h131v18h-131v-18zM1078 55h131v18h-131v-18z|fill=#c95555|fill-opacity=0.24|class=msa-background",
  ]);
  // Backgrounds mark columns that are NOT conserved, so identical sequences at
  // threshold 100 emit none of them.
  assert.equal(
    withClass(
      render("(A,B);", [
        ["A", "ACGT"],
        ["B", "ACGT"],
      ]).lines(),
      "msa-background",
    ).length,
    0,
  );
});

test("a lower conservation threshold removes background paths", () => {
  const alignment = [
    ["A", "ACGT"],
    ["B", "AGGT"],
  ];
  const view = render("(A,B);", alignment, 100);
  assert.equal(withClass(view.lines(), "msa-background").length, 2);
  view.controller.setConservationThreshold(50);
  assert.equal(withClass(view.lines(), "msa-background").length, 0);
});

test("MSA rows keep missing sequences and repeat runs together", () => {
  const { lines } = render("(A,B);", [["A", "AAGT"]]);
  const all = lines();
  const start = all.findIndex(
    (line) => attr(line, "class") === "msa-sequence",
  );
  assert.deepEqual(all.slice(start, start + 5), [
    'text|x=816|y=40|font-family="Noto Sans Mono", "Liberation Mono", monospace|font-size=12|textLength=524|lengthAdjust=spacingAndGlyphs|xml:space=preserve|class=msa-sequence',
    "tspan|fill=#3b8f5a|text=AA",
    "tspan|fill=#d08a25|text=G",
    "tspan|fill=#c95555|text=T",
    "text|x=816|y=68|fill=#b04a4a|font-size=11|class=msa-missing|text=No matching MSA sequence",
  ]);
});

test("ruler step follows the alignment cell width", () => {
  const wide = render("(A,B);", [
    ["A", "A".repeat(200)],
    ["B", "A".repeat(200)],
  ]);
  const ticks = withClass(wide.lines(), "msa-ruler");
  assert.equal(ticks.length, 20);
  assert.equal(attr(ticks[0], "x"), "839.58");
  assert.equal(attr(ticks[1], "x"), "865.78");
  assert.equal(attr(ticks[ticks.length - 1], "x"), "1337.38");

  const narrow = render("(A,B);", [
    ["A", "A".repeat(300)],
    ["B", "A".repeat(300)],
  ]);
  const narrowTicks = withClass(narrow.lines(), "msa-ruler");
  assert.equal(narrowTicks.length, 15);
  assert.equal(attr(narrowTicks[0], "x"), "849.1866666666667");
  assert.equal(attr(narrowTicks[1], "x"), "884.12");
  assert.equal(attr(narrowTicks[narrowTicks.length - 1], "x"), "1338.2533333333333");
});

test("search highlights matching leaves only", () => {
  const view = render("(A,B)0.95;");
  view.controller.setSearch("a");
  const lines = view.lines();
  assert.deepEqual(withClass(lines, "tree-node-point"), [
    "circle|cx=710|cy=20|r=3.5|fill=#d76735|stroke=#d76735|stroke-width=2.5|class=tree-node-point",
    "circle|cx=710|cy=48|r=3.5|fill=#446b88|stroke=#fff|stroke-width=1|class=tree-node-point",
  ]);
  const labels = lines.filter((line) =>
    (attr(line, "class") ?? "").startsWith("tree-node-label"),
  );
  assert.deepEqual(
    labels.map((line) => attr(line, "class")),
    ["tree-node-label tree-node-label-match", "tree-node-label"],
  );
  assert.equal(attr(labels[0], "fill"), "#d76735");
  assert.equal(attr(labels[0], "font-weight"), "700");
  assert.equal(attr(labels[1], "fill"), "#2e4f66");
  assert.equal(attr(labels[1], "font-weight"), "400");
  // Only the leaf label carries the match class; the internal node 0.95 has no
  // label element at all and is never highlighted.
  assert.equal(
    labels.filter((line) => attr(line, "class").includes("-match")).length,
    1,
  );
});

test("tooltip and pointer listeners are registered once per element", () => {
  const all = render("(A,B);").lines();
  const nodes = withClass(all, "tree-node");
  assert.equal(nodes.length, 2);
  for (const node of nodes) {
    assert.equal(attr(node, "listeners"), "mouseenter,mousemove,mouseleave");
  }
  assert.equal(
    attr(all.find((line) => line.startsWith("svg|")), "listeners"),
    "wheel,pointerdown,pointermove,pointerup,pointercancel,pointerleave",
  );
});

test("formatTreeUnavailableMessage explains the taxa limit", () => {
  assert.equal(
    formatTreeUnavailableMessage("skipped_too_many_taxa", 501),
    "Tree generation was skipped because the sequence count exceeded the limit. (sequences: 501)",
  );
});
