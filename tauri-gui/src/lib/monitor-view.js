function sumValues(record) {
  return Object.values(record || {}).reduce(
    (acc, value) => acc + (Number(value) || 0),
    0,
  );
}

function taxidMetricItems(queryCountByTaxid, fetchCountByTaxid) {
  const items = [];
  const allTaxids = new Set([
    ...Object.keys(queryCountByTaxid),
    ...Object.keys(fetchCountByTaxid),
  ]);
  for (const taxid of Array.from(allTaxids).sort()) {
    if (queryCountByTaxid[taxid] != null) {
      items.push(`query count taxid=${taxid}: ${queryCountByTaxid[taxid]}`);
    }
    if (fetchCountByTaxid[taxid] != null) {
      const fetched = fetchCountByTaxid[taxid];
      const total = queryCountByTaxid[taxid];
      items.push(
        total != null
          ? `fetch progress taxid=${taxid}: ${Math.min(fetched, total)}/${total}`
          : `fetch progress taxid=${taxid}: ${fetched}`,
      );
    }
  }
  return items;
}

function boldTaxonMetricItems(
  boldSpecimenCountByTaxon,
  boldDownloadedByTaxon,
  boldMatchedByTaxon,
) {
  const items = [];
  const allBoldTaxa = new Set([
    ...Object.keys(boldSpecimenCountByTaxon),
    ...Object.keys(boldDownloadedByTaxon),
    ...Object.keys(boldMatchedByTaxon),
  ]);
  for (const taxon of Array.from(allBoldTaxa).sort()) {
    const parts = [];
    if (boldSpecimenCountByTaxon[taxon] != null) {
      parts.push(`specimens=${boldSpecimenCountByTaxon[taxon]}`);
    }
    if (boldDownloadedByTaxon[taxon] != null) {
      parts.push(`downloaded=${boldDownloadedByTaxon[taxon]}`);
    }
    if (boldMatchedByTaxon[taxon] != null) {
      parts.push(`matched=${boldMatchedByTaxon[taxon]}`);
    }
    if (parts.length) {
      items.push(`bold taxon=${taxon}: ${parts.join(" ")}`);
    }
  }
  return items;
}

function msaTreeItems(metrics) {
  if (metrics?.msaTreeStatus == null) return [];
  const items = [
    `msa_tree: status=${metrics.msaTreeStatus} taxa=${metrics.msaTreeTaxa ?? "-"}`,
  ];
  if (metrics.msaTreeMsaPath) items.push(`msa file: ${metrics.msaTreeMsaPath}`);
  if (metrics.msaTreeTreePath) items.push(`tree file: ${metrics.msaTreeTreePath}`);
  return items;
}

function summaryMetricItems(metrics) {
  const items = [];
  if (metrics?.matchedRecords != null)
    items.push(`matched records: ${metrics.matchedRecords}`);
  if (metrics?.keptRecordsBeforePostPrep != null) {
    items.push(`kept before post_prep: ${metrics.keptRecordsBeforePostPrep}`);
  }
  if (metrics?.primerTrimRemoved != null)
    items.push(`primer_trim removed: ${metrics.primerTrimRemoved}`);
  if (metrics?.lengthFilterRemoved != null)
    items.push(`length_filter removed: ${metrics.lengthFilterRemoved}`);
  if (metrics?.duplicateGroups != null)
    items.push(`duplicate groups: ${metrics.duplicateGroups}`);
  if (metrics?.crossOrganismGroups != null) {
    items.push(`cross_organism_groups: ${metrics.crossOrganismGroups}`);
  }
  return items.concat(msaTreeItems(metrics));
}

export function buildMetricItems(metrics) {
  const queryCountByTaxid = metrics?.queryCountByTaxid || {};
  const fetchCountByTaxid = metrics?.fetchCountByTaxid || {};
  const boldSpecimenCountByTaxon = metrics?.boldSpecimenCountByTaxon || {};
  const boldDownloadedByTaxon = metrics?.boldDownloadedByTaxon || {};
  const boldMatchedByTaxon = metrics?.boldMatchedByTaxon || {};

  return [
    ...taxidMetricItems(queryCountByTaxid, fetchCountByTaxid),
    ...boldTaxonMetricItems(
      boldSpecimenCountByTaxon,
      boldDownloadedByTaxon,
      boldMatchedByTaxon,
    ),
    ...summaryMetricItems(metrics),
  ];
}

function fetchProgressPart(queryCountByTaxid, fetchCountByTaxid) {
  const total = sumValues(queryCountByTaxid);
  if (total <= 0) return null;
  const fetched = Object.entries(fetchCountByTaxid).reduce((acc, [taxid, raw]) => {
    const done = Number(raw) || 0;
    const max = Number(queryCountByTaxid[taxid]) || done;
    return acc + Math.min(done, max);
  }, 0);
  const safeFetched = Math.min(fetched, total);
  return `fetch ${safeFetched}/${total} (${((safeFetched / total) * 100).toFixed(1)}%)`;
}

