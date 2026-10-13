// Results-screen integration: run -> progress -> result -> tree + MSA render ->
// search / conservation / zoom / SVG export, all through the real main.js.
//
// renderRunTree (main.js) picks tree files from the result event's `files`
// (suffix .tree.nwk), falls back to `${outputPath}.tree.nwk`, then reads the
// newick and the sibling `.msa.fasta` through read_text_file.

import { expect, test } from "@playwright/test";
import { invocations, openGuiApp } from "./tauri-stub.mjs";

const NEWICK =
  "((Taxon_alba:0.12,Taxon_rubra:0.18)0.95,(Taxon_viridis:0.30,Taxon_cyanea:0.41)0.80)root;";
// Columns 0-7 carry the agreement mix the background test relies on; 8-19 are
// fully conserved. Same fixture as tests/e2e/fixture.html.
const FASTA = [
  ">Taxon_alba",
  "AACGTACGACGTACGTACGT",
  ">Taxon_rubra",
  "AACGTACGACGTACGTACGT",
  ">Taxon_viridis",
  "AAGGTCCGACGTACGTACGT",
  ">Taxon_cyanea",
  "AATGTACGACGTACGTACGT",
].join("\n");

const TREE_PATH = "/srv/out/Results/mifish.taxid1234.tree.nwk";
const MSA_PATH = "/srv/out/Results/mifish.taxid1234.msa.fasta";

function readTreeAndMsa({ newick = NEWICK, fasta = FASTA, msaFails = false } = {}) {
  return {
    read_text_file: {
      routes: [
        { includes: ".tree.nwk", value: newick },
        { includes: ".msa.fasta", value: msaFails ? undefined : fasta, reject: msaFails ? "no msa" : undefined },
        { includes: "", reject: `no such file` },
      ],
    },
  };
}

function runCommands(extra = {}, { outputPath = "/srv/out/Results/job-1/mifish" } = {}) {
  return {
    load_gui_config: { value: {} },
    start_run: {
      value: {
        jobId: "job-1",
        jobDir: "/srv/out/Results/job-1",
        configPath: "/srv/out/Results/job-1/config/db.toml",
        outputPath,
        logPath: "/srv/out/Results/job-1/run.log",
        command: "taxondbbuilder build",
      },
    },
    ...extra,
  };
}

// Fill the required fields, start the job, then deliver progress and result.
async function runToResults(page, commands, files = [TREE_PATH, MSA_PATH]) {
  const errors = await openGuiApp(page, commands);
  await page.fill("#taxid-input", "1234");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "survey@example.com");
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("job dir:");

  await page.evaluate(
    ([treeFiles]) => {
      const emit = (payload) => window.__guiEmit__("run-event", payload);
      emit({ eventType: "progress", phase: "post_prep", percent: 60, metrics: { totalRecords: 12 } });
      emit({ eventType: "result", jobDir: "/srv/out/Results/job-1", files: treeFiles });
    },
    [files],
  );
  await expect(page.locator("#view-results.active")).toHaveCount(1);
  return errors;
}

// One <path> per background layer; each subpath starts with M.
async function backgroundCells(page) {
  const paths = await page.$$eval("#tree-view-container .msa-background", (nodes) =>
    nodes.map((node) => node.getAttribute("d")),
  );
  return paths.reduce((total, d) => total + (d.match(/M/g) || []).length, 0);
}
const nodePoints = (page) => page.locator("#tree-view-container .tree-node-point");

test("a finished run renders the tree, the MSA status and the tree picker", async ({ page }) => {
  const errors = await runToResults(page, runCommands(readTreeAndMsa()));
  expect(await page.locator("#tree-select option").count()).toBe(1);
  expect(await page.locator("#tree-select option").textContent()).toBe("TaxID 1234");
  expect(await page.locator("#tree-select").isDisabled()).toBe(false);
  await expect(page.locator("#tree-view-container svg")).toHaveCount(1);
  expect(await nodePoints(page).count()).toBe(4);
  expect(await page.locator("#tree-view-status").textContent()).toBe(
    "mifish.taxid1234.tree.nwk / 4 sequences × 20 bases",
  );
  expect(await page.locator("#msa-conservation").isDisabled()).toBe(false);
  expect(await backgroundCells(page)).toBe(8);
  expect(errors).toEqual([]);
});

