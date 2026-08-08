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

function nodeName(node) {
  return node.name || "(unnamed)";
}

function nodeSupport(node) {
  if (!node.children.length || !node.name || !/^\d+(?:\.\d+)?$/.test(node.name)) {
    return "";
  }
  return node.name;
}

function inspectTree(root) {
  const leaves = [];
  const nodes = [];
  const positions = new Map();
  let maxDepth = 0;
  let maxDistance = 0;
  let usableLengths = true;

  function inspect(node, depth, distance) {
    const branchLength = node.length;
    if (depth > 0 && (!Number.isFinite(branchLength) || branchLength < 0)) {
      usableLengths = false;
    }
    const safeLength = depth > 0 && Number.isFinite(branchLength) && branchLength >= 0
      ? branchLength
      : 0;
    const nextDistance = distance + safeLength;
    const position = {
      depth,
      distance: nextDistance,
      x: 0,
      y: 0,
    };
    maxDepth = Math.max(maxDepth, depth);
    maxDistance = Math.max(maxDistance, nextDistance);
    positions.set(node, position);
    nodes.push(node);
    if (!node.children.length) leaves.push(node);
    for (const child of node.children) inspect(child, depth + 1, nextDistance);
  }

  inspect(root, 0, 0);
  if (!leaves.length) throw new Error("Newick tree has no leaves");
  return { leaves, nodes, positions, maxDepth, maxDistance, usableLengths };
}

function createRectangularLayout(tree, containerEl) {
  const rowHeight = 28;
  const top = 20;
  const left = 20;
  const labelWidth = Math.min(
    300,
    Math.max(90, ...tree.leaves.map((leaf) => nodeName(leaf).length * 7 + 16)),
  );
  const width = Math.max(560, containerEl.clientWidth || 0);
  const height = Math.max(220, top * 2 + Math.max(1, tree.leaves.length - 1) * rowHeight);
  const plotWidth = Math.max(180, width - left - labelWidth);
  const useLengths = tree.usableLengths && tree.maxDistance > 0;
  const xScale = plotWidth / (useLengths ? tree.maxDistance : Math.max(1, tree.maxDepth));

  tree.leaves.forEach((leaf, leafIndex) => {
    tree.positions.get(leaf).y = top + leafIndex * rowHeight;
  });

  function place(node) {
    const position = tree.positions.get(node);
    position.x = left + (useLengths ? position.distance : position.depth) * xScale;
    if (node.children.length) {
      node.children.forEach(place);
      position.y = node.children.reduce(
        (sum, child) => sum + tree.positions.get(child).y,
        0,
      ) / node.children.length;
    }
  }
  place(tree.nodes[0]);

  return {
    type: "rectangular",
    width,
    height,
    tree,
    useLengths,
    root: tree.nodes[0],
  };
}

function makeViewBox(width, height) {
  return { x: 0, y: 0, width, height };
}

function viewBoxString(viewBox) {
  return [viewBox.x, viewBox.y, viewBox.width, viewBox.height].join(" ");
}

