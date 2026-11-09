import test from "node:test";
import assert from "node:assert/strict";
import { parseFloatOrNull, parseIntOrNull } from "../src/lib/form-utils.js";

test("integer inputs require complete, safely representable integers", () => {
  for (const value of ["12abc", "12.5", "1e2", "0x10", "Infinity", "9007199254740993", "", " ", null, undefined]) {
    assert.equal(parseIntOrNull(value), null, String(value));
  }
  assert.equal(parseIntOrNull(" 12 "), 12);
  assert.equal(parseIntOrNull("-3"), -3);
  assert.equal(parseIntOrNull("+3"), 3);
  assert.equal(parseIntOrNull(0), 0);
});

test("decimal inputs accept complete finite decimals and exponents", () => {
  for (const value of ["12abc", "1.2.3", "1e", "0x10", "Infinity", "NaN", "1e309", "", " ", null, undefined]) {
    assert.equal(parseFloatOrNull(value), null, String(value));
  }
  assert.equal(parseFloatOrNull(" 12.5 "), 12.5);
  assert.equal(parseFloatOrNull("-.5"), -0.5);
  assert.equal(parseFloatOrNull("1."), 1);
  assert.equal(parseFloatOrNull("+2e-3"), 0.002);
  assert.equal(parseFloatOrNull(0), 0);
});
