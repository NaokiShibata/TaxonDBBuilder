// Static file server for the Playwright fixture. ES modules need http(s),
// file:// would block the import of src/tree-view.js.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, normalize, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "../..");
const PORT = Number(process.env.PORT ?? 4173);
const TYPES = {
  ".css": "text/css",
  ".html": "text/html",
  ".js": "text/javascript",
  ".json": "application/json",
  ".mjs": "text/javascript",
  ".svg": "image/svg+xml",
};

createServer(async (request, response) => {
  const path = new URL(request.url, "http://localhost").pathname;
  const file = resolve(ROOT, `.${normalize(path)}`);
  if (!file.startsWith(ROOT)) {
    response.writeHead(403).end("forbidden");
    return;
  }
  try {
    const body = await readFile(file);
    response.writeHead(200, {
      "content-type": TYPES[extname(file)] ?? "application/octet-stream",
    });
    response.end(body);
  } catch {
    response.writeHead(404).end("not found");
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`tree-view fixture server: http://127.0.0.1:${PORT}`);
});
