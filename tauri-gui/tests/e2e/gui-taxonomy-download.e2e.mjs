import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

const searchStub = { value: [{ taxId: "9606", scientificName: "Homo sapiens" }] };

function downloadCount(page) {
  return page.evaluate(
    () => window.__guiInvocations__.filter(({ cmd }) => cmd === "download_taxonomy_db").length
  );
}

test("Download taxonomy DB blocks double runs, reports success, and re-runs the search", async ({ page }) => {
  await openGuiApp(page, { search_taxonomy: searchStub });
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
  await expect(page.locator("#taxonomy-status")).toHaveText("taxonomy.db updated");
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
    download_taxonomy_db: { reject: "network down" },
  });

  await page.click("#taxonomy-download");
  await expect(page.locator("#taxonomy-status")).toHaveText("Download failed");
  await expect(page.locator("#log-view")).toContainText("taxonomy download failed: Error: network down");
  await expect(page.locator("#taxonomy-download")).toBeEnabled();
  await expect(page.locator("#taxonomy-download")).toHaveAttribute("aria-busy", "false");

  await page.click("#taxonomy-download");
  expect(await downloadCount(page)).toBe(2);
  expect(errors).toEqual([]);
});
