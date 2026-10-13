// Fake Tauri IPC so the real src/main.js boots in a plain browser.
//
// @tauri-apps/api 2.x talks to exactly three internals: invoke(),
// transformCallback() and unregisterCallback(). Commands are plain data so each
// test can script its own replies ({"value": ...} or {"reject": "msg"}).
// plugin:event|listen is recorded so __guiEmit__ can deliver run-events.

export const GUI_URL = "http://127.0.0.1:1420/";

export async function installTauriStub(page, commands = {}) {
  await page.addInitScript((table) => {
    const internals = {
      metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
      callbacks: new Map(),
      listeners: new Map(),
      nextCallbackId: 1,
      transformCallback(callback) {
        const id = internals.nextCallbackId++;
        internals.callbacks.set(id, callback);
        return id;
      },
      unregisterCallback(id) {
        internals.callbacks.delete(id);
      },
      convertFileSrc(path) {
        return path;
      },
      async invoke(cmd, args) {
        window.__guiInvocations__.push({ cmd, args });
        if (cmd === "plugin:event|listen") {
          const ids = internals.listeners.get(args.event) || [];
          ids.push(args.handler);
          internals.listeners.set(args.event, ids);
          return internals.nextCallbackId++;
        }
        const entry = table[cmd];
        if (entry && entry.reject) throw new Error(entry.reject);
        return entry && "value" in entry ? entry.value : null;
      },
    };
    window.__guiInvocations__ = [];
    window.__guiEmit__ = (event, payload) => {
      for (const id of internals.listeners.get(event) || []) {
        const callback = internals.callbacks.get(id);
        if (callback) callback({ event, payload });
      }
    };
    window.__TAURI_INTERNALS__ = internals;
  }, commands);
}

// Every command the GUI sent, for tests that assert on IPC traffic.
export async function invocations(page) {
  return page.evaluate(() => window.__guiInvocations__.map((entry) => entry.cmd));
}

// Boot the real app with the given command replies and collect page errors.
export async function openGuiApp(page, commands = {}) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await installTauriStub(page, commands);
  await page.goto(GUI_URL);
  await page.waitForSelector("#primer-set");
  return errors;
}
