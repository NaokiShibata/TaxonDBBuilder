import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMetricItems,
  buildProgressDetail,
} from "../src/lib/monitor-view.js";

test("metric items are empty without metrics", () => {
  assert.deepEqual(buildMetricItems(undefined), []);
  assert.deepEqual(buildMetricItems({}), []);
});

test("per-taxid query and fetch lines are sorted and clamped", () => {
  assert.deepEqual(
    buildMetricItems({
      queryCountByTaxid: { 2: 5, 10: 3 },
      fetchCountByTaxid: { 2: 9, 10: 1 },
    }),
    [
      "query count taxid=10: 3",
      "fetch progress taxid=10: 1/3",
      "query count taxid=2: 5",
      "fetch progress taxid=2: 5/5",
    ],
  );
});

test("a taxid with only a fetch count has no total", () => {
  assert.deepEqual(buildMetricItems({ fetchCountByTaxid: { 40: 7 } }), [
    "fetch progress taxid=40: 7",
  ]);
});

test("bold taxon lines join whichever counters are present", () => {
  assert.deepEqual(
    buildMetricItems({
      boldSpecimenCountByTaxon: { Mantis: 12 },
      boldDownloadedByTaxon: { Mantis: 4, "Aeshna": 2 },
      boldMatchedByTaxon: { Mantis: 0 },
    }),
    [
      "bold taxon=Aeshna: downloaded=2",
      "bold taxon=Mantis: specimens=12 downloaded=4 matched=0",
    ],
  );
});

test("a bold taxon with no counters contributes no line", () => {
  assert.deepEqual(
    buildMetricItems({
      boldSpecimenCountByTaxon: {},
      boldDownloadedByTaxon: {},
      boldMatchedByTaxon: {},
    }),
    [],
  );
});

test("scalar counters appear in pipeline order and keep zero", () => {
  assert.deepEqual(
    buildMetricItems({
      matchedRecords: 0,
      keptRecordsBeforePostPrep: 8,
      primerTrimRemoved: 2,
      lengthFilterRemoved: 1,
      duplicateGroups: 3,
      crossOrganismGroups: 4,
    }),
    [
      "matched records: 0",
      "kept before post_prep: 8",
      "primer_trim removed: 2",
      "length_filter removed: 1",
      "duplicate groups: 3",
      "cross_organism_groups: 4",
    ],
  );
});

test("msa_tree reports taxa placeholder and only existing paths", () => {
  assert.deepEqual(buildMetricItems({ msaTreeStatus: "ok" }), [
    "msa_tree: status=ok taxa=-",
  ]);
  assert.deepEqual(
    buildMetricItems({
      msaTreeStatus: "written",
      msaTreeTaxa: 6,
      msaTreeMsaPath: "/tmp/a.msa",
      msaTreeTreePath: "/tmp/a.tree",
    }),
    [
      "msa_tree: status=written taxa=6",
      "msa file: /tmp/a.msa",
      "tree file: /tmp/a.tree",
    ],
  );
});

test("progress detail summarises fetch and bold totals", () => {
  assert.equal(
    buildProgressDetail("Fetch/Parse", 40, {
      queryCountByTaxid: { 1: 10, 2: 10 },
      fetchCountByTaxid: { 1: 12, 2: 4 },
      boldDownloadedByTaxon: { A: 3 },
      boldMatchedByTaxon: { A: 2 },
      boldSpecimenCountByTaxon: { A: 9 },
    }),
    "fetch 14/20 (70.0%) | bold matched 2/3 specimens 9 taxa 1",
  );
});

test("progress detail falls back to the percent or a dash", () => {
  assert.equal(buildProgressDetail("Post-prep", 25, {}), "total 25.0%");
  assert.equal(buildProgressDetail("Post-prep", null, {}), "-");
  assert.equal(buildProgressDetail("Fetch/Parse", 25, {}), "25.0%");
});
