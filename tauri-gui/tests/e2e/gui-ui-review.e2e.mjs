import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

const commands = {
  load_gui_config: { value: {} },
  start_run: { value: { outputPath: "/srv/out/job", jobDir: "/srv/job", logPath: "/srv/job/run.log" } },
};

async function ready(page, extra = {}) {
  const errors = await openGuiApp(page, { ...commands, ...extra });
  await page.fill("#taxid-input", "1234");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "survey@example.com");
  return errors;
}

test("a running job cannot restart and cancellation stays pending until terminal status", async ({ page }) => {
  const errors = await ready(page);
  await expect(page.locator("#cancel")).toBeDisabled();
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("job dir:");
  await expect(page.locator("#run")).toBeDisabled();
  await expect(page.locator("#run")).toHaveAttribute("aria-busy", "true");
  await page.evaluate(() => {
    document.querySelector("#setup-form").dispatchEvent(new Event("submit", { cancelable: true }));
  });
  expect(await page.evaluate(() => window.__guiInvocations__.filter(({ cmd }) => cmd === "start_run").length)).toBe(1);
  await page.click("#cancel");
  await expect(page.locator("#cancel")).toBeDisabled();
  await expect(page.locator("#status")).toHaveText("Cancelling…");
  await page.evaluate(() => window.__guiEmit__("run-event", { eventType: "status", status: "Cancelled" }));
  await page.click("#tab-setup");
  await expect(page.locator("#run")).toBeEnabled();
  await expect(page.locator("#run")).toHaveAttribute("aria-busy", "false");
  expect(errors).toEqual([]);
});

test("Escape cancels pending and in-flight taxonomy suggestions", async ({ page }) => {
  await openGuiApp(page, { search_taxonomy: { value: [{ taxId: "1", scientificName: "Taxon" }] } });
  await page.fill("#taxon-name-input", "Taxon");
  await page.press("#taxon-name-input", "Escape");
  await page.waitForTimeout(350);
  await expect(page.locator("#taxon-candidates")).not.toHaveClass(/active/);
  expect(await page.evaluate(() => window.__guiInvocations__.filter(({ cmd }) => cmd === "search_taxonomy").length)).toBe(0);
  await page.evaluate(() => {
    const original = window.__TAURI_INTERNALS__.invoke;
    window.__TAURI_INTERNALS__.invoke = (cmd, args) => cmd === "search_taxonomy"
      ? new Promise((resolve) => { window.resolveTaxonomy = resolve; })
      : original(cmd, args);
  });
  await page.fill("#taxon-name-input", "Taxon again");
  await page.waitForFunction(() => window.resolveTaxonomy);
  await page.press("#taxon-name-input", "Escape");
  await page.evaluate(() => window.resolveTaxonomy([{ taxId: "1", scientificName: "Old taxon" }]));
  await expect(page.locator("#taxon-candidates")).not.toHaveClass(/active/);
});

test("invalid numeric inputs block submission instead of being partially parsed", async ({ page }) => {
  await ready(page);
  for (const [id, value] of [["ncbi-per-query", "12abc"], ["ncbi-delay-sec", "-1"], ["f-length-min", "1.2"], ["f-length-max", "4294967296"]]) {
    await page.evaluate(([id, value]) => {
      const input = document.getElementById(id);
      input.value = value;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      document.querySelector("#setup-form").dispatchEvent(new Event("submit", { cancelable: true }));
    }, [id, value]);
    expect(await page.locator(`#${id}`).evaluate((el) => el.validity.valid)).toBe(false);
    expect(await page.evaluate(() => window.__guiInvocations__.some(({ cmd }) => cmd === "start_run"))).toBe(false);
    await page.locator(`#${id}`).fill("");
  }
});

for (const action of ["reset", "import"]) {
  test(`${action} clears numeric validation left by the previous input`, async ({ page }) => {
    await ready(page, {
      choose_db_toml_file: { value: "/srv/db.toml" },
      import_db_toml: { value: { source: "ncbi", email: "survey@example.com", filters: { lengthMin: 10 } } },
    });
    await page.fill("#f-length-min", "invalid");
    expect(await page.locator("#f-length-min").evaluate((input) => input.validity.valid)).toBe(false);
    await page.click(action === "reset" ? "#reset" : "#load-db-toml");
    await expect(page.locator("#f-length-min")).toHaveValue(action === "reset" ? "" : "10");
    expect(await page.locator("#f-length-min").evaluate((input) => input.validity.valid)).toBe(true);
    if (action === "reset") {
      await page.fill("#taxid-input", "1234");
      await page.click("#add-taxid");
    }
    await page.click("#run");
    await expect(page.locator("#log-view")).toContainText("job dir:");
  });
}

test("starting errors restore Run and cancel errors allow a retry", async ({ page }) => {
  const errors = await ready(page, { start_run: { reject: "start failed" } });
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("start failed");
  await expect(page.locator("#run")).toBeEnabled();
  await expect(page.locator("#cancel")).toBeDisabled();
  await ready(page, { cancel_run: { reject: "cancel failed" } });
  await page.click("#run");
  await page.click("#cancel");
  await expect(page.locator("#log-view")).toContainText("cancel failed");
  await expect(page.locator("#cancel")).toBeEnabled();
  await expect(page.locator("#run")).toBeDisabled();
  expect(errors).toEqual([]);
});

test("clicking outside taxonomy search closes suggestions", async ({ page }) => {
  await openGuiApp(page, { search_taxonomy: { value: [{ taxId: "1", scientificName: "Taxon" }] } });
  await page.fill("#taxon-name-input", "Taxon");
  await expect(page.locator("#taxon-candidates")).toHaveClass(/active/);
  await page.click("#taxid-input");
  await expect(page.locator("#taxon-candidates")).not.toHaveClass(/active/);
});