test("search, conservation and zoom act on the rendered tree", async ({ page }) => {
  const errors = await runToResults(page, runCommands(readTreeAndMsa()));

  await page.fill("#tree-search", "viridis");
  expect(await page.locator("#tree-view-container .tree-node-label-match").count()).toBe(1);
  const strokes = await page.$$eval("#tree-view-container .tree-node-point", (nodes) =>
    nodes.map((node) => `${getComputedStyle(node).stroke} ${getComputedStyle(node).strokeWidth}`),
  );
  expect(strokes.filter((value) => value === "rgb(215, 103, 53) 2.5px")).toHaveLength(1);

  await page.fill("#tree-search", "");
  expect(await page.locator("#tree-view-container .tree-node-label-match").count()).toBe(0);

  await page.selectOption("#msa-conservation", "50");
  expect(await backgroundCells(page)).toBe(3);

  const before = await page.locator("#tree-view-container svg").getAttribute("viewBox");
  await page.click("#tree-zoom-in");
  const zoomed = await page.locator("#tree-view-container svg").getAttribute("viewBox");
  expect(zoomed).not.toBe(before);
  await page.click("#tree-zoom-reset");
  expect(await page.locator("#tree-view-container svg").getAttribute("viewBox")).toBe(before);
  expect(errors).toEqual([]);
});

test("exporting the tree sends well-formed SVG and reports the saved path", async ({ page }) => {
  const errors = await runToResults(
    page,
    runCommands({ ...readTreeAndMsa(), save_svg_file: { value: "/srv/tree.svg" } }),
  );
  await page.fill("#tree-search", "rubra");
  await page.click("#tree-export");
  await expect(page.locator("#tree-view-status")).toHaveText("SVG saved: /srv/tree.svg");

  const calls = await page.evaluate(() =>
    window.__guiInvocations__.filter((entry) => entry.cmd === "save_svg_file"),
  );
  expect(calls).toHaveLength(1);
  const content = calls[0].args.content;
  expect(content.startsWith("<svg")).toBe(true);
  expect(content).toContain("</svg>");
  // The markup carries the palette as presentation attributes, so the highlight
  // survives on its own without styles.css.
  expect(content).toContain('stroke="#d76735"');
  expect(content).toContain('stroke-width="2.5"');
  const parsed = await page.evaluate((markup) => {
    const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
    return {
      parserError: doc.querySelector("parsererror") !== null,
      points: doc.querySelectorAll(".tree-node-point").length,
      labels: doc.querySelectorAll("text").length,
    };
  }, content);
  expect(parsed.parserError).toBe(false);
  expect(parsed.points).toBe(4);
  expect(parsed.labels).toBeGreaterThan(4);

  // Standalone check: the markup alone, in a document that never loads
  // styles.css. The highlight must still compute; typography is CSS-only and
  // falls back (reported as a known gap).
  const standalone = await page.context().newPage();
  await standalone.setContent(content);
  const standaloneStrokes = await standalone.$$eval("svg .tree-node-point", (nodes) =>
    nodes.map((node) => `${getComputedStyle(node).stroke} ${getComputedStyle(node).strokeWidth}`),
  );
  expect(standaloneStrokes.filter((value) => value === "rgb(215, 103, 53) 2.5px")).toHaveLength(1);
  expect(standaloneStrokes.filter((value) => value === "rgb(255, 255, 255) 1px")).toHaveLength(3);
  expect(await standalone.$eval("svg .tree-node-label", (node) =>
    getComputedStyle(node).fill,
  )).toBe("rgb(46, 79, 102)");
  expect(errors).toEqual([]);
});

test("a failed SVG save is reported in the status line", async ({ page }) => {
  const errors = await runToResults(
    page,
    runCommands({ ...readTreeAndMsa(), save_svg_file: { reject: "dialog cancelled" } }),
  );
  await page.click("#tree-export");
  await expect(page.locator("#tree-view-status")).toHaveText("Failed to save SVG.");
  expect(errors).toEqual([]);
});