export function renderTreeSVG(root, containerEl) {
  containerEl.replaceChildren();
  const tree = inspectTree(root);
  const state = {
    root,
    containerEl,
    searchQuery: "",
    tree,
    layoutData: null,
    svg: null,
    tooltip: null,
    baseViewBox: null,
    viewBox: null,
    dragging: null,
  };

  function showTooltip(node, event) {
    if (!state.tooltip) return;
    const rect = containerEl.getBoundingClientRect();
    const left = Math.max(8, Math.min(event.clientX - rect.left + 12, rect.width - 220));
    const top = Math.max(8, Math.min(event.clientY - rect.top + 12, rect.height - 86));
    state.tooltip.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = nodeName(node);
    state.tooltip.append(title);
    state.tooltip.style.left = `${left}px`;
    state.tooltip.style.top = `${top}px`;
    state.tooltip.hidden = false;
  }

  function hideTooltip() {
    if (state.tooltip) state.tooltip.hidden = true;
  }

  function setViewBox(viewBox) {
    state.viewBox = viewBox;
    if (state.svg) state.svg.setAttribute("viewBox", viewBoxString(viewBox));
  }

  function zoomAt(factor, clientX, clientY) {
    if (!state.svg || !state.viewBox) return;
    const rect = state.svg.getBoundingClientRect();
    const anchorX = clientX == null
      ? state.viewBox.x + state.viewBox.width / 2
      : state.viewBox.x + ((clientX - rect.left) / rect.width) * state.viewBox.width;
    const anchorY = clientY == null
      ? state.viewBox.y + state.viewBox.height / 2
      : state.viewBox.y + ((clientY - rect.top) / rect.height) * state.viewBox.height;
    const minWidth = state.baseViewBox.width * 0.12;
    const maxWidth = state.baseViewBox.width * 6;
    const width = Math.max(minWidth, Math.min(maxWidth, state.viewBox.width * factor));
    const height = width * state.baseViewBox.height / state.baseViewBox.width;
    setViewBox({
      x: anchorX - ((anchorX - state.viewBox.x) * width) / state.viewBox.width,
      y: anchorY - ((anchorY - state.viewBox.y) * height) / state.viewBox.height,
      width,
      height,
    });
  }

  function attachPointerEvents(svg) {
    svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoomAt(event.deltaY < 0 ? 0.85 : 1.18, event.clientX, event.clientY);
    }, { passive: false });

    svg.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      state.dragging = {
        clientX: event.clientX,
        clientY: event.clientY,
        viewBox: { ...state.viewBox },
      };
      svg.setPointerCapture(event.pointerId);
      svg.style.cursor = "grabbing";
    });
    svg.addEventListener("pointermove", (event) => {
      if (!state.dragging) return;
      const rect = svg.getBoundingClientRect();
      const dx = (event.clientX - state.dragging.clientX) * state.dragging.viewBox.width / rect.width;
      const dy = (event.clientY - state.dragging.clientY) * state.dragging.viewBox.height / rect.height;
      setViewBox({
        ...state.viewBox,
        x: state.dragging.viewBox.x - dx,
        y: state.dragging.viewBox.y - dy,
      });
    });
    const finishDrag = (event) => {
      if (!state.dragging) return;
      state.dragging = null;
      svg.style.cursor = "grab";
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    };
    svg.addEventListener("pointerup", finishDrag);
    svg.addEventListener("pointercancel", finishDrag);
    svg.addEventListener("pointerleave", (event) => {
      if (state.dragging && !svg.hasPointerCapture(event.pointerId)) finishDrag(event);
    });
  }

  function addTooltipEvents(group, node) {
    group.addEventListener("mouseenter", (event) => showTooltip(node, event));
    group.addEventListener("mousemove", (event) => showTooltip(node, event));
    group.addEventListener("mouseleave", hideTooltip);
  }

  function isMatched(node) {
    return Boolean(state.searchQuery &&
      !node.children.length && nodeName(node).toLocaleLowerCase().includes(state.searchQuery)
    );
  }

  function drawRectangular(svg, data) {
    function draw(node) {
      const position = data.tree.positions.get(node);
      if (node.children.length) {
        const childYs = node.children.map((child) => data.tree.positions.get(child).y);
        svg.appendChild(svgElement("line", {
          x1: position.x,
          y1: Math.min(...childYs),
          x2: position.x,
          y2: Math.max(...childYs),
          stroke: "#446b88",
          "stroke-width": 1.5,
          class: "tree-node-branch",
        }));
        for (const child of node.children) {
          const childPosition = data.tree.positions.get(child);
          svg.appendChild(svgElement("line", {
            x1: position.x,
            y1: childPosition.y,
            x2: childPosition.x,
            y2: childPosition.y,
            stroke: "#446b88",
            "stroke-width": 1.5,
            class: "tree-node-branch",
          }));
          draw(child);
        }
        const support = nodeSupport(node);
        if (support && node !== data.root) {
          const supportLabel = svgElement("text", {
            x: position.x + 6,
            y: position.y - 6,
            fill: "#7b4f2c",
            "font-size": 10,
            "font-weight": 600,
            class: "tree-node-support",
          });
          supportLabel.textContent = support;
          svg.appendChild(supportLabel);
        }
        return;
      }

      const group = svgElement("g", { class: "tree-node" });
      group.appendChild(svgElement("circle", {
        cx: position.x,
        cy: position.y,
        r: 3.5,
        fill: isMatched(node) ? "#d76735" : "#446b88",
        stroke: isMatched(node) ? "#d76735" : "#fff",
        "stroke-width": isMatched(node) ? 2.5 : 1,
        class: "tree-node-point",
      }));
      const label = svgElement("text", {
        x: position.x + 7,
        y: position.y + 4,
        fill: isMatched(node) ? "#d76735" : "#2e4f66",
        "font-size": 12,
        "font-weight": isMatched(node) ? 700 : 400,
        class: `tree-node-label${isMatched(node) ? " tree-node-label-match" : ""}`,
      });
      label.textContent = nodeName(node);
      group.appendChild(label);
      addTooltipEvents(group, node);
      svg.appendChild(group);
    }
    draw(data.root);
  }

  function redraw() {
    state.tree = inspectTree(state.root);
    state.layoutData = createRectangularLayout(state.tree, containerEl);
    state.baseViewBox = makeViewBox(state.layoutData.width, state.layoutData.height);
    state.viewBox = { ...state.baseViewBox };
    state.tooltip = document.createElement("div");
    state.tooltip.className = "tree-view-tooltip";
    state.tooltip.setAttribute("role", "tooltip");
    state.tooltip.hidden = true;
    const svg = svgElement("svg", {
      xmlns: SVG_NS,
      viewBox: viewBoxString(state.viewBox),
      width: state.layoutData.width,
      height: state.layoutData.height,
      role: "img",
      "aria-label": "系統樹",
    });
    svg.style.width = "100%";
    svg.style.height = `${Math.min(720, Math.max(260, state.layoutData.height))}px`;
    svg.style.cursor = "grab";
    const title = svgElement("title");
    title.textContent = "系統樹";
    svg.appendChild(title);
    drawRectangular(svg, state.layoutData);
    state.svg = svg;
    attachPointerEvents(svg);
    containerEl.replaceChildren(svg, state.tooltip);
  }

  const controller = {
    setSearch(query) {
      state.searchQuery = `${query || ""}`.trim().toLocaleLowerCase();
      redraw();
    },
    zoomIn() {
      zoomAt(0.8);
    },
    zoomOut() {
      zoomAt(1.25);
    },
    resetView() {
      setViewBox({ ...state.baseViewBox });
    },
    getSVGMarkup() {
      return state.svg ? new XMLSerializer().serializeToString(state.svg) : "";
    },
    dispose() {
      containerEl.replaceChildren();
    },
  };

  redraw();
  return controller;
}

export function renderTreeStatus(containerEl, statusText) {
  const message = document.createElement("p");
  message.className = "tree-view-message";
  message.textContent = statusText;
  containerEl.replaceChildren(message);
}
