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

async function openMonitor(page) {
  await page.goto("/tests/e2e/fixture.html");
  await page.evaluate(async () => {
    const { createMonitorView } = await import("/src/lib/monitor-view.js");
    const logsEl = document.createElement("pre");
    logsEl.id = "monitor-log";
    logsEl.style.cssText = "height:100px; overflow:auto; line-height:20px";
    document.body.append(logsEl);
    const element = () => document.createElement("div");
    const statusEl = element();
    window.monitorFixture = {
      logsEl,
      statusEl,
      resultFilesEl: element(),
    };
    window.monitorFixture.view = createMonitorView({
      ...window.monitorFixture,
      phaseEl: element(), progressEl: element(), progressDetailEl: element(),
      metricsListEl: element(),
      openPath: async () => { throw new Error("Permission denied"); },
    });
    window.flushMonitor = () => new Promise(requestAnimationFrame);
  });
}

test("new logs append and preserve the reader's scroll position", async ({ page }) => {
  await openMonitor(page);
  const result = await page.evaluate(async () => {
    const { view, logsEl } = window.monitorFixture;
    for (let index = 0; index < 40; index += 1) view.appendLog(`line ${index}`);
    await window.flushMonitor();
    const following = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 1;
    logsEl.scrollTop = 100;
    const first = logsEl.firstChild;
    view.appendLog("new line");
    await window.flushMonitor();
    return { following, scrollTop: logsEl.scrollTop, preserved: first === logsEl.firstChild,
      lastLine: logsEl.textContent.endsWith("new line\n") };
  });
  expect(result).toEqual({ following: true, scrollTop: 100, preserved: true, lastLine: true });
});

test("logs stay bounded, reset cleanly, and report file open errors", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openMonitor(page);
  const result = await page.evaluate(async () => {
    const { view, logsEl, resultFilesEl, statusEl } = window.monitorFixture;
    for (let index = 0; index < 5000; index += 1) view.appendLog(`line ${index}`);
    await window.flushMonitor();
    for (let index = 5000; index < 5010; index += 1) view.appendLog(`line ${index}`);
    await window.flushMonitor();
    const lines = logsEl.textContent.trimEnd().split("\n");
    view.appendLog("discard on reset");
    view.reset();
    view.appendLog("after reset");
    await window.flushMonitor();
    const resetText = logsEl.textContent;
    view.renderResultFiles(["/unreadable.file"]);
    resultFilesEl.querySelector("button").click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    return { count: lines.length, first: lines[0], last: lines.at(-1), resetText,
      errorLogged: logsEl.textContent.includes("Permission denied"), status: statusEl.textContent };
  });
  expect(result.count).toBeLessThanOrEqual(4000);
  expect(result.first).toContain("Older GUI log lines omitted");
  expect(result.last).toBe("line 5009");
  expect(result.resetText).toBe("after reset\n");
  expect(result.errorLogged).toBe(true);
  expect(result.status).toContain("Error");
  expect(errors).toEqual([]);
});
