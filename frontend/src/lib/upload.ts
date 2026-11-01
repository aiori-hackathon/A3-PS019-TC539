// Browser-side pre-checks for uploads. They give fast feedback only; the server is the authority
// and applies the same rules (src/fieldledger/web/paths.py).

export interface QueuedFile { file: File; path: string }

const RESERVED = new Set(["con", "prn", "aux", "nul", ...Array.from({ length: 9 }, (_, i) => `com${i + 1}`), ...Array.from({ length: 9 }, (_, i) => `lpt${i + 1}`)]);
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

export function checkPath(path: string, suffixes: string[]): string | null {
  if (!path) return "Empty file name.";
  if (path.length > 1024) return "Path is too long.";
  if (path.includes("\\")) return "Use forward slashes in paths.";
  if (path.startsWith("/") || /^[A-Za-z]:/.test(path)) return "Path must be relative.";
  if (CONTROL.test(path) || /[<>:"|?*]/.test(path)) return "Name contains a character that is not allowed.";
  for (const seg of path.split("/")) {
    if (seg === "" || seg === "." || seg === "..") return "Path contains an empty or relative segment.";
    if (seg.length > 255) return "A path segment is too long.";
    if (/[. ]$/.test(seg)) return "A path segment may not end with a dot or space.";
    if (RESERVED.has(seg.split(".")[0].trim().toLowerCase())) return "Name uses a reserved device name.";
  }
  const lower = path.toLowerCase();
  if (!suffixes.some((s) => lower.endsWith(s))) return `Unsupported type. Use ${suffixes.join(", ")}.`;
  return null;
}

export function isSqlite(path: string): boolean {
  return /\.(db|db3|sqlite|sqlite3)$/i.test(path);
}

/** Relative path of a picked file: the folder structure if the browser provides it. */
export function relativePathOf(file: File): string {
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return rel && rel.length > 0 ? rel : file.name;
}

/** Drop the common top-level folder name chosen by "Choose folder", so keys start inside it. */
export function stripCommonRoot(items: QueuedFile[]): QueuedFile[] {
  if (items.length === 0) return items;
  const roots = new Set(items.map((i) => i.path.split("/")[0]));
  if (roots.size !== 1 || items.some((i) => !i.path.includes("/"))) return items;
  return items.map((i) => ({ ...i, path: i.path.split("/").slice(1).join("/") }));
}

interface FsEntry {
  isFile: boolean; isDirectory: boolean; name: string; fullPath: string;
  file?: (ok: (f: File) => void, err: (e: unknown) => void) => void;
  createReader?: () => { readEntries: (ok: (e: FsEntry[]) => void, err: (e: unknown) => void) => void };
}

async function walk(entry: FsEntry, out: QueuedFile[]): Promise<void> {
  if (entry.isFile && entry.file) {
    const file = await new Promise<File>((ok, err) => entry.file!(ok, err));
    out.push({ file, path: entry.fullPath.replace(/^\//, "") });
  } else if (entry.isDirectory && entry.createReader) {
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise<FsEntry[]>((ok, err) => reader.readEntries(ok, err));
      if (batch.length === 0) break;
      for (const child of batch) await walk(child, out);
    }
  }
}

/** Files and folders from a drop event, keeping folder structure. */
export async function filesFromDrop(dt: DataTransfer): Promise<QueuedFile[]> {
  const out: QueuedFile[] = [];
  const entries: FsEntry[] = [];
  for (const item of Array.from(dt.items)) {
    const e = (item as DataTransferItem & { webkitGetAsEntry?: () => FsEntry | null }).webkitGetAsEntry?.();
    if (e) entries.push(e);
  }
  if (entries.length === 0) return Array.from(dt.files).map((file) => ({ file, path: file.name }));
  for (const e of entries) await walk(e, out);
  return entries.length === 1 && entries[0].isDirectory ? stripCommonRoot(out) : out;
}
