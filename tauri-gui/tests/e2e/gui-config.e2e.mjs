// Boundary tests for the startup config path (loadSavedConfig).
//
// Rust contract (src-tauri/src/commands.rs, config.rs):
//   load_gui_config() -> Result<GuiConfig, String>
//   - no config file  -> Ok(GuiConfig::default())  (marker "12s", source, prefix, ncbi defaults)
//   - absent JSON key -> per-field default (struct-level #[serde(default)])
//   - bad JSON / wrong type -> Err(String), which the JS invoke() rejects on
//   - save_api_key == false -> api_key cleared before it ever reaches the GUI
// So a null payload is NOT part of the contract; the reachable failure is reject.
// The last two cases pin the recovery behaviour added in 3.3.4: neither a null payload nor
// a reject may leave the form without its default marker.

import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

async function markerPills(page) {
  return page.locator("#marker-list .marker-pill").count();
}

async function runDisabled(page) {
  return page.locator("#run").isDisabled();
}

test("a saved config overrides the HTML defaults", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: {
      value: {
        marker: "coi",
        source: "bold",
        outputPrefix: "MiFish-Unity",
        email: "survey@example.com",
        apiKey: "stored-key",
        saveApiKey: true,
      },
    },
  });
  expect(await page.locator("#marker-list .marker-pill").textContent()).toContain("coi");
  expect(await page.locator("#marker").inputValue()).toBe("coi");
  expect(await page.locator("#source").inputValue()).toBe("bold");
  expect(await page.locator("#output-prefix").inputValue()).toBe("MiFish-Unity");
  expect(errors).toEqual([]);
});

test("an empty config seeds the default marker and keeps the HTML defaults", async ({ page }) => {
  const errors = await openGuiApp(page, { load_gui_config: { value: {} } });
  // The select's first option becomes the seeded marker.
  expect(await markerPills(page)).toBe(1);
  expect(await page.locator("#marker-list .marker-pill").textContent()).toContain("complete");
  expect(await page.locator("#source").inputValue()).toBe("ncbi");
  expect(await page.locator("#output-prefix").inputValue()).not.toBe("");
  expect(await page.locator("#ncbi-db").inputValue()).toBe("nucleotide");
  // Run is still gated on the required fields, not on a missing marker.
  expect(await runDisabled(page)).toBe(true);
  expect(errors).toEqual([]);
});

test("a saved config with an empty marker falls back to the select default", async ({ page }) => {
  // start_run persists marker = req.markers.first(), which is "" only for a
  // hand-edited or older config file; the GUI must still end up usable.
  const errors = await openGuiApp(page, { load_gui_config: { value: { marker: "" } } });
  expect(await markerPills(page)).toBe(1);
  expect(errors).toEqual([]);
});

test("a partial config applies only the fields it carries", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: {
      value: { email: "partial@example.com", ncbiDb: "nuccore", ncbiDelaySec: 0, ncbiUseHistory: false },
    },
  });
  expect(await page.locator("#email").inputValue()).toBe("partial@example.com");
  expect(await page.locator("#ncbi-db").inputValue()).toBe("nuccore");
  expect(await page.locator("#ncbi-rettype").inputValue()).toBe("gb");
  expect(await page.locator("#ncbi-per-query").inputValue()).toBe("100");
  // 0 and false are values, not absences: the != null guards must let them through.
  expect(await page.locator("#ncbi-delay-sec").inputValue()).toBe("0");
  expect(await page.locator("#ncbi-use-history").isChecked()).toBe(false);
  expect(errors).toEqual([]);
});

test("saveApiKey false clears the api key field", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: { value: { apiKey: "should-not-show", saveApiKey: false } },
  });
  expect(await page.locator("#api-key").inputValue()).toBe("");
  expect(await page.locator("#save-api-key").isChecked()).toBe(false);
  expect(errors).toEqual([]);
});

test("a null payload still leaves the form usable", async ({ page }) => {
  // Not reachable with the current Rust command, but `if (!saved) return` skips
  // marker seeding, so any null-ish answer disables Run for the whole session.
  const errors = await openGuiApp(page, { load_gui_config: { value: null } });
  expect(await markerPills(page)).toBe(1);
  expect(errors).toEqual([]);
});

test("a rejected load logs a warning and still seeds the defaults", async ({ page }) => {
  // Reachable: a hand-edited or truncated gui_config.json makes serde fail.
  const errors = await openGuiApp(page, {
    load_gui_config: { reject: "failed to parse gui_config.json: expected value at line 1" },
  });
  await expect(page.locator("#log-view")).toContainText("[warn] load config failed");
  expect(await markerPills(page)).toBe(1);
  expect(errors).toEqual([]);
});

// The log lives in the Monitor view, which is disabled at startup, so a config
// failure has to be visible in the Setup view as well.
test("a normal startup shows no config notice", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: { value: { marker: "16s", source: "ncbi" } },
  });
  await expect(page.locator("#config-notice")).toBeHidden();
  expect(await markerPills(page)).toBe(1);
  expect(errors).toEqual([]);
});

test("a quarantined config names the backup file in the setup view", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: {
      reject:
        "failed to parse gui_config.json: expected value at line 1 column 40; " +
        "kept the unreadable copy at /home/u/.config/com.taxondbbuilder.gui/gui_config.json.corrupt",
    },
  });
  const notice = page.locator("#config-notice");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("gui_config.json.corrupt");
  await expect(notice).toContainText("default");
  // Startup continues on the defaults.
  expect(await markerPills(page)).toBe(1);
  expect(errors).toEqual([]);
});

test("a transient ipc failure does not claim a backup was kept", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: { reject: "load_gui_config command not available" },
  });
  const notice = page.locator("#config-notice");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("default");
  expect(await notice.textContent()).not.toContain(".corrupt");
  expect(await markerPills(page)).toBe(1);
  expect(errors).toEqual([]);
});
