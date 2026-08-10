import assert from "node:assert/strict";
import test from "node:test";

import { formatTreeUnavailableMessage, parseFasta } from "../src/tree-view.js";

test("parseFasta reads aligned records by FASTA ID", () => {
  assert.deepEqual(
    [...parseFasta(">leaf-1 description\nAC-G\n>leaf-2\nACTG\n").entries()],
    [["leaf-1", "AC-G"], ["leaf-2", "ACTG"]],
  );
});

test("parseFasta rejects sequences with different lengths", () => {
  assert.throws(() => parseFasta(">a\nAC\n>b\nA\n"), /not aligned/);
});

test("formatTreeUnavailableMessage explains the taxa limit", () => {
  assert.equal(
    formatTreeUnavailableMessage("skipped_too_many_taxa", 501),
    "対象配列数が上限を超えたため、系統樹の生成をスキップしました（対象配列数: 501）。",
  );
});
