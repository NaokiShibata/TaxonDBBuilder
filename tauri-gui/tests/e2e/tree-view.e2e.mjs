import { expect, test } from "@playwright/test";

// Real browser regression coverage for renderTreeSVG: layout that depends on
// clientWidth, pointer/wheel geometry, tooltip clamping, XMLSerializer export.
// The Node suites (tests/tree-view*.test.js) cover the pure helpers with a DOM
// stub; they cannot see any of the maths below.

const FIXTURE = "/tests/e2e/fixture.html";
const CONTAINER = "#tree-view-container";

function parseViewBox(value) {
  const [x, y, width, height] = value.split(" ").map(Number);
  return { x, y, width, height };
}

function userPoint(viewBox, box, clientX, clientY) {
  return {
    x: viewBox.x + ((clientX - box.x) / box.width) * viewBox.width,
    y: viewBox.y + ((clientY - box.y) / box.height) * viewBox.height,
  };
}

async function openFixture(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  await page.goto(FIXTURE);
  await page.waitForSelector(`${CONTAINER} svg`);
  return errors;
}

async function viewBox(page) {
  return parseViewBox(
    await page.getAttribute(`${CONTAINER} svg`, "viewBox"),
  );
}

async function svgBox(page) {
  return page.locator(`${CONTAINER} svg`).boundingBox();
}

async function backgroundCells(page) {
  const paths = await page.$$eval(`${CONTAINER} .msa-background`, (nodes) =>
    nodes.map((node) => node.getAttribute("d")),
  );
  return paths.reduce((total, d) => total + (d.match(/M/g) || []).length, 0);
}

async function call(page, method, ...args) {
  await page.evaluate(
    ([name, callArgs]) => window.taxonFixture.controller[name](...callArgs),
    [method, args],
  );
}