test("a missing MSA still renders the tree and disables conservation", async ({ page }) => {
  const errors = await runToResults(page, runCommands(readTreeAndMsa({ msaFails: true })));
  await expect(page.locator("#tree-view-container svg")).toHaveCount(1);
  expect(await page.locator("#tree-view-status").textContent()).toBe(
    "mifish.taxid1234.tree.nwk / No MSA",
  );
  expect(await page.locator("#msa-conservation").isDisabled()).toBe(true);
  expect(await backgroundCells(page)).toBe(0);
  expect(errors).toEqual([]);
});

test("an unparsable newick leaves a clear status and disabled controls", async ({ page }) => {
  const errors = await runToResults(
    page,
    runCommands(readTreeAndMsa({ newick: "((Taxon_alba:0.12,Taxon_rubra" })),
  );
  await expect(page.locator("#tree-view-status")).toHaveText(
    "Failed to load the phylogenetic tree file.",
  );
  expect(await page.locator("#tree-view-container svg").count()).toBe(0);
  expect(await page.locator("#tree-search").isDisabled()).toBe(true);
  expect(await page.locator("#tree-export").isDisabled()).toBe(true);
  expect(errors).toEqual([]);
});

test("an unreadable tree file is reported the same way", async ({ page }) => {
  const errors = await runToResults(
    page,
    runCommands({
      read_text_file: { routes: [{ includes: ".tree.nwk", reject: "failed to read: no such file" }] },
    }),
  );
  await expect(page.locator("#tree-view-status")).toHaveText(
    "Failed to load the phylogenetic tree file.",
  );
  expect(errors).toEqual([]);
});

test("no tree file disables the whole tree panel", async ({ page }) => {
  // The fallback tree path is `${outputPath}.tree.nwk`, so an empty outputPath is what
  // actually leaves the panel without a tree.
  const errors = await runToResults(
    page,
    runCommands(readTreeAndMsa(), { outputPath: "" }),
    [],
  );
  await expect(page.locator("#tree-view-status")).toHaveText("No phylogenetic tree file found.");
  expect(await page.locator("#tree-select").isDisabled()).toBe(true);
  expect(await page.locator("#tree-export").isDisabled()).toBe(true);
  expect(await page.locator("#tree-view-container svg").count()).toBe(0);
  expect(errors).toEqual([]);
});

test("with no result files the outputPath fallback names the tree", async ({ page }) => {
  const errors = await runToResults(page, runCommands(readTreeAndMsa()), ["notes.txt"]);
  expect(await page.locator("#tree-select option").count()).toBe(1);
  expect(await page.locator("#tree-select option").textContent()).toBe("mifish.tree.nwk");
  expect(await page.locator("#tree-view-status").textContent()).toContain("mifish.tree.nwk");
  expect(errors).toEqual([]);
});

test("cancel sends cancel_run and a failed cancel is logged", async ({ page }) => {
  const errors = await openGuiApp(page, runCommands());
  await page.fill("#taxid-input", "1234");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "survey@example.com");
  await page.click("#run");
  await page.click("#cancel");
  expect(await invocations(page)).toContain("cancel_run");

  await page.evaluate(() => window.__guiEmit__("run-event", { eventType: "error", message: "killed" }));
  await expect(page.locator("#log-view")).toContainText("[error] killed");
  expect(errors).toEqual([]);
});

// state.msaTreeStatus / msaTreeTaxa arrive on progress events and are rendered by
// formatTreeUnavailableMessage whenever the tree panel has nothing to draw.
async function runWithTreeStatus(page, metrics, files = [], extraCommands = {}) {
  const errors = await openGuiApp(
    page,
    runCommands({ ...readTreeAndMsa(), ...extraCommands }, { outputPath: "" }),
  );
  await page.fill("#taxid-input", "1234");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "survey@example.com");
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("job dir:");
  await page.evaluate(
    ([payloadMetrics, payloadFiles]) => {
      const emit = (payload) => window.__guiEmit__("run-event", payload);
      emit({ eventType: "progress", phase: "post_prep", percent: 90, metrics: payloadMetrics });
      emit({ eventType: "result", jobDir: "/srv/out/Results/job-1", files: payloadFiles });
    },
    [metrics, files],
  );
  await expect(page.locator("#view-results.active")).toHaveCount(1);
  return errors;
}

