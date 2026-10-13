// Characterization tests for the form/config half of src/main.js, run against
// the real page (vite dev) with a fake Tauri IPC. These pin behaviour BEFORE
// the 3.3.4 main.js split, including the asymmetries in applyImportedDbToml
// (absent key -> "" vs absent key -> keep current value).

import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

async function value(page, selector) {
  return page.locator(selector).inputValue();
}

async function checked(page, selector) {
  return page.locator(selector).isChecked();
}

test("MiFish-Unity is the only default-checked candidate and prefills primer_set", async ({ page }) => {
  const errors = await openGuiApp(page);
  expect(await checked(page, ".primer-candidate[value='mifish_unity']")).toBe(true);
  expect(await page.locator(".primer-candidate:checked").count()).toBe(1);
  expect(await value(page, "#primer-set")).toBe("mifish_unity");
  expect(errors).toEqual([]);
});

test("primer_set follows the checked candidates", async ({ page }) => {
  const errors = await openGuiApp(page);
  await page.check(".primer-candidate[value='mideca']");
  await page.check(".primer-candidate[value='uceta']");
  // Order follows DOM order of the candidate checkboxes (mifish_unity is last).
  expect(await value(page, "#primer-set")).toBe("mideca,uceta,mifish_unity");

  await page.uncheck(".primer-candidate[value='mifish_unity']");
  expect(await value(page, "#primer-set")).toBe("mideca,uceta");

  await page.uncheck(".primer-candidate[value='mideca']");
  await page.uncheck(".primer-candidate[value='uceta']");
  expect(await value(page, "#primer-set")).toBe("");
  expect(errors).toEqual([]);
});

test("Reset restores the default candidate selection and primer_set", async ({ page }) => {
  const errors = await openGuiApp(page);
  await page.uncheck(".primer-candidate[value='mifish_unity']");
  await page.check(".primer-candidate[value='amph_16s']");
  expect(await value(page, "#primer-set")).toBe("amph_16s");

  await page.click("#reset");
  expect(await checked(page, ".primer-candidate[value='mifish_unity']")).toBe(true);
  expect(await page.locator(".primer-candidate:checked").count()).toBe(1);
  expect(await value(page, "#primer-set")).toBe("mifish_unity");
  expect(errors).toEqual([]);
});

test("loadSavedConfig applies the saved GUI config at startup", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: {
      value: {
        outputRoot: "/srv/out",
        outputPrefix: "MiFish-U",
        email: "survey@example.com",
        saveApiKey: true,
        apiKey: "secret-key",
        marker: "12s",
        source: "both",
        ncbiDb: "nuccore",
        ncbiRettype: "text",
        ncbiRetmode: "fasta",
        ncbiPerQuery: 200,
        ncbiUseHistory: false,
        ncbiDelaySec: 1.5,
        outputDefaultHeaderFormat: "gb|{acc_id}",
        outputMifishHeaderFormat: "gb|{acc_id}|{head}",
      },
    },
  });
  expect(await value(page, "#output-root")).toBe("/srv/out");
  expect(await value(page, "#output-prefix")).toBe("MiFish-U");
  expect(await value(page, "#email")).toBe("survey@example.com");
  expect(await checked(page, "#save-api-key")).toBe(true);
  expect(await value(page, "#api-key")).toBe("secret-key");
  expect(await value(page, "#source")).toBe("both");
  expect(await value(page, "#ncbi-db")).toBe("nuccore");
  expect(await value(page, "#ncbi-per-query")).toBe("200");
  expect(await checked(page, "#ncbi-use-history")).toBe(false);
  expect(await value(page, "#ncbi-delay-sec")).toBe("1.5");
  expect(await value(page, "#output-default-header-format")).toBe("gb|{acc_id}");
  // Startup always forces the export format selection to mifish_pipeline.
  expect(await checked(page, ".output-header-format-choice[value='mifish_pipeline']")).toBe(true);
  expect(errors).toEqual([]);
});

test("loadSavedConfig failure keeps defaults and logs a warning", async ({ page }) => {
  const errors = await openGuiApp(page, { load_gui_config: { reject: "no saved config" } });
  expect(await value(page, "#output-root")).toBe("");
  expect(await value(page, "#email")).toBe("");
  expect(await value(page, "#primer-set")).toBe("mifish_unity");
  await expect(page.locator("#log-view")).toContainText("[warn] load config failed");
  expect(errors).toEqual([]);
});

