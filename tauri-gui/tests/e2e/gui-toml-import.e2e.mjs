// Characterization of applyImportedDbToml against the real payload contract.
//
// import_db_toml returns ImportedDbTomlConfig (src-tauri/src/config.rs), which is
// Serialize-only over camelCase structs with #[serde(default)]. That fixes which
// shapes can actually arrive:
//   always a string  : sourcePath, source, email, apiKey, ncbiOptions.db/rettype/
//                      retmode, outputOptions.*HeaderFormat, postPrep.primerFile,
//                      postPrep.msaTreeMode
//   always a number  : ncbiOptions.perQuery (u32)
//   always a bool    : filters.mitochondrion/ddbjEmblGenbank/biomolGenomic,
//                      postPrep.enable, ncbiOptions.useHistory
//   always an array  : markerOptions, primerSet, steps, exportFormats
//   nullable (Option): filters.lengthMin/lengthMax, ncbiOptions.delaySec,
//                      postPrep.sequenceLengthMin/Max, qualityMaxAmbiguousFraction,
//                      qualityRejectInvalidIupac, duplicateSequencePolicy
// import_db_toml also rejects a duplicateSequencePolicy or msaTreeMode outside the
// values taxondbbuilder accepts, so those two arrive only as valid or absent.
// So `?? ""` sits exactly on the Option fields and `|| current` exactly on the
// non-Option ones. Absent keys and wrong types cannot arrive; the `imported.x || {}`
// guards in main.js are defensive only.

import { expect, test } from "@playwright/test";
import { openGuiApp } from "./tauri-stub.mjs";

// A payload with every non-Option field present, as Rust serialises it.
function payload(overrides = {}) {
  return {
    sourcePath: "/srv/job/db.toml",
    markerOptions: [],
    source: "ncbi",
    email: "",
    apiKey: "",
    filters: {
      mitochondrion: false,
      ddbjEmblGenbank: false,
      biomolGenomic: false,
      lengthMin: null,
      lengthMax: null,
    },
    postPrep: {
      enable: true,
      msaTreeMode: "disabled",
      primerFile: "",
      primerSet: [],
      steps: [],
      sequenceLengthMin: null,
      sequenceLengthMax: null,
      qualityMaxAmbiguousFraction: null,
      qualityRejectInvalidIupac: null,
      duplicateSequencePolicy: null,
    },
    ncbiOptions: {
      db: "nuccore",
      rettype: "gb",
      retmode: "text",
      perQuery: 100,
      useHistory: true,
      delaySec: null,
    },
    outputOptions: {
      defaultHeaderFormat: "{acc_id}",
      mifishHeaderFormat: "gb|{acc_id}|{organism}",
      exportFormats: [],
    },
    ...overrides,
  };
}

async function openWithImport(page, imported) {
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/job/db.toml" },
    import_db_toml: { value: imported },
  });
  await page.click("#load-db-toml");
  await expect(page.locator("#loaded-db-toml")).toHaveText("/srv/job/db.toml");
  return errors;
}

const value = (page, selector) => page.locator(selector).inputValue();
const checked = (page, selector) => page.locator(selector).isChecked();

test("a full Rust-shaped payload drives every mapped field", async ({ page }) => {
  const errors = await openWithImport(page, payload());
  expect(await value(page, "#ncbi-db")).toBe("nuccore");
  expect(await value(page, "#ncbi-rettype")).toBe("gb");
  expect(await value(page, "#ncbi-per-query")).toBe("100");
  expect(await value(page, "#output-default-header-format")).toBe("{acc_id}");
  expect(await checked(page, "#post-enable")).toBe(true);
  expect(await checked(page, ".msa-tree-mode[value='disabled']")).toBe(true);
  expect(errors).toEqual([]);
});

test("empty strings in the non-Option fields keep the current form value", async ({ page }) => {
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/job/db.toml" },
    import_db_toml: {
      value: payload({
        ncbiOptions: { db: "", rettype: "", retmode: "", perQuery: 0, useHistory: true, delaySec: null },
        outputOptions: { defaultHeaderFormat: "", mifishHeaderFormat: "", exportFormats: [] },
      }),
    },
  });
  const before = {
    db: await value(page, "#ncbi-db"),
    rettype: await value(page, "#ncbi-rettype"),
    perQuery: await value(page, "#ncbi-per-query"),
    header: await value(page, "#output-default-header-format"),
  };
  await page.click("#load-db-toml");
  await expect(page.locator("#loaded-db-toml")).toHaveText("/srv/job/db.toml");
  expect(await value(page, "#ncbi-db")).toBe(before.db);
  expect(await value(page, "#ncbi-rettype")).toBe(before.rettype);
  // perQuery is u32, so 0 is the only falsy value it can hold: the GUI reads it as
  // "unspecified" and keeps the form value.
  expect(await value(page, "#ncbi-per-query")).toBe(before.perQuery);
  expect(await value(page, "#output-default-header-format")).toBe(before.header);
  expect(errors).toEqual([]);
});

