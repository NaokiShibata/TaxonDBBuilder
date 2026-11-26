import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

const searchStub = { value: [{ taxId: "9606", scientificName: "Homo sapiens" }] };
const target = (path, token = "tok1") => ({ value: { path, token } });
const TARGET = { path: "/local/taxonomy.db", token: "tok1" };

function downloadCount(page) {
  return page.evaluate(
    () => window.__guiInvocations__.filter(({ cmd }) => cmd === "download_taxonomy_db").length
  );
}

async function geometry(page, selector) {
  const box = await page.locator(selector).boundingBox();
  return { x: box.x, y: box.y, bottom: box.y + box.height };
}

// Replace the download IPC: first call demands replace consent, second defers
// to a manual resolve so the test controls completion.
async function scriptConsentDownload(page) {
  await page.evaluate(() => {
    const original = window.__TAURI_INTERNALS__.invoke;
    window.__dlCount = 0;
    window.__guiInvocations__ = window.__guiInvocations__ || [];
    window.__TAURI_INTERNALS__.invoke = (cmd, args) => {
      if (cmd !== "download_taxonomy_db") return original(cmd, args);
      window.__guiInvocations__.push({ cmd, args });
      window.__dlCount += 1;
      if (window.__dlCount === 1) {
        return Promise.reject(new Error("REPLACE_CONFIRMATION_REQUIRED"));
      }
      return new Promise((resolve) => {
        window.resolveTaxonomyDownload = resolve;
      });
    };
  });
}

test("existing target: confirm dialog consents per attempt, then success re-runs the search", async ({ page }) => {
  const dialogMessages = [];
  page.on("dialog", (dialog) => {
    dialogMessages.push(dialog.message());
    dialog.accept();
  });
  await openGuiApp(page, { search_taxonomy: searchStub, get_taxonomy_db_path: target(TARGET.path, TARGET.token) });
  await scriptConsentDownload(page);

  await page.fill("#taxon-name-input", "Homo sapiens");
  await expect(page.locator("#taxon-candidates")).toContainText("Homo sapiens : 9606");
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Download target: /local/taxonomy.db");

  // Button beside the input; status on the line below.
  const input = await geometry(page, "#taxon-name-input");
  const button = await geometry(page, "#taxonomy-download");
  const status = await geometry(page, "#taxonomy-status");
  expect(button.x).toBeGreaterThan(input.x);
  expect(Math.abs(button.y - input.y)).toBeLessThan(10);
  expect(status.y).toBeGreaterThanOrEqual(input.bottom);
  expect(status.x).toBeLessThan(button.x);

  await page.click("#taxonomy-download");
  await page.waitForFunction(() => window.resolveTaxonomyDownload);
  await expect(page.locator("#taxonomy-download")).toBeDisabled();
  await expect(page.locator("#taxonomy-download")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#taxonomy-target-change")).toBeDisabled();
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Downloading taxonomy DB…");
  await page.click("#taxonomy-download", { force: true });
  expect(await downloadCount(page)).toBe(2); // consent attempt + confirmed attempt
  expect(dialogMessages.some((m) => m.includes("/local/taxonomy.db") && m.includes("taxonomy.db.old"))).toBe(true);

  const calls = await page.evaluate(() =>
    window.__guiInvocations__.filter(({ cmd }) => cmd === "download_taxonomy_db").map(({ args }) => args)
  );
  expect(calls[0]).toEqual({ confirmReplace: false, expectedToken: "tok1" });
  expect(calls[1]).toEqual({ confirmReplace: true, expectedToken: "tok1" });

  const searchesBefore = await page.evaluate(
    () => window.__guiInvocations__.filter(({ cmd }) => cmd === "search_taxonomy").length
  );
  await page.evaluate(() => window.resolveTaxonomyDownload({ path: "/local/taxonomy.db", token: "tok2" }));
  await expect(page.locator("#taxonomy-status-text")).toHaveText("taxonomy.db updated: /local/taxonomy.db");
  await expect(page.locator("#taxonomy-download")).toBeEnabled();
  await expect(page.locator("#taxonomy-target-change")).toBeEnabled();
  await expect(page.locator("#log-view")).toContainText("taxonomy.db updated");

  await expect
    .poll(async () =>
      page.evaluate(() => window.__guiInvocations__.filter(({ cmd }) => cmd === "search_taxonomy").length)
    )
    .toBeGreaterThan(searchesBefore);
});

test("declining the replace dialog downloads nothing", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.dismiss());
  const errors = await openGuiApp(page, { get_taxonomy_db_path: target(TARGET.path, TARGET.token) });
  await scriptConsentDownload(page);

  await page.click("#taxonomy-download");
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Download target: /local/taxonomy.db");
  await expect(page.locator("#taxonomy-download")).toBeEnabled();
  expect(await downloadCount(page)).toBe(1);
  expect(await page.evaluate(() => window.__dlCount)).toBe(1);
  expect(errors).toEqual([]);
});

test("a backend failure reports the error, unlocks, and a retry is possible", async ({ page }) => {
  const errors = await openGuiApp(page, {
    search_taxonomy: searchStub,
    get_taxonomy_db_path: target(TARGET.path, TARGET.token),
    download_taxonomy_db: { reject: "network down" },
  });

  await page.click("#taxonomy-download");
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Download failed. Target: /local/taxonomy.db");
  await expect(page.locator("#log-view")).toContainText("taxonomy download failed: Error: network down");
  await expect(page.locator("#taxonomy-download")).toBeEnabled();
  await expect(page.locator("#taxonomy-download")).toHaveAttribute("aria-busy", "false");

  await page.click("#taxonomy-download");
  expect(await downloadCount(page)).toBe(2);
  expect(errors).toEqual([]);
});

test("a stale target token is rejected and the display refreshes", async ({ page }) => {
  await openGuiApp(page, { get_taxonomy_db_path: target(TARGET.path, TARGET.token) });
  await page.evaluate(() => {
    const original = window.__TAURI_INTERNALS__.invoke;
    window.__TAURI_INTERNALS__.invoke = (cmd, args) => {
      if (cmd !== "download_taxonomy_db") return original(cmd, args);
      window.__guiInvocations__.push({ cmd, args });
      return Promise.reject(new Error("taxonomy target changed elsewhere; re-read the target line and retry"));
    };
  });
  await page.click("#taxonomy-download");
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Target changed — now: /local/taxonomy.db");
});

test("Change… moves the download target and a cancelled picker keeps it", async ({ page }) => {
  await openGuiApp(page, {
    get_taxonomy_db_path: target(TARGET.path, TARGET.token),
    choose_taxonomy_db_dir: target("/data/taxa/taxonomy.db", "tok9"),
  });
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Download target: /local/taxonomy.db");

  await page.click("#taxonomy-target-change");
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Download target: /data/taxa/taxonomy.db");
  await expect(page.locator("#log-view")).toContainText("taxonomy.db target: /data/taxa/taxonomy.db");
  await page.evaluate(() => {
    const original = window.__TAURI_INTERNALS__.invoke;
    window.__TAURI_INTERNALS__.invoke = (cmd, args) =>
      cmd === "choose_taxonomy_db_dir" ? Promise.resolve(null) : original(cmd, args);
  });
  await page.click("#taxonomy-target-change");
  await expect(page.locator("#taxonomy-status-text")).toHaveText("Download target: /data/taxa/taxonomy.db");
});