function boldProgressPart(
  boldSpecimenCountByTaxon,
  boldDownloadedByTaxon,
  boldMatchedByTaxon,
) {
  const downloadedTotal = sumValues(boldDownloadedByTaxon);
  const matchedTotal = sumValues(boldMatchedByTaxon);
  const specimenTotal = sumValues(boldSpecimenCountByTaxon);
  if (downloadedTotal <= 0 && specimenTotal <= 0) return null;
  const taxonCount = new Set([
    ...Object.keys(boldSpecimenCountByTaxon),
    ...Object.keys(boldDownloadedByTaxon),
    ...Object.keys(boldMatchedByTaxon),
  ]).size;
  let detail = `bold matched ${matchedTotal}/${downloadedTotal || 0}`;
  if (specimenTotal > 0) {
    detail += ` specimens ${specimenTotal}`;
  }
  if (taxonCount > 0) {
    detail += ` taxa ${taxonCount}`;
  }
  return detail;
}

export function buildProgressDetail(phase, percent, metrics) {
  const safePercent =
    typeof percent === "number" ? Math.max(0, Math.min(percent, 100)) : null;
  const parts = [
    fetchProgressPart(metrics?.queryCountByTaxid || {}, metrics?.fetchCountByTaxid || {}),
    boldProgressPart(
      metrics?.boldSpecimenCountByTaxon || {},
      metrics?.boldDownloadedByTaxon || {},
      metrics?.boldMatchedByTaxon || {},
    ),
  ].filter((part) => part != null);

  if (safePercent != null && phase !== "Fetch/Parse") {
    parts.push(`total ${safePercent.toFixed(1)}%`);
  }

  if (parts.length) return parts.join(" | ");
  if (safePercent != null) return `${safePercent.toFixed(1)}%`;
  return "-";
}

export function createMonitorView({
  statusEl,
  phaseEl,
  progressEl,
  progressDetailEl,
  logsEl,
  metricsListEl,
  resultFilesEl,
  openPath,
}) {
  const MAX_LOG_LINES = 4000;
  const logLines = [];
  let logRenderScheduled = false;

  function renderLogs() {
    logRenderScheduled = false;
    logsEl.textContent = `${logLines.join("\n")}\n`;
    logsEl.scrollTop = logsEl.scrollHeight;
  }

  function scheduleLogRender() {
    if (logRenderScheduled) return;

    logRenderScheduled = true;
    window.requestAnimationFrame(renderLogs);
  }

  function reset() {
    statusEl.textContent = "Idle";
    phaseEl.textContent = "-";
    progressEl.value = 0;
    progressDetailEl.textContent = "0.0%";

    logLines.length = 0;
    logsEl.textContent = "";

    metricsListEl.innerHTML = "";
  }

  function appendLog(line) {
    logLines.push(line);

    if (logLines.length > MAX_LOG_LINES) {
      const removeCount = logLines.length - MAX_LOG_LINES;
      logLines.splice(0, removeCount);

      if (
        logLines[0] !==
        "[Older GUI log lines omitted. See the .log file for the full log.]"
      ) {
        logLines.unshift(
          "[Older GUI log lines omitted. See the .log file for the full log.]",
        );
      }
    }

    scheduleLogRender();
  }

  function renderMetrics(metrics) {
    metricsListEl.innerHTML = "";
    for (const text of buildMetricItems(metrics)) {
      const li = document.createElement("li");
      li.textContent = text;
      metricsListEl.appendChild(li);
    }
  }

  function renderProgressDetail(phase, percent, metrics) {
    progressDetailEl.textContent = buildProgressDetail(phase, percent, metrics);
  }

  function basenameForPath(path) {
    if (typeof path !== "string") return "";
    return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
  }

  function renderResultFiles(files) {
    resultFilesEl.innerHTML = "";
    for (const path of files) {
      const basename = basenameForPath(path) || path;

      const li = document.createElement("li");
      const row = document.createElement("div");
      row.className = "file-row";

      const link = document.createElement("button");
      link.type = "button";
      link.className = "link-button";
      link.title = path;
      const text = document.createElement("code");
      text.textContent = basename;
      const mark = document.createElement("span");
      mark.className = "link-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "➚";
      link.append(text, mark);
      link.addEventListener("click", async () => {
        await openPath(path);
      });

      row.append(link);
      li.appendChild(row);
      resultFilesEl.appendChild(li);
    }
  }

  return {
    reset,
    appendLog,
    renderMetrics,
    renderProgressDetail,
    renderResultFiles,
  };
}
