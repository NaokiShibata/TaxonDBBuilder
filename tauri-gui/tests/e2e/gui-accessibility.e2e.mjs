import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

test("tabs expose selection and skip disabled views with the keyboard", async ({ page }) => {
  await openGuiApp(page);
  const setup = page.getByRole("tab", { name: "Run Setup" });
  await expect(setup).toHaveAttribute("aria-selected", "true");
  await setup.focus();
  await page.keyboard.press("ArrowRight");
  await expect(setup).toBeFocused();
  await page.evaluate(() => {
    document.querySelector("#tab-monitor").disabled = false;
    document.querySelector("#tab-results").disabled = false;
  });
  await page.keyboard.press("ArrowRight");
  const monitor = page.getByRole("tab", { name: "Run Monitor" });
  await expect(monitor).toBeFocused();
  await expect(monitor).toHaveAttribute("aria-selected", "true");
  await expect(setup).toHaveAttribute("tabindex", "-1");
  await expect(page.getByRole("tabpanel", { name: "Run Monitor" })).toBeVisible();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "Results" })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(setup).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "Results" })).toBeFocused();
  await page.keyboard.press("Home");
  await expect(setup).toBeFocused();
});

test("help, action names, and live updates expose accessible semantics", async ({ page }) => {
  await openGuiApp(page);
  await expect(page.getByRole("button", { name: "Add taxid", exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Add marker", exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Clear all taxids", exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Clear all markers", exact: true })).toHaveCount(1);
  const help = page.locator(".help-tip").first();
  await help.focus();
  await expect(help).toHaveAccessibleDescription("Add TaxIDs directly or find them by scientific name.");
  await expect(page.locator("#log-view")).toHaveAttribute("role", "log");
  await expect(page.locator("#status")).toHaveAttribute("role", "status");
  await expect(page.locator("#phase")).toHaveAttribute("role", "status");
});

test("muted text meets AA contrast against the light application backgrounds", async ({ page }) => {
  await openGuiApp(page);
  const ratios = await page.evaluate(() => {
    const styles = getComputedStyle(document.documentElement);
    function luminance(hex) {
      const channels = hex.trim().slice(1).match(/../g).map((channel) => {
        const value = parseInt(channel, 16) / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    }
    const foreground = luminance(styles.getPropertyValue("--muted"));
    return ["--bg-top", "--bg-bottom", "--panel", "--panel-strong"].map((name) =>
      (luminance(styles.getPropertyValue(name)) + 0.05) / (foreground + 0.05));
  });
  for (const ratio of ratios) expect(ratio).toBeGreaterThanOrEqual(4.5);
});
