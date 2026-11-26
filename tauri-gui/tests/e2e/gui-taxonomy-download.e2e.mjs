import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

const searchStub = { value: [{ taxId: "9606", scientificName: "Homo sapiens" }] };
const pathStub = { value: "/local/taxonomy.db" };

async function geometry(page, selector) {
  const box = await page.locator(selector).boundingBox();
  return { x: box.x, y: box.y, bottom: box.y + box.height };
}

function downloadCount(page) {
  return page.evaluate(
    () => window.__guiInvocations__.filter(({ cmd }) => cmd === "download_taxonomy_db").length
  );
}

test("Download taxonomy DB blocks double runs, reports success, and re-runs the search", async ({ page }) => {
  await openGuiApp(page, { search_taxonomy: searchStub, get_taxonomy_db_path: pathStub });
  await page.evaluate(() => {
    const original = window.__TAURI_INTERNALS__.invoke;
    window.__TAURI_INTERNALS__.invoke = (cmd, args) => {
      if (cmd !== "download_taxonomy_db") return original(cmd, args);
      window.__guiInvocations__.push({ cmd, args });
      return new Promise((resolve) => {
        window.resolveTaxonomyDownload = resolve;
      });
    };
  });

  await page.fill("#taxon-name-input", "Homo sapiens");
  await expect(page.locator("#taxon-candidates")).toContainText("Homo sapiens : 9606");
  await expect(page.locator("#taxonomy-status")).toHaveText("Download target: /local/taxonomy.db");

  // Button sits beside the input; status renders on the line below it.
  const input = await geometry(page, "#taxon-name-input");
  const button = await geometry(page, "#taxonomy-download");
  const status = await geometry(page, "#taxonomy-status");
  expect(button.x).toBeGreaterThan(input.x);
  expect(Math.abs(button.y - input.y)).toBeLessThan(10);
  expect(status.y).toBeGreaterThanOrEqual(input.bottom);
  expect(status.x).toBeLessThan(button.x);
  const searchesBefore = await page.evaluate(
    () => window.__guiInvocations__.filter(({ cmd }) => cmd === "search_taxonomy").length
  );

  await page.click("#taxonomy-download");
  await expect(page.locator("#taxonomy-download")).toBeDisabled();
  await expect(page.locator("#taxonomy-download")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#taxonomy-status")).toHaveText("Downloading taxonomy DB…");
  await page.click("#taxonomy-download", { force: true });
  expect(await downloadCount(page)).toBe(1);

  await page.evaluate(() => window.resolveTaxonomyDownload("/local/taxonomy.db"));
  await expect(page.locator("#taxonomy-status")).toHaveText("taxonomy.db updated: /local/taxonomy.db");
  await expect(page.locator("#taxonomy-download")).toBeEnabled();
  await expect(page.locator("#taxonomy-download")).toHaveAttribute("aria-busy", "false");
  expect(await downloadCount(page)).toBe(1);
  await expect(page.locator("#log-view")).toContainText("taxonomy.db updated");

  // Success re-runs the pending scientific-name search.
  await expect
    .poll(async () =>
      page.evaluate(() => window.__guiInvocations__.filter(({ cmd }) => cmd === "search_taxonomy").length)
    )
    .toBeGreaterThan(searchesBefore);
});

test("a failed download reports the error and lets the user retry", async ({ page }) => {
  const errors = await openGuiApp(page, {
    search_taxonomy: searchStub,
    get_taxonomy_db_path: pathStub,
    download_taxonomy_db: { reject: "network down" },
  });

  await page.click("#taxonomy-download");
  await expect(page.locator("#taxonomy-status")).toHaveText("Download failed. Target: /local/taxonomy.db");
  await expect(page.locator("#log-view")).toContainText("taxonomy download failed: Error: network down");
  await expect(page.locator("#taxonomy-download")).toBeEnabled();
  await expect(page.locator("#taxonomy-download")).toHaveAttribute("aria-busy", "false");

  await page.click("#taxonomy-download");
  expect(await downloadCount(page)).toBe(2);
  expect(errors).toEqual([]);
});
