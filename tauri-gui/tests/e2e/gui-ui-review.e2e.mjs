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
