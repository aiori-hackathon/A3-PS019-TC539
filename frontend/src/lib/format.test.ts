import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, formatRatio, humanize, plural } from "./format";

describe("format", () => {
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(50 * 1024 * 1024)).toBe("50.0 MiB");
  });
  it("never turns an undefined ratio into zero", () => {
    expect(formatRatio(null)).toBe("n/a");
    expect(formatRatio(undefined)).toBe("n/a");
    expect(formatRatio(0)).toBe("0.0%");
    expect(formatRatio(0.9718)).toBe("97.2%");
  });
  it("formats durations, plurals and codes", () => {
    expect(formatDuration(3600)).toBe("60 minutes");
    expect(formatDuration(30)).toBe("30 seconds");
    expect(plural(1, "file")).toBe("1 file");
    expect(plural(2, "entry", "entries")).toBe("2 entries");
    expect(humanize("missing_purpose")).toBe("Missing purpose");
  });
});