test("Option fields clear the form because absent means no limit", async ({ page }) => {
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/job/db.toml" },
    import_db_toml: { value: payload() },
  });
  await page.evaluate(() => {
    document.querySelector("#f-length-min").value = "1000";
    document.querySelector("#post-length-min").value = "100";
    document.querySelector("#quality-max-ambiguous-fraction").value = "0.05";
    document.querySelector("#ncbi-delay-sec").value = "2";
  });
  await page.click("#load-db-toml");
  await expect(page.locator("#loaded-db-toml")).toHaveText("/srv/job/db.toml");
  expect(await value(page, "#f-length-min")).toBe("");
  expect(await value(page, "#post-length-min")).toBe("");
  expect(await value(page, "#quality-max-ambiguous-fraction")).toBe("");
  expect(await value(page, "#ncbi-delay-sec")).toBe("");
  expect(errors).toEqual([]);
});

test("qualityRejectInvalidIupac null means the backend default", async ({ page }) => {
  // taxondbbuilder defaults the guard to on, so null must not uncheck it.
  const errors = await openWithImport(page, payload());
  expect(await checked(page, "#quality-reject-invalid-iupac")).toBe(true);
  expect(errors).toEqual([]);
});

test("qualityRejectInvalidIupac false unchecks the guard", async ({ page }) => {
  const errors = await openWithImport(
    page,
    payload({ postPrep: { ...payload().postPrep, qualityRejectInvalidIupac: false } }),
  );
  expect(await checked(page, "#quality-reject-invalid-iupac")).toBe(false);
  expect(errors).toEqual([]);
});

test("duplicateSequencePolicy null falls back to keep", async ({ page }) => {
  const errors = await openWithImport(page, payload());
  expect(await value(page, "#duplicate-sequence-policy")).toBe("keep");
  expect(errors).toEqual([]);
});

test("an invalid duplicateSequencePolicy is rejected at import, not blanked", async ({ page }) => {
  // import_db_toml now mirrors taxondbbuilder/config.py and rejects the value, so a
  // policy the select cannot show never reaches the form.
  const errors = await openGuiApp(page, {
    choose_db_toml_file: { value: "/srv/job/db.toml" },
    import_db_toml: {
      reject:
        "post_prep.duplicate_sequence_policy must be one of: keep, representative, exclude_conflicts",
    },
  });
  await page.click("#load-db-toml");
  await expect(page.locator("#log-view")).toContainText(
    "duplicate_sequence_policy must be one of: keep, representative, exclude_conflicts",
  );
  expect(await value(page, "#duplicate-sequence-policy")).toBe("keep");
  expect(await page.locator("#loaded-db-toml").textContent()).toBe("No file loaded");
  expect(errors).toEqual([]);
});

test("an msaTreeMode the radio group cannot show falls back to disabled", async ({ page }) => {
  // Defensive only: import_db_toml rejects a mode outside disabled / combined /
  // per_taxid, so this path needs a hand-built payload to reach.
  const errors = await openWithImport(
    page,
    payload({ postPrep: { ...payload().postPrep, msaTreeMode: "per_marker" } }),
  );
  expect(errors).toEqual([]);
  expect(await checked(page, ".msa-tree-mode[value='disabled']")).toBe(true);
  expect(await page.locator(".msa-tree-mode:checked").count()).toBe(1);
});

test("markerOptions replaces the select and filters the current selection", async ({ page }) => {
  const errors = await openGuiApp(page, {
    load_gui_config: { value: { marker: "12s" } },
    choose_db_toml_file: { value: "/srv/job/db.toml" },
    import_db_toml: { value: payload({ markerOptions: ["coi", "coii"] }) },
  });
  const optionsBefore = await page.locator("#marker option").count();
  expect(optionsBefore).toBeGreaterThan(2);
  await page.click("#load-db-toml");
  await expect(page.locator("#loaded-db-toml")).toHaveText("/srv/job/db.toml");
  expect(await page.locator("#marker option").count()).toBe(2);
  // The seeded 12s marker is dropped because it is no longer a valid option.
  expect(await page.locator("#marker-list .marker-pill").count()).toBe(0);
  expect(errors).toEqual([]);
});

test("exportFormats rewrites the mifish header format it selects", async ({ page }) => {
  const errors = await openWithImport(
    page,
    payload({
      outputOptions: {
        defaultHeaderFormat: "{acc_id}",
        mifishHeaderFormat: "custom|{acc_id}",
        exportFormats: ["dada2_species"],
      },
    }),
  );
  expect(errors).toEqual([]);
  expect(await checked(page, ".output-export-format[value='dada2_species']")).toBe(true);
  // The selection wins over the header format that came in the same payload.
  expect(await value(page, "#output-mifish-header-format")).toBe("{acc_id} {organism_raw}");
});