test("importing db.toml fills every mapped field", async ({ page }) => {
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/job/db.toml" },
    import_db_toml: {
      value: {
        sourcePath: "/srv/job/db.toml",
        markerOptions: ["12s", "16s", "coi"],
        source: "bold",
        email: "from-toml@example.com",
        apiKey: "toml-key",
        filters: {
          mitochondrion: true,
          ddbjEmblGenbank: true,
          biomolGenomic: false,
          lengthMin: 1000,
          lengthMax: 1700,
        },
        postPrep: {
          enable: false,
          msaTreeMode: "per_taxid",
          primerFile: "/srv/primers.toml",
          primerSet: ["mideca", "uceta"],
          sequenceLengthMin: 100,
          sequenceLengthMax: 400,
          qualityMaxAmbiguousFraction: 0.05,
          qualityRejectInvalidIupac: false,
          duplicateSequencePolicy: "representative",
          steps: ["primer_trim", "length_filter"],
        },
        ncbiOptions: {
          db: "nuccore",
          rettype: "gbwithparts",
          retmode: "text",
          perQuery: 300,
          useHistory: false,
          delaySec: 2,
        },
        outputOptions: {
          defaultHeaderFormat: "gb|{acc_id}",
          mifishHeaderFormat: "gb|{acc_id}|{organism}",
          exportFormats: ["qiime2"],
        },
      },
    },
  });
  await page.click("#load-db-toml");
  await expect(page.locator("#loaded-db-toml")).toHaveText("/srv/job/db.toml");
  expect(await page.locator("#marker option").count()).toBe(3);
  expect(await value(page, "#source")).toBe("bold");
  expect(await value(page, "#email")).toBe("from-toml@example.com");
  expect(await value(page, "#api-key")).toBe("toml-key");
  expect(await checked(page, "#save-api-key")).toBe(true);
  expect(await checked(page, "#f-mito")).toBe(true);
  expect(await checked(page, "#f-ddbj")).toBe(true);
  expect(await checked(page, "#f-biomol")).toBe(false);
  expect(await value(page, "#f-length-min")).toBe("1000");
  expect(await value(page, "#f-length-max")).toBe("1700");
  expect(await checked(page, "#post-enable")).toBe(false);
  expect(await checked(page, ".msa-tree-mode[value='per_taxid']")).toBe(true);
  expect(await value(page, "#primer-file")).toBe("/srv/primers.toml");
  expect(await value(page, "#primer-set")).toBe("mideca,uceta");
  expect(await checked(page, ".primer-candidate[value='mideca']")).toBe(true);
  expect(await value(page, "#post-length-min")).toBe("100");
  expect(await value(page, "#post-length-max")).toBe("400");
  expect(await value(page, "#quality-max-ambiguous-fraction")).toBe("0.05");
  expect(await checked(page, "#quality-reject-invalid-iupac")).toBe(false);
  expect(await value(page, "#duplicate-sequence-policy")).toBe("representative");
  expect(await checked(page, ".post-step[value='length_filter']")).toBe(true);
  expect(await checked(page, ".post-step[value='quality_filter']")).toBe(false);
  expect(await value(page, "#ncbi-db")).toBe("nuccore");
  expect(await value(page, "#ncbi-rettype")).toBe("gbwithparts");
  expect(await value(page, "#ncbi-per-query")).toBe("300");
  expect(await checked(page, "#ncbi-use-history")).toBe(false);
  expect(await value(page, "#ncbi-delay-sec")).toBe("2");
  expect(await value(page, "#output-default-header-format")).toBe("gb|{acc_id}");
  expect(await checked(page, ".output-export-format[value='qiime2']")).toBe(true);
  // The export-format selection rewrites the mifish header format field.
  expect(await value(page, "#output-mifish-header-format")).toBe("{acc_id}");
  expect(errors).toEqual([]);
});

