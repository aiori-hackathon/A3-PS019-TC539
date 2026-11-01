import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, exportUrl, findingsQuery } from "./client";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(body), { status }));
}

describe("api client", () => {
  it("sends the client header only on state-changing requests", async () => {
    const spy = mockFetch(200, {});
    await api.get("/api/health");
    await api.post("/api/scans", { formats: ["json"] });
    await api.del("/api/workspace");
    const [get, post, del] = spy.mock.calls.map((c) => (c[1]!.headers as Record<string, string>));
    expect(get["X-FieldLedger-Client"]).toBeUndefined();
    expect(post["X-FieldLedger-Client"]).toBe("ui");
    expect(del["X-FieldLedger-Client"]).toBe("ui");
  });
  it("turns error bodies into ApiError with the server code", async () => {
    mockFetch(409, { error: { code: "scan_in_progress", message: "a scan is already running" } });
    await expect(api.post("/api/scans")).rejects.toMatchObject({ code: "scan_in_progress", status: 409, message: "a scan is already running" });
  });
  it("reports an unreachable server clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    const err: unknown = await api.get("/api/health").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("network");
  });
  it("builds findings queries with repeated parameters and skips empties", () => {
    const q = findingsQuery({ q: "", class: ["A", "B"], page: 2, source: undefined, metadata_status: [] });
    expect(q).toBe("class=A&class=B&page=2");
    expect(exportUrl("abc", "csv")).toBe("/api/scans/abc/export/csv");
  });
});
