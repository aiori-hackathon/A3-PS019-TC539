import { describe, expect, it } from "vitest";
import { checkPath, isSqlite, stripCommonRoot } from "./upload";

const SUFFIXES = [".csv", ".db", ".db3", ".sqlite", ".sqlite3"];

describe("checkPath mirrors the server rules", () => {
  it.each(["a.csv", "dir/sub/a.CSV", "data/app.sqlite3", "a b/c d.csv"])("accepts %s", (p) => expect(checkPath(p, SUFFIXES)).toBeNull());
  it.each(["", "/a.csv", "../a.csv", "a/../b.csv", "a//b.csv", "a\\b.csv", "C:/a.csv", "CON.csv", "x/nul.csv", "a:b.csv", "q?.csv", "dot./x.csv", "dir /x.csv", "a\u0000.csv", "notes.txt", "run.exe"])("rejects %j", (p) =>
    expect(checkPath(p, SUFFIXES)).not.toBeNull());
  it("detects sqlite names", () => {
    expect(isSqlite("x/app.DB")).toBe(true);
    expect(isSqlite("x.csv")).toBe(false);
  });
});

describe("stripCommonRoot", () => {
  const f = (path: string) => ({ file: new File(["x"], "x.csv"), path });
  it("drops a single shared top-level folder", () => {
    expect(stripCommonRoot([f("data/a.csv"), f("data/b/c.csv")]).map((i) => i.path)).toEqual(["a.csv", "b/c.csv"]);
  });
  it("keeps paths when there is no single shared root", () => {
    expect(stripCommonRoot([f("a.csv"), f("data/b.csv")]).map((i) => i.path)).toEqual(["a.csv", "data/b.csv"]);
    expect(stripCommonRoot([f("one/a.csv"), f("two/b.csv")]).map((i) => i.path)).toEqual(["one/a.csv", "two/b.csv"]);
  });
});
