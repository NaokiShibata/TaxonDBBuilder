export function parseIntOrNull(value) {
  const v = `${value}`.trim();
  if (!v) return null;
  const parsed = Number.parseInt(v, 10);
  return Number.isNaN(parsed) ? null : parsed;
}

export function parseFloatOrNull(value) {
  const v = `${value}`.trim();
  if (!v) return null;
  const parsed = Number.parseFloat(v);
  return Number.isNaN(parsed) ? null : parsed;
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
