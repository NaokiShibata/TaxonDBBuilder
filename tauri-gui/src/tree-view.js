const SVG_NS = "http://www.w3.org/2000/svg";

export function parseNewick(newickText) {
  const text = `${newickText ?? ""}`;
  let index = 0;

  function skipWhitespace() {
    while (/\s/.test(text[index] || "")) index += 1;
  }

  function parseName() {
    skipWhitespace();
    if (text[index] === "'") {
      index += 1;
      let name = "";
      while (index < text.length) {
        if (text[index] !== "'") {
          name += text[index++];
        } else if (text[index + 1] === "'") {
          name += "'";
          index += 2;
        } else {
          index += 1;
          return name;
        }
      }
      throw new Error("Unterminated quoted Newick name");
    }

    const start = index;
    while (index < text.length && !"(),:;".includes(text[index])) index += 1;
    return text.slice(start, index).trim();
  }

  function parseLength() {
    skipWhitespace();
    if (text[index] !== ":") return null;
    index += 1;
    skipWhitespace();
    const start = index;
    while (index < text.length && !",);".includes(text[index])) index += 1;
    const raw = text.slice(start, index).trim();
    const length = Number(raw);
    if (!raw || !Number.isFinite(length)) {
      throw new Error(`Invalid Newick branch length: ${raw || "empty"}`);
    }
    return length;
  }

  function parseNode() {
    skipWhitespace();
    const children = [];
    if (text[index] === "(") {
      index += 1;
      do {
        children.push(parseNode());
        skipWhitespace();
        if (text[index] === ",") index += 1;
        else break;
      } while (index < text.length);
      skipWhitespace();
      if (text[index] !== ")") throw new Error("Expected ')' in Newick tree");
      index += 1;
    }
    return { name: parseName(), length: parseLength(), children };
  }

  const root = parseNode();
  skipWhitespace();
  if (text[index] !== ";") throw new Error("Newick tree must end with ';'");
  index += 1;
  skipWhitespace();
  if (index !== text.length) throw new Error("Unexpected text after Newick tree");
  return root;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, `${value}`);
  }
  return element;
}

export function renderTreeSVG(root, containerEl) {
  containerEl.replaceChildren();

  const leaves = [];
  const positions = new Map();
  let maxDepth = 0;
  let maxDistance = 0;
  let usableLengths = true;

  function inspect(node, depth, distance) {
    maxDepth = Math.max(maxDepth, depth);
    const branchLength = node.length;
    if (depth > 0 && (!Number.isFinite(branchLength) || branchLength < 0)) {
      usableLengths = false;
    }
    const nextDistance = distance + (depth === 0 ? 0 : branchLength || 0);
    maxDistance = Math.max(maxDistance, nextDistance);
    positions.set(node, { depth, distance: nextDistance, x: 0, y: 0 });
    if (!node.children.length) leaves.push(node);
    for (const child of node.children) inspect(child, depth + 1, nextDistance);
  }

  inspect(root, 0, 0);
  if (!leaves.length) throw new Error("Newick tree has no leaves");

  const rowHeight = 28;
  const top = 20;
  const left = 20;
  const labelWidth = Math.min(
    300,
    Math.max(90, ...leaves.map((leaf) => leaf.name.length * 7 + 16)),
  );
  const width = Math.max(560, containerEl.clientWidth || 0);
  const height = Math.max(100, top * 2 + Math.max(1, leaves.length - 1) * rowHeight);
  const plotWidth = Math.max(180, width - left - labelWidth);
  const useLengths = usableLengths && maxDistance > 0;
  const xScale = plotWidth / (useLengths ? maxDistance : Math.max(1, maxDepth));

  leaves.forEach((leaf, leafIndex) => {
    positions.get(leaf).y = top + leafIndex * rowHeight;
  });
  function place(node) {
    const position = positions.get(node);
    position.x = left + (useLengths ? position.distance : position.depth) * xScale;
    if (node.children.length) {
      node.children.forEach(place);
      position.y =
        node.children.reduce((sum, child) => sum + positions.get(child).y, 0) /
        node.children.length;
    }
  }
  place(root);

  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": "系統樹",
  });
  svg.style.width = "100%";
  svg.style.height = `${height}px`;

  function draw(node) {
    const position = positions.get(node);
    if (node.children.length) {
      const childYs = node.children.map((child) => positions.get(child).y);
      svg.appendChild(
        svgElement("line", {
          x1: position.x,
          y1: Math.min(...childYs),
          x2: position.x,
          y2: Math.max(...childYs),
          stroke: "#446b88",
        }),
      );
      for (const child of node.children) {
        const childPosition = positions.get(child);
        svg.appendChild(
          svgElement("line", {
            x1: position.x,
            y1: childPosition.y,
            x2: childPosition.x,
            y2: childPosition.y,
            stroke: "#446b88",
          }),
        );
        draw(child);
      }
    } else {
      const label = svgElement("text", {
        x: position.x + 6,
        y: position.y + 4,
        fill: "#2e4f66",
        "font-size": 12,
      });
      label.textContent = node.name || "(unnamed)";
      svg.appendChild(label);
    }
  }

  draw(root);
  containerEl.appendChild(svg);
}

export function renderTreeStatus(containerEl, statusText) {
  const message = document.createElement("p");
  message.className = "tree-view-message";
  message.textContent = statusText;
  containerEl.replaceChildren(message);
}
