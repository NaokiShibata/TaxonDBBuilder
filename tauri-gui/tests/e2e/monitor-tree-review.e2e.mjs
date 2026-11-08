import { expect, test } from "@playwright/test";

test("tree search preserves the SVG and zoom while updating highlights", async ({ page }) => {
  await page.goto("/tests/e2e/fixture.html");
  const result = await page.evaluate(() => {
    const controller = window.taxonFixture.controller;
    controller.zoomIn();
    const svg = document.querySelector("svg");
    const viewBox = svg.getAttribute("viewBox");
    controller.setSearch("viridis");
    return {
      sameSvg: svg === document.querySelector("svg"),
      sameZoom: viewBox === document.querySelector("svg").getAttribute("viewBox"),
      matched: document.querySelector(".tree-node-label-match")?.textContent,
    };
  });
  expect(result).toEqual({ sameSvg: true, sameZoom: true, matched: "Taxon_viridis" });
});