const treeMessage = (page) => page.locator("#tree-view-container .tree-view-message").textContent();

test("a tree skipped for size names the sequence count", async ({ page }) => {
  const errors = await runWithTreeStatus(page, {
    msaTreeStatus: "skipped_too_many_taxa",
    msaTreeTaxa: 1234,
  });
  await expect(page.locator("#tree-view-status")).toHaveText("No phylogenetic tree file found.");
  expect(await treeMessage(page)).toBe(
    "Tree generation was skipped because the sequence count exceeded the limit. (sequences: 1234)",
  );
  expect(errors).toEqual([]);
});

test("a skipped tree without a taxa count omits the count", async ({ page }) => {
  const errors = await runWithTreeStatus(page, { msaTreeStatus: "skipped_too_many_taxa" });
  expect(await treeMessage(page)).toBe(
    "Tree generation was skipped because the sequence count exceeded the limit.",
  );
  expect(errors).toEqual([]);
});

test("any other tree status is shown as the reason", async ({ page }) => {
  const errors = await runWithTreeStatus(page, { msaTreeStatus: "mafft_failed" });
  expect(await treeMessage(page)).toBe(
    "The phylogenetic tree was not generated (reason: mafft_failed)",
  );
  expect(errors).toEqual([]);
});

test("an ok status with no tree file falls back to the plain message", async ({ page }) => {
  const errors = await runWithTreeStatus(page, { msaTreeStatus: "ok" });
  expect(await treeMessage(page)).toBe("The phylogenetic tree was not generated.");
  expect(errors).toEqual([]);
});

test("the reason also shows when the tree file exists but cannot be read", async ({ page }) => {
  const errors = await runWithTreeStatus(
    page,
    { msaTreeStatus: "skipped_too_many_taxa", msaTreeTaxa: 42 },
    [TREE_PATH],
    { read_text_file: { routes: [{ includes: ".tree.nwk", reject: "failed to read" }] } },
  );
  await expect(page.locator("#tree-view-status")).toHaveText(
    "Failed to load the phylogenetic tree file.",
  );
  expect(await treeMessage(page)).toContain("sequence count exceeded the limit. (sequences: 42)");
  expect(errors).toEqual([]);
});

// Typography and the support-label halo used to live only in styles.css, so a saved
// SVG lost them. The computed values must be identical with and without the sheet.
test("the exported SVG keeps label and support styling without CSS", async ({ page }) => {
  const errors = await runToResults(page, runCommands(readTreeAndMsa()));
  await page.click("#tree-export");
  const content = await page.evaluate(
    () => window.__guiInvocations__.find((entry) => entry.cmd === "save_svg_file")?.args.content ?? "",
  );
  expect(content).not.toBe("");

  const readStyle = (selector) => {
    const style = getComputedStyle(document.querySelector(selector));
    return {
      font: style.fontFamily,
      stroke: style.stroke,
      strokeWidth: style.strokeWidth,
      paintOrder: style.paintOrder,
    };
  };
  const inApp = await page.evaluate(readStyle, "#tree-view-container .tree-node-support");
  const labelInApp = await page.evaluate(
    (selector) => getComputedStyle(document.querySelector(selector)).fontFamily,
    "#tree-view-container .tree-node-label",
  );

  const standalone = await page.context().newPage();
  await standalone.setContent(content);
  const alone = await standalone.evaluate(readStyle, "svg .tree-node-support");
  const labelAlone = await standalone.evaluate(
    (selector) => getComputedStyle(document.querySelector(selector)).fontFamily,
    "svg .tree-node-label",
  );

  expect(labelInApp).toContain("Noto Sans JP");
  expect(inApp.stroke).toBe("rgb(255, 255, 255)");
  expect(inApp.strokeWidth).toBe("3px");
  expect(inApp.paintOrder).toBe("stroke");
  expect(alone).toEqual(inApp);
  expect(labelAlone).toBe(labelInApp);
  expect(errors).toEqual([]);
});
