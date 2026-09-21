import assert from "node:assert/strict";
import test from "node:test";
import { parseTaxids } from "../src/lib/token-list.js";

test("TaxID accepts digit IDs and rejects the entire mixed input", () => {
  assert.deepEqual(parseTaxids("30991, 1756094; 7777\n9606"), ["30991", "1756094", "7777", "9606"]);
  assert.deepEqual(parseTaxids(""), []);
  for (const invalid of ["Silurus", "ナマズ", "１２３", "1e3", "1.5", "-1", "12abc"]) {
    assert.throws(() => parseTaxids(`30991,${invalid}`), /半角数字/);
  }
});