test("empty db.toml payload: defaults, blanks, and kept values, exactly as coded", async ({ page }) => {
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/empty/db.toml" },
    import_db_toml: { value: { sourcePath: "/srv/empty/db.toml" } },
  });
  // The NCBI fields live inside a collapsed <details>, so set values directly.
  await page.evaluate(() => {
    document.querySelector("#ncbi-db").value = "kept-db";
    document.querySelector("#ncbi-rettype").value = "gbwithparts";
    document.querySelector("#email").value = "keep@me.example";
  });
  await page.click("#load-db-toml");
  await expect(page.locator("#loaded-db-toml")).toHaveText("/srv/empty/db.toml");

  // `|| current` keeps the form value; `?? ""` blanks it; source falls back to ncbi.
  expect(await value(page, "#source")).toBe("ncbi");
  expect(await value(page, "#ncbi-db")).toBe("kept-db");
  expect(await value(page, "#ncbi-rettype")).toBe("gbwithparts");
  expect(await value(page, "#ncbi-delay-sec")).toBe("");
  expect(await value(page, "#output-default-header-format")).toBe(
    "{acc_id}|{organism}|{marker}|{label}|{type}|{loc}|{strand}",
  );
  expect(await value(page, "#email")).toBe("");
  expect(await value(page, "#f-length-min")).toBe("");
  // Booleans: filters and post-enable are Boolean(undefined) -> false, the
  // IUPAC guard and ncbi useHistory are `!== false` -> true, duplicate policy
  // falls back to "keep", and an unknown msaTreeMode becomes "disabled".
  expect(await checked(page, "#f-mito")).toBe(false);
  expect(await checked(page, "#post-enable")).toBe(false);
  expect(await checked(page, "#quality-reject-invalid-iupac")).toBe(true);
  expect(await checked(page, "#ncbi-use-history")).toBe(true);
  expect(await value(page, "#duplicate-sequence-policy")).toBe("keep");
  expect(await checked(page, ".msa-tree-mode[value='disabled']")).toBe(true);
  // Lists: primer_set clears every candidate and every post-prep step.
  expect(await value(page, "#primer-set")).toBe("");
  expect(await page.locator(".primer-candidate:checked").count()).toBe(0);
  expect(await checked(page, ".post-step[value='primer_trim']")).toBe(false);
  // With no exportFormats, the mifish_pipeline choice survives because the
  // header format still matches the mifish pipeline template.
  expect(await checked(page, ".output-header-format-choice[value='mifish_pipeline']")).toBe(true);
  expect(errors).toEqual([]);
});

test("db.toml import failure logs an error and leaves the form and path alone", async ({ page }) => {
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/missing/db.toml" },
    import_db_toml: { reject: "db.toml not found" },
  });
  await page.fill("#email", "untouched@example.com");
  await page.click("#load-db-toml");
  await expect(page.locator("#log-view")).toContainText("[error] db.toml import failed");
  expect(await page.locator("#loaded-db-toml").textContent()).toBe("No file loaded");
  expect(await value(page, "#email")).toBe("untouched@example.com");
  expect(await value(page, "#primer-set")).toBe("mifish_unity");
  expect(errors).toEqual([]);
});

test("run-events drive the monitor and unlock the results view", async ({ page }) => {
  const errors = await openGuiApp(page, {
    // The Rust side answers with a default GuiConfig on a fresh install, and
    // that empty config is what seeds the default marker selection.
    load_gui_config: { value: {} },
    start_run: {
      value: { outputPath: "/srv/out/mifish", jobDir: "/srv/job1", logPath: "/srv/job1/run.log" },
    },
  });
  await page.fill("#taxid-input", "1234");
  await page.click("#add-taxid");
  await page.fill("#output-root", "/srv/out");
  await page.fill("#email", "survey@example.com");
  await page.click("#run");
  await expect(page.locator("#log-view")).toContainText("job dir: /srv/job1");
  expect(await page.locator("#view-monitor.active").count()).toBe(1);

  await page.evaluate(() => {
    const emit = (payload) => window.__guiEmit__("run-event", payload);
    emit({ eventType: "log", line: "hello from sidecar" });
    emit({ eventType: "status", status: "Running" });
    emit({ eventType: "progress", phase: "post_prep", percent: 42 });
    emit({ eventType: "error", message: "boom" });
    emit({ eventType: "result", jobDir: "/srv/job1", files: ["/srv/job1/out.fasta"] });
    emit("not-an-object");
    emit({ eventType: "unknown" });
  });
  await expect(page.locator("#log-view")).toContainText("hello from sidecar");
  await expect(page.locator("#log-view")).toContainText("[error] boom");
  expect(await page.locator("#phase").textContent()).toBe("post_prep");
  // #progress is a <progress> element, so read the DOM property.
  expect(await page.evaluate(() => document.querySelector("#progress").value)).toBe(42);
  await expect(page.locator("#progress-detail")).toContainText("42");
  // The error event lands after the status event, so "Failed" wins.
  expect(await page.locator("#status").textContent()).toBe("Failed");
  await expect(page.locator("#result-job-dir-path")).toHaveText("/srv/job1");
  await expect(page.locator("#result-files")).toContainText("out.fasta");
  await expect(page.locator("#tab-results")).toBeEnabled();
  expect(await page.locator("#view-results.active").count()).toBe(1);
  expect(errors).toEqual([]);
});
