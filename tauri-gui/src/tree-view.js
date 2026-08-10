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
  if (index !== text.length)
    throw new Error("Unexpected text after Newick tree");
  return root;
}

export function parseFasta(fastaText) {
  const records = new Map();
  let id = "";
  let sequence = "";

  function saveRecord() {
    if (!id) return;
    if (records.has(id)) throw new Error(`Duplicate FASTA ID: ${id}`);
    records.set(id, sequence);
  }

  for (const rawLine of `${fastaText ?? ""}`.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    if (line.startsWith(">")) {
      saveRecord();
      id = line.slice(1).trim().split(/\s+/, 1)[0];
      if (!id) throw new Error("FASTA header has no ID");
      sequence = "";
    } else {
      if (!id) throw new Error("FASTA sequence appears before a header");
      sequence += line.replace(/\s/g, "");
    }
  }
  saveRecord();
  if (!records.size) throw new Error("FASTA has no records");

  const lengths = new Set(
    Array.from(records.values(), (value) => value.length),
  );
  if (lengths.size !== 1) throw new Error("FASTA records are not aligned");
  return records;
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
  if (
    !node.children.length ||
    !node.name ||
    !/^\d+(?:\.\d+)?$/.test(node.name)
  ) {
    return "";
  }
  return node.name;
}

const MSA_FONT_FAMILY = '"Noto Sans Mono", "Liberation Mono", monospace';

function msaBaseColor(character) {
  if (/[Aa]/.test(character)) return "#3b8f5a";
  if (/[Cc]/.test(character)) return "#3879b9";
  if (/[Gg]/.test(character)) return "#d08a25";
  if (/[TtUu]/.test(character)) return "#c95555";
  if (/[-.]/.test(character)) return "#9aa9b3";
  return "#584c74";
}

function normalizedMsaBase(character) {
  const base = `${character || ""}`.toUpperCase();
  if (base === "-" || base === ".") return "";
  return base === "U" ? "T" : base;
}

export function calculateAlignmentAgreement(alignment) {
  const sequences = [...alignment.values()];
  const length = sequences[0]?.length || 0;
  return Array.from({ length }, (_, index) => {
    const counts = new Map();
    let total = 0;
    for (const sequence of sequences) {
      const base = normalizedMsaBase(sequence[index]);
      if (!base) continue;
      counts.set(base, (counts.get(base) || 0) + 1);
      total += 1;
    }
    return new Map(
      [...counts].map(([base, count]) => [base, (count / total) * 100]),
    );
  });
}

