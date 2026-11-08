export function parseIntOrNull(value) {
  const v = String(value ?? "").trim();
  if (!/^[+-]?\d+$/.test(v)) return null;
  const parsed = Number(v);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function parseFloatOrNull(value) {
  const v = String(value ?? "").trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(v)) return null;
  const parsed = Number(v);
  return Number.isFinite(parsed) ? parsed : null;
}

export function readCheckedValues(elements) {
  return elements.filter((el) => el.checked).map((el) => el.value);
}

export function setCheckedValues(elements, values) {
  const wanted = new Set(Array.isArray(values) ? values : []);
  elements.forEach((el) => {
    el.checked = wanted.has(el.value);
  });
}
