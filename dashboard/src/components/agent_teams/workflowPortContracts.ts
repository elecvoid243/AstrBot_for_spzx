type Schema = Record<string, any>;
const jsonTypes = ["array", "boolean", "integer", "null", "number", "object", "string"];

/** Collect simultaneous inline constraints. Conditional branches need an actual value. */
function fragments(schema: unknown): Schema[] {
  const pending = [schema];
  const result: Schema[] = [];
  for (const item of pending) {
    if (item === false) result.push({ enum: [] });
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const fragment = item as Schema;
    result.push(fragment);
    if (Array.isArray(fragment.allOf)) pending.push(...fragment.allOf);
  }
  return result;
}

/** Resolve declared port types, including intersections of allOf branches. */
export function inlinePortTypes(schema: unknown): string[] {
  let types: string[] | undefined;
  for (const { type } of fragments(schema)) {
    const declared = (Array.isArray(type) ? type : [type]).filter(
      (kind): kind is string => typeof kind === "string" && jsonTypes.includes(kind),
    );
    if (!declared.length) continue;
    if (declared.includes("number") && !declared.includes("integer")) declared.push("integer");
    types = types ? types.filter((kind) => declared.includes(kind)) : declared;
  }
  const resolved = types ?? [];
  return resolved.includes("number") ? resolved.filter((kind) => kind !== "integer") : resolved;
}

/** Reject disjoint declared values, required object fields and homogeneous array items. */
export function incompatiblePortShapes(source: unknown, target: unknown): boolean {
  if (source === false || target === false) return true;
  const shapes = [fragments(source), fragments(target)];
  const combined = shapes.flat();
  const domains: Set<string>[] = [];
  for (const schema of combined) {
    const choices = [];
    if (Array.isArray(schema.enum)) choices.push(schema.enum);
    if ("const" in schema) choices.push([schema.const]);
    for (const values of choices) {
      domains.push(new Set(values.map((value: unknown) => JSON.stringify(value, (_, item) =>
        item && typeof item === "object" && !Array.isArray(item)
          ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]])) : item))));
    }
  }

  let common = jsonTypes;
  for (const { type } of combined) {
    if (!type) continue;
    const types = Array.isArray(type) ? type : [type];
    common = common.filter((kind) => types.includes(kind) || (kind === "integer" && types.includes("number")));
  }
  if (!common.length) return true;
  if (domains.length && ![...domains[0]].some((encoded) => {
    const value = JSON.parse(encoded);
    const kind = value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
    return (common.includes(kind) || (kind === "number" && Number.isInteger(value) && common.includes("integer")))
      && domains.every((set) => set.has(encoded));
  })) return true;
  if (common.length === 1 && common[0] === "object") {
    const names = new Set<string>(combined.flatMap((schema) => schema.required ?? []));
    for (const name of names) {
      const [left, right] = shapes.map((parts) => parts.flatMap((schema) => {
        if (Object.prototype.hasOwnProperty.call(schema.properties ?? {}, name)) return [schema.properties[name]];
        return "additionalProperties" in schema && !Object.keys(schema.patternProperties ?? {}).length
          ? [schema.additionalProperties] : [];
      }));
      if (left.includes(false) || right.includes(false) || incompatiblePortShapes({allOf: left}, {allOf: right})) return true;
    }
  }
  if (common.length === 1 && common[0] === "array" && combined.every((s) => !s.prefixItems?.length && s.maxItems !== 0)) {
    const [left, right] = shapes.map((parts) => parts.filter((s) => "items" in s).map((s) => s.items));
    if (left.length && right.length && !left.includes(false) && !right.includes(false))
      return incompatiblePortShapes({allOf: left}, {allOf: right});
  }
  return false;
}