test.describe("tree view in a real browser", () => {
  test("draws leaves, branches, support labels, MSA rows and ruler ticks", async ({
    page,
  }) => {
    const errors = await openFixture(page);

    await expect(page.locator(`${CONTAINER} .tree-node`)).toHaveCount(4);
    await expect(page.locator(`${CONTAINER} .tree-node-support`)).toHaveCount(2);
    await expect(page.locator(`${CONTAINER} .msa-sequence`)).toHaveCount(4);
    await expect(page.locator(`${CONTAINER} .msa-ruler`)).toHaveCount(2);
    expect(await page.locator(`${CONTAINER} .tree-node-branch`).count()).toBeGreaterThan(0);

    // Leaf rows stay in Newick order top to bottom, labels sit right of the point.
    const leaves = await page.$$eval(`${CONTAINER} .tree-node`, (nodes) =>
      nodes.map((node) => ({
        name: node.querySelector(".tree-node-label").textContent,
        cx: Number(node.querySelector(".tree-node-point").getAttribute("cx")),
        cy: Number(node.querySelector(".tree-node-point").getAttribute("cy")),
        labelX: Number(node.querySelector(".tree-node-label").getAttribute("x")),
      })),
    );
    expect(leaves.map((leaf) => leaf.name)).toEqual([
      "Taxon_alba",
      "Taxon_rubra",
      "Taxon_viridis",
      "Taxon_cyanea",
    ]);
    expect(leaves.map((leaf) => leaf.cy)).toEqual([36, 64, 92, 120]);
    for (const leaf of leaves) expect(leaf.labelX).toBeGreaterThan(leaf.cx);

    // MSA rows are laid out with real text metrics, not stubbed numbers.
    const rows = await page.$$eval(`${CONTAINER} .msa-sequence`, (nodes) =>
      nodes.map((node) => ({
        textLength: Number(node.getAttribute("textLength")),
        tspans: node.querySelectorAll("tspan").length,
        x: Number(node.getAttribute("x")),
      })),
    );
    for (const row of rows) {
      expect(row.textLength).toBeGreaterThan(0);
      expect(row.tspans).toBeGreaterThan(0);
    }
    const treeWidth = await page.evaluate(
      (selector) => document.querySelector(selector).clientWidth,
      CONTAINER,
    );
    expect(rows[0].x).toBeGreaterThan(treeWidth);

    expect(errors).toEqual([]);
  });

  test("layout width follows the real container clientWidth", async ({
    page,
  }) => {
    await openFixture(page);
    const seen = [];
    for (const width of [1280, 900, 480]) {
      await page.setViewportSize({ width, height: 800 });
      await page.evaluate(() => window.taxonFixture.render());
      const clientWidth = await page.evaluate(
        (selector) => document.querySelector(selector).clientWidth,
        CONTAINER,
      );
      const svg = await page.$eval(
        `${CONTAINER} svg`,
        (node) => ({
          width: Number(node.getAttribute("width")),
          height: Number(node.getAttribute("height")),
          styleWidth: node.style.width,
          styleHeight: node.style.height,
        }),
      );
      const treeWidth = Math.max(560, clientWidth);
      expect(svg.width).toBeCloseTo(treeWidth + Math.max(320, treeWidth * 0.7), 6);
      expect(svg.styleWidth).toBe("100%");
      expect(svg.styleHeight).toBe("260px");
      seen.push(svg.width);
    }
    // 480px viewport falls back to the 560px floor, so the last two match.
    expect(seen[0]).toBeGreaterThan(seen[1]);
    expect(seen[1]).toBeGreaterThan(seen[2]);
    expect(seen[2]).toBeCloseTo(560 + Math.max(320, 560 * 0.7), 6);
  });

  test("wheel zoom keeps the point under the cursor fixed", async ({
    page,
  }) => {
    await openFixture(page);
    const box = await svgBox(page);
    const before = await viewBox(page);
    const cursor = { x: box.x + box.width * 0.3, y: box.y + box.height * 0.4 };

    await page.mouse.move(cursor.x, cursor.y);
    await page.mouse.wheel(0, -120);

    const after = await viewBox(page);
    expect(after.width).toBeCloseTo(before.width * 0.85, 6);
    const anchorBefore = userPoint(before, box, cursor.x, cursor.y);
    const anchorAfter = userPoint(after, box, cursor.x, cursor.y);
    // Chromium rounds wheel coordinates to device pixels, so the anchor can
    // shift by a fraction of a pixel. Anchoring on the viewBox centre instead
    // of the cursor would drift by ~3% of the viewBox width.
    expect(Math.abs(anchorBefore.x - anchorAfter.x)).toBeLessThan(before.width * 0.001);
    expect(Math.abs(anchorBefore.y - anchorAfter.y)).toBeLessThan(before.height * 0.001);

    // Wheel down zooms out about the same anchor. The factors are deliberately
    // asymmetric (0.85 in, 1.18 out), so a round trip does not land on 1.0.
    await page.mouse.wheel(0, 120);
    expect((await viewBox(page)).width).toBeCloseTo(before.width * 0.85 * 1.18, 6);
  });

  test("wheel zoom clamps between 12% and 6x of the base viewBox", async ({
    page,
  }) => {
    await openFixture(page);
    const box = await svgBox(page);
    const base = await viewBox(page);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);

    for (let step = 0; step < 40; step += 1) await page.mouse.wheel(0, -120);
    const min = await viewBox(page);
    expect(min.width).toBeCloseTo(base.width * 0.12, 6);
    expect(min.height).toBeCloseTo(base.height * 0.12, 6);

    for (let step = 0; step < 80; step += 1) await page.mouse.wheel(0, 120);
    const max = await viewBox(page);
    expect(max.width).toBeCloseTo(base.width * 6, 6);
  });

  test("pointer drag pans the viewBox and stops on pointerup", async ({
    page,
  }) => {
    await openFixture(page);
    const box = await svgBox(page);
    const before = await viewBox(page);
    const start = { x: box.x + 200, y: box.y + 120 };

    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    expect(
      await page.$eval(`${CONTAINER} svg`, (node) => node.style.cursor),
    ).toBe("grabbing");

    await page.mouse.move(start.x + 60, start.y + 40);
    const during = await viewBox(page);
    expect(during.x).toBeCloseTo(before.x - (60 * before.width) / box.width, 4);
    expect(during.y).toBeCloseTo(before.y - (40 * before.height) / box.height, 4);
    expect(during.width).toBeCloseTo(before.width, 6);

    await page.mouse.up();
    expect(
      await page.$eval(`${CONTAINER} svg`, (node) => node.style.cursor),
    ).toBe("grab");
    expect(
      await page.$eval(
        `${CONTAINER} svg`,
        (node) => node.hasPointerCapture(1),
      ),
    ).toBe(false);

    // A plain move after release must not pan: the drag state is really cleared.
    await page.mouse.move(start.x + 200, start.y + 200);
    expect(await viewBox(page)).toEqual(during);
  });

  test("resetView, zoomIn and zoomOut drive the viewBox", async ({ page }) => {
    await openFixture(page);
    const base = await viewBox(page);

    await call(page, "zoomIn");
    const zoomedIn = await viewBox(page);
    expect(zoomedIn.width).toBeCloseTo(base.width * 0.8, 6);
    expect(zoomedIn.x + zoomedIn.width / 2).toBeCloseTo(base.x + base.width / 2, 6);
    expect(zoomedIn.y + zoomedIn.height / 2).toBeCloseTo(base.y + base.height / 2, 6);

    await call(page, "resetView");
    expect(await viewBox(page)).toEqual(base);

    await call(page, "zoomOut");
    expect((await viewBox(page)).width).toBeCloseTo(base.width * 1.25, 6);

    await call(page, "resetView");
    expect(await viewBox(page)).toEqual(base);
  });

  test("tooltip shows the leaf name, follows the pointer, hides and clamps", async ({
    page,
  }) => {
    await openFixture(page);
    const tooltip = page.locator(`${CONTAINER} .tree-view-tooltip`);
    await expect(tooltip).toBeHidden();

    const leafPoint = (index) =>
      page.locator(`${CONTAINER} .tree-node`).nth(index).locator(".tree-node-point");

    await leafPoint(0).hover();
    await expect(tooltip).toBeVisible();
    expect(await tooltip.textContent()).toContain("Taxon_alba");
    expect(await tooltip.getAttribute("role")).toBe("tooltip");

    await leafPoint(3).hover();
    expect(await tooltip.textContent()).toContain("Taxon_cyanea");

    const container = await page.locator(CONTAINER).boundingBox();
    const placed = await tooltip.evaluate((node) => ({
      left: parseFloat(node.style.left),
      top: parseFloat(node.style.top),
    }));
    expect(placed.left).toBeGreaterThanOrEqual(8);
    expect(placed.top).toBeGreaterThanOrEqual(8);
    expect(placed.left).toBeLessThanOrEqual(container.width - 220);
    expect(placed.top).toBeLessThanOrEqual(container.height - 86);

    await page.mouse.move(5, 5);
    await expect(tooltip).toBeHidden();

    // Position limits. Zoom in first so the leaf is big enough to hit, then
    // pan it against the right edge (a horizontal pan maps 1:1 to client px
    // because the viewBox is width-constrained by preserveAspectRatio).
    const point = leafPoint(3);
    await call(page, "zoomIn");
    await call(page, "zoomIn");
    const zoomed = await point.boundingBox();
    const from = { x: zoomed.x + zoomed.width / 2, y: zoomed.y + zoomed.height / 2 };
    const shift = container.x + container.width - 60 - from.x;
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(from.x + shift, from.y, { steps: 4 });
    await page.mouse.up();

    const moved = await point.boundingBox();
    await page.mouse.move(moved.x + moved.width / 2, moved.y + moved.height / 2);
    await expect(tooltip).toBeVisible();
    const clamped = await tooltip.evaluate((node) => ({
      left: parseFloat(node.style.left),
      top: parseFloat(node.style.top),
    }));
    expect(clamped.left).toBeCloseTo(container.width - 220, 0);
    expect(clamped.top).toBeGreaterThanOrEqual(8);

    // Short container: the vertical bound (rect.height - 86) now bites. The
    // leaf sits ~147px below the container top, so the box has to stay taller
    // than that or the pointer target gets clipped by overflow: hidden.
    await page.addStyleTag({ content: `${CONTAINER} { height: 200px; }` });
    await page.mouse.move(5, 5);
    await page.mouse.move(moved.x + moved.width / 2 + 2, moved.y + moved.height / 2 + 2);
    const short = await page.locator(CONTAINER).boundingBox();
    expect(short.height).toBeCloseTo(200, 0);
    await expect(tooltip).toBeVisible();
    const clampedTop = await tooltip.evaluate((node) => parseFloat(node.style.top));
    expect(clampedTop).toBeCloseTo(short.height - 86, 0);
  });

  test("search highlights only the matching leaf", async ({ page }) => {
    await openFixture(page);
    await call(page, "setSearch", "viridis");

    await expect(page.locator(`${CONTAINER} .tree-node-label-match`)).toHaveCount(1);
    expect(
      await page.locator(`${CONTAINER} .tree-node-label-match`).textContent(),
    ).toBe("Taxon_viridis");

    const fills = await page.$$eval(`${CONTAINER} .tree-node-point`, (nodes) =>
      nodes.map((node) => getComputedStyle(node).fill),
    );
    expect(fills).toEqual([
      "rgb(68, 107, 136)",
      "rgb(68, 107, 136)",
      "rgb(215, 103, 53)",
      "rgb(68, 107, 136)",
    ]);
    // The matched node keeps its own stroke: the palette sets stroke #d76735 /
    // stroke-width 2.5 as presentation attributes and no CSS rule outranks them.
    const strokes = await page.$$eval(`${CONTAINER} .tree-node-point`, (nodes) =>
      nodes.map((node) => {
        const style = getComputedStyle(node);
        return `${style.stroke} ${style.strokeWidth}`;
      }),
    );
    expect(strokes).toEqual([
      "rgb(255, 255, 255) 1px",
      "rgb(255, 255, 255) 1px",
      "rgb(215, 103, 53) 2.5px",
      "rgb(255, 255, 255) 1px",
    ]);

    await call(page, "setSearch", "");
    await expect(page.locator(`${CONTAINER} .tree-node-label-match`)).toHaveCount(0);
    const cleared = await page.$$eval(`${CONTAINER} .tree-node-point`, (nodes) =>
      nodes.map((node) => {
        const style = getComputedStyle(node);
        return `${style.stroke} ${style.strokeWidth}`;
      }),
    );
    expect(new Set(cleared)).toEqual(new Set(["rgb(255, 255, 255) 1px"]));
  });

  test("conservation threshold rewrites the MSA background cells", async ({
    page,
  }) => {
    await openFixture(page);
    expect(await backgroundCells(page)).toBe(8);

    await call(page, "setConservationThreshold", 50);
    expect(await backgroundCells(page)).toBe(3);

    // Out-of-range values clamp back to the 50-100 band.
    await call(page, "setConservationThreshold", 140);
    expect(await backgroundCells(page)).toBe(8);
    await call(page, "setConservationThreshold", "not-a-number");
    expect(await backgroundCells(page)).toBe(8);

    await page.evaluate(() => window.taxonFixture.render({ threshold: 50 }));
    expect(await backgroundCells(page)).toBe(3);
  });

  test("getSVGMarkup exports valid XML at the layout size, not the zoomed view", async ({
    page,
  }) => {
    const errors = await openFixture(page);
    await call(page, "zoomIn");
    const liveViewBox = await viewBox(page);

    const markup = await page.evaluate(() => window.taxonFixture.controller.getSVGMarkup());
    const report = await page.evaluate((text) => {
      const doc = new DOMParser().parseFromString(text, "image/svg+xml");
      const root = doc.documentElement;
      return {
        parseError: doc.querySelector("parsererror")?.textContent ?? "",
        root: root.tagName,
        xmlns: root.getAttribute("xmlns"),
        viewBox: root.getAttribute("viewBox"),
        width: root.getAttribute("width"),
        height: root.getAttribute("height"),
        aria: root.getAttribute("aria-label"),
        title: root.querySelector("title")?.textContent ?? "",
        leaves: doc.querySelectorAll(".tree-node").length,
        rows: doc.querySelectorAll(".msa-sequence").length,
        backgrounds: doc.querySelectorAll(".msa-background").length,
      };
    }, markup);

    expect(report.parseError).toBe("");
    expect(report.root).toBe("svg");
    expect(report.xmlns).toBe("http://www.w3.org/2000/svg");
    expect(report.aria).toBe("Phylogenetic tree and multiple sequence alignment");
    expect(report.title).toBe("Phylogenetic tree and multiple sequence alignment");
    expect(report.leaves).toBe(4);
    expect(report.rows).toBe(4);
    expect(report.backgrounds).toBeGreaterThan(0);

    const exported = parseViewBox(report.viewBox);
    expect(exported.x).toBe(0);
    expect(exported.y).toBe(0);
    expect(exported.width).toBeCloseTo(Number(report.width), 6);
    expect(exported.height).toBeCloseTo(Number(report.height), 6);
    // The live view is zoomed; the export must not inherit that.
    expect(liveViewBox.width).not.toBeCloseTo(exported.width, 6);

    expect(errors).toEqual([]);
  });

  test("dispose empties the container and a re-render starts clean", async ({
    page,
  }) => {
    await openFixture(page);
    await call(page, "dispose");
    await expect(page.locator(`${CONTAINER} *`)).toHaveCount(0);

    await page.evaluate(() => window.taxonFixture.render());
    await expect(page.locator(`${CONTAINER} .tree-node`)).toHaveCount(4);
  });

  test("a full interaction pass raises no page errors or rejections", async ({
    page,
  }) => {
    const errors = await openFixture(page);
    const box = await svgBox(page);

    await call(page, "setSearch", "taxon");
    await call(page, "setConservationThreshold", 60);
    await page
      .locator(`${CONTAINER} .tree-node`)
      .nth(1)
      .locator(".tree-node-point")
      .hover();
    await page.mouse.move(box.x + 300, box.y + 100);
    await page.mouse.wheel(0, -120);
    await page.mouse.down();
    await page.mouse.move(box.x + 380, box.y + 160);
    await page.mouse.up();
    await call(page, "zoomIn");
    await call(page, "resetView");
    await page.evaluate(() => window.taxonFixture.controller.getSVGMarkup());
    await page.evaluate(() => window.taxonFixture.render({ threshold: 75 }));

    expect(errors).toEqual([]);
    expect(await page.evaluate(() => window.taxonFixture.errors)).toEqual([]);
  });
});
