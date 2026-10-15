import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

test("BOLD failure policy is opt-in and available only for both", async ({ page }) => {
  const errors = await openGuiApp(page, { load_gui_config: { value: {} } });
  await expect(page.locator("#on-bold-failure")).toBeDisabled();
  await page.selectOption("#source", "both");
  await expect(page.locator("#on-bold-failure")).toBeVisible();
  await expect(page.locator("#on-bold-failure")).toHaveValue("fail-fast");
  await page.selectOption("#on-bold-failure", "continue-ncbi");
  await page.selectOption("#source", "bold");
  await expect(page.locator("#on-bold-failure")).toBeDisabled();
  await expect(page.locator("#on-bold-failure")).toHaveValue("fail-fast");
  await page.selectOption("#source", "both");
  await expect(page.locator("#on-bold-failure")).toHaveValue("fail-fast");
  expect(errors).toEqual([]);
});

test("saved partial policy and actual partial files reach Results", async ({ page }) => {
  const output = "/srv/out/job/Results/example__partial-nobold.fasta";
  const errors = await openGuiApp(page, {
    load_gui_config: { value: { source: "both", onBoldFailure: "continue-ncbi" } },
    start_run: { value: { jobId: "partial", jobDir: "/srv/out/job", outputPath: "/srv/out/job/Results/example.fasta", logPath: "/srv/out/job/run.log" } },
    open_path: { value: null },
  });
  await expect(page.locator("#on-bold-failure")).toHaveValue("continue-ncbi");
  await page.fill("#taxid-input", "999");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "test@example.invalid");
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("job dir:");
  const req = await page.evaluate(() => window.__guiInvocations__.find(x => x.cmd === "start_run").args.req);
  expect(req.source).toBe("both");
  expect(req.onBoldFailure).toBe("continue-ncbi");
  await page.evaluate((file) => {
    window.__guiEmit__("run-event", { eventType: "status", status: "PartialSuccess" });
    window.__guiEmit__("run-event", { eventType: "result", jobDir: "/srv/out/job", files: [file, file + ".manifest.json"] });
  }, output);
  await expect(page.locator("#view-results")).toHaveClass(/active/);
  await expect(page.locator("#partial-result-state")).toHaveText("部分成功（NCBIのみ）");
  await page.getByText("Output Files", { exact: true }).click();
  const fileButton = page.locator(`button[title="${output}"]`);
  await expect(fileButton).toBeVisible();
  await fileButton.click();
  expect(await page.evaluate(() => window.__guiInvocations__.filter(x => x.cmd === "open_path").at(-1).args.path)).toBe(output);
  expect(errors).toEqual([]);
});

test("cancellation stays Cancelled rather than partial success", async ({ page }) => {
  await openGuiApp(page, { load_gui_config: { value: {} }, start_run: { value: { jobId: "cancel", jobDir: "/srv/out/job", outputPath: "/srv/out/job/example.fasta", logPath: "/srv/out/job/run.log" } } });
  await page.fill("#taxid-input", "999");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "test@example.invalid");
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("job dir:");
  await page.evaluate(() => window.__guiEmit__("run-event", { eventType: "status", status: "Cancelled" }));
  await expect(page.locator("#status")).toHaveText("Cancelled");
  await expect(page.locator("#partial-result-state")).toBeEmpty();
});