export function showMsaBackground(character, columnAgreement, threshold) {
  const base = normalizedMsaBase(character);
  return !base || (columnAgreement.get(base) || 0) < threshold;
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
    const safeLength =
      depth > 0 && Number.isFinite(branchLength) && branchLength >= 0
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

function createRectangularLayout(tree, containerEl, alignment) {
  const rowHeight = 28;
  const top = alignment.size ? 36 : 20;
  const left = 20;
  const labelWidth = Math.min(
    300,
    Math.max(90, ...tree.leaves.map((leaf) => nodeName(leaf).length * 7 + 16)),
  );
  const treeWidth = Math.max(560, containerEl.clientWidth || 0);
  const alignmentLength = Math.max(
    0,
    ...Array.from(alignment.values(), (sequence) => sequence.length),
  );
  const alignmentViewport = Math.max(320, treeWidth * 0.7);
  const alignmentCellWidth = alignmentLength
    ? (alignmentViewport - 36) / alignmentLength
    : 0;
  const alignmentFontSize = alignmentLength ? 12 : 0;
  const alignmentStart = treeWidth + 16;
  const alignmentWidth = alignmentLength ? alignmentViewport : 0;
  const width = treeWidth + alignmentWidth;
  const viewportWidth = width;
  const height = Math.max(
    220,
    top * 2 + Math.max(1, tree.leaves.length - 1) * rowHeight,
  );
  const plotWidth = Math.max(180, treeWidth - left - labelWidth);
  const useLengths = tree.usableLengths && tree.maxDistance > 0;
  const xScale =
    plotWidth / (useLengths ? tree.maxDistance : Math.max(1, tree.maxDepth));

  tree.leaves.forEach((leaf, leafIndex) => {
    tree.positions.get(leaf).y = top + leafIndex * rowHeight;
  });

  function place(node) {
    const position = tree.positions.get(node);
    position.x =
      left + (useLengths ? position.distance : position.depth) * xScale;
    if (node.children.length) {
      node.children.forEach(place);
      position.y =
        node.children.reduce(
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
    alignment,
    alignmentLength,
    alignmentStart,
    alignmentCellWidth,
    alignmentFontSize,
    viewportWidth,
  };
}

function makeViewBox(width, height) {
  return { x: 0, y: 0, width, height };
}

function viewBoxString(viewBox) {
  return [viewBox.x, viewBox.y, viewBox.width, viewBox.height].join(" ");
}

export function renderTreeSVG(
  root,
  containerEl,
  alignment = new Map(),
  conservationThreshold = 100,
) {
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
    alignmentAgreement: calculateAlignmentAgreement(alignment),
    conservationThreshold,
  };

  function showTooltip(node, event) {
    if (!state.tooltip) return;
    const rect = containerEl.getBoundingClientRect();
    const left = Math.max(
      8,
      Math.min(event.clientX - rect.left + 12, rect.width - 220),
    );
    const top = Math.max(
      8,
      Math.min(event.clientY - rect.top + 12, rect.height - 86),
    );
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
    const anchorX =
      clientX == null
        ? state.viewBox.x + state.viewBox.width / 2
        : state.viewBox.x +
          ((clientX - rect.left) / rect.width) * state.viewBox.width;
    const anchorY =
      clientY == null
        ? state.viewBox.y + state.viewBox.height / 2
        : state.viewBox.y +
          ((clientY - rect.top) / rect.height) * state.viewBox.height;
    const minWidth = state.baseViewBox.width * 0.12;
    const maxWidth = state.baseViewBox.width * 6;
    const width = Math.max(
      minWidth,
      Math.min(maxWidth, state.viewBox.width * factor),
    );
    const height = (width * state.baseViewBox.height) / state.baseViewBox.width;
    setViewBox({
      x: anchorX - ((anchorX - state.viewBox.x) * width) / state.viewBox.width,
      y:
        anchorY - ((anchorY - state.viewBox.y) * height) / state.viewBox.height,
      width,
      height,
    });
  }

  function attachPointerEvents(svg) {
    svg.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        zoomAt(event.deltaY < 0 ? 0.85 : 1.18, event.clientX, event.clientY);
      },
      { passive: false },
    );

    svg.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
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
      const dx =
        ((event.clientX - state.dragging.clientX) *
          state.dragging.viewBox.width) /
        rect.width;
      const dy =
        ((event.clientY - state.dragging.clientY) *
          state.dragging.viewBox.height) /
        rect.height;
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
      if (svg.hasPointerCapture(event.pointerId))
        svg.releasePointerCapture(event.pointerId);
    };
    svg.addEventListener("pointerup", finishDrag);
    svg.addEventListener("pointercancel", finishDrag);
    svg.addEventListener("pointerleave", (event) => {
      if (state.dragging && !svg.hasPointerCapture(event.pointerId))
        finishDrag(event);
    });
  }

  function addTooltipEvents(group, node) {
    group.addEventListener("mouseenter", (event) => showTooltip(node, event));
    group.addEventListener("mousemove", (event) => showTooltip(node, event));
    group.addEventListener("mouseleave", hideTooltip);
  }

  function isMatched(node) {
    return Boolean(
      state.searchQuery &&
      !node.children.length &&
      nodeName(node).toLocaleLowerCase().includes(state.searchQuery),
    );
  }

  function drawRectangular(svg, data) {
    function draw(node) {
      const position = data.tree.positions.get(node);
      if (node.children.length) {
        const childYs = node.children.map(
          (child) => data.tree.positions.get(child).y,
        );
        svg.appendChild(
          svgElement("line", {
            x1: position.x,
            y1: Math.min(...childYs),
            x2: position.x,
            y2: Math.max(...childYs),
            stroke: "#446b88",
            "stroke-width": 1.5,
            class: "tree-node-branch",
          }),
        );
        for (const child of node.children) {
          const childPosition = data.tree.positions.get(child);
          svg.appendChild(
            svgElement("line", {
              x1: position.x,
              y1: childPosition.y,
              x2: childPosition.x,
              y2: childPosition.y,
              stroke: "#446b88",
              "stroke-width": 1.5,
              class: "tree-node-branch",
            }),
          );
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
      group.appendChild(
        svgElement("circle", {
          cx: position.x,
          cy: position.y,
          r: 3.5,
          fill: isMatched(node) ? "#d76735" : "#446b88",
          stroke: isMatched(node) ? "#d76735" : "#fff",
          "stroke-width": isMatched(node) ? 2.5 : 1,
          class: "tree-node-point",
        }),
      );
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

    if (!data.alignmentLength) return;
    const rulerStep =
      data.alignmentCellWidth >= 2.4
        ? 10
        : Math.ceil(24 / data.alignmentCellWidth / 10) * 10;
    for (
      let index = rulerStep - 1;
      index < data.alignmentLength;
      index += rulerStep
    ) {
      const tick = svgElement("text", {
        x: data.alignmentStart + index * data.alignmentCellWidth,
        y: 16,
        fill: "#4b708c",
        "font-family": MSA_FONT_FAMILY,
        "font-size": 10,
        "text-anchor": "middle",
        class: "msa-ruler",
      });
      tick.textContent = "│";
      svg.appendChild(tick);
    }

    const backgroundPaths = new Map();
    for (const leaf of data.tree.leaves) {
      const sequence = data.alignment.get(nodeName(leaf));
      if (sequence == null) continue;
      const y = data.tree.positions.get(leaf).y - 9;
      let runStart = 0;
      let runColor = showMsaBackground(
        sequence[0],
        state.alignmentAgreement[0] || new Map(),
        state.conservationThreshold,
      )
        ? msaBaseColor(sequence[0] || "")
        : null;
      for (let index = 1; index <= sequence.length; index += 1) {
        const color =
          index < sequence.length &&
          showMsaBackground(
            sequence[index],
            state.alignmentAgreement[index],
            state.conservationThreshold,
          )
            ? msaBaseColor(sequence[index])
            : null;
        if (color === runColor) continue;
        if (runColor) {
          const x = data.alignmentStart + runStart * data.alignmentCellWidth;
          const width = (index - runStart) * data.alignmentCellWidth;
          const commands = backgroundPaths.get(runColor) || [];
          commands.push(`M${x} ${y}h${width}v18h-${width}v-18z`);
          backgroundPaths.set(runColor, commands);
        }
        runStart = index;
        runColor = color;
      }
    }
    for (const [color, commands] of backgroundPaths) {
      svg.appendChild(
        svgElement("path", {
          d: commands.join(""),
          fill: color,
          "fill-opacity": 0.24,
          class: "msa-background",
        }),
      );
    }

    for (const leaf of data.tree.leaves) {
      const sequence = data.alignment.get(nodeName(leaf));
      const y = data.tree.positions.get(leaf).y + 4;
      if (sequence == null) {
        const missing = svgElement("text", {
          x: data.alignmentStart,
          y,
          fill: "#b04a4a",
          "font-size": 11,
          class: "msa-missing",
        });
        missing.textContent = "MSAに対応する配列がありません";
        svg.appendChild(missing);
        continue;
      }
      const row = svgElement("text", {
        x: data.alignmentStart,
        y,
        "font-family": MSA_FONT_FAMILY,
        "font-size": data.alignmentFontSize,
        textLength: sequence.length * data.alignmentCellWidth,
        lengthAdjust: "spacingAndGlyphs",
        "xml:space": "preserve",
        class: "msa-sequence",
      });
      let runStart = 0;
      let runColor = msaBaseColor(sequence[0] || "");
      for (let index = 1; index <= sequence.length; index += 1) {
        const color =
          index < sequence.length ? msaBaseColor(sequence[index]) : null;
        if (color === runColor) continue;
        const base = svgElement("tspan", { fill: runColor });
        base.textContent = sequence.slice(runStart, index);
        row.appendChild(base);
        runStart = index;
        runColor = color;
      }
      svg.appendChild(row);
    }
  }

  function redraw() {
    state.tree = inspectTree(state.root);
    state.layoutData = createRectangularLayout(
      state.tree,
      containerEl,
      alignment,
    );
    state.baseViewBox = makeViewBox(
      state.layoutData.viewportWidth,
      state.layoutData.height,
    );
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
      "aria-label": alignment.size
        ? "系統樹とMultiple sequence alignment"
        : "系統樹",
    });
    svg.style.width = "100%";
    svg.style.height = `${Math.min(720, Math.max(260, state.layoutData.height))}px`;
    svg.style.cursor = "grab";
    const title = svgElement("title");
    title.textContent = alignment.size
      ? "系統樹とMultiple sequence alignment"
      : "系統樹";
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
    setConservationThreshold(threshold) {
      const value = Number(threshold);
      state.conservationThreshold = Number.isFinite(value)
        ? Math.min(100, Math.max(50, value))
        : 100;
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
      if (!state.svg) return "";
      const exported = state.svg.cloneNode(true);
      exported.setAttribute(
        "viewBox",
        viewBoxString(
          makeViewBox(state.layoutData.width, state.layoutData.height),
        ),
      );
      exported.setAttribute("width", state.layoutData.width);
      exported.setAttribute("height", state.layoutData.height);
      return new XMLSerializer().serializeToString(exported);
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

export function formatTreeUnavailableMessage(status, taxa) {
  if (status === "skipped_too_many_taxa") {
    const count = taxa == null ? "" : `（対象配列数: ${taxa}）`;
    return `対象配列数が上限を超えたため、系統樹の生成をスキップしました${count}。`;
  }
  return status && status !== "ok"
    ? `系統樹は生成されませんでした（理由: ${status}）`
    : "系統樹は生成されませんでした。";
}
