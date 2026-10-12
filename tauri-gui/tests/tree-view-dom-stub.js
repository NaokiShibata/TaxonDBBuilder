// Minimal DOM stub for tree-view.js characterization tests.
// Deliberately not named *.test.js so `node --test` only loads it on demand.
// svgElement() stringifies every attribute, so recording the raw string that
// reaches setAttribute pins float formatting for free.
export const el = (tag) => ({
  tag,
  attrs: [],
  children: [],
  textContent: "",
  style: {},
  listeners: [],
  hidden: true,
  className: "",
  clientWidth: 800,
  setAttribute(k, v) {
    this.attrs.push([k, `${v}`]);
  },
  appendChild(c) {
    this.children.push(c);
    return c;
  },
  append(c) {
    this.children.push(c);
  },
  replaceChildren(...cs) {
    this.children = cs;
  },
  addEventListener(t) {
    this.listeners.push(t);
  },
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 400 }),
  setPointerCapture() {},
  releasePointerCapture() {},
  hasPointerCapture: () => false,
});

globalThis.document = {
  createElementNS: (_ns, tag) => el(tag),
  createElement: el,
};

// Depth-first walk; children in recorded (append) order, so z-order and DFS
// interleaving are both visible in the returned lines.
export function flatten(node) {
  const parts = [node.tag];
  for (const [key, value] of node.attrs) parts.push(`${key}=${value}`);
  if (node.textContent) parts.push(`text=${node.textContent}`);
  if (node.listeners.length) {
    parts.push(`listeners=${node.listeners.join(",")}`);
  }
  const lines = [parts.join("|")];
  for (const child of node.children) lines.push(...flatten(child));
  return lines;
}
