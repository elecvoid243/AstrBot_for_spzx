import { vi } from "vitest";
import { draftApi, type Draft } from "@/composables/useAssetDraft";

export function mockDrafts(
  publish: (id: string | null, body: any) => Promise<any>,
) {
  const records = new Map<string, Draft>();
  let next = 0;
  vi.spyOn(draftApi, "list").mockImplementation(async () =>
    Array.from(records.values()),
  );
  vi.spyOn(draftApi, "get").mockImplementation(async (_kind, id) =>
    structuredClone(records.get(id)!),
  );
  vi.spyOn(draftApi, "create").mockImplementation(async (_kind, body, id) => {
    const record = {
      asset_id: id ?? `draft-${++next}`,
      revision: 1,
      body: JSON.parse(JSON.stringify(body)),
      base_version: id ? 1 : null,
    };
    records.set(record.asset_id, record);
    return structuredClone(record);
  });
  vi.spyOn(draftApi, "update").mockImplementation(
    async (_kind, id, body, revision) => {
      const record = records.get(id)!;
      if (record.revision !== revision) throw { response: { status: 409 } };
      Object.assign(record, {
        body: JSON.parse(JSON.stringify(body)),
        revision: revision + 1,
      });
      return structuredClone(record);
    },
  );
  vi.spyOn(draftApi, "publish").mockImplementation(async (_kind, id) => {
    const record = records.get(id)!;
    const { editor: _editor, ...body } = record.body;
    return publish(record.base_version ? id : null, {
      ...body,
      expected_version: record.base_version ?? undefined,
    });
  });
}
