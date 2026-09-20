import type { FileEntry } from "./host-files";
export function displayedFiles(entries: FileEntry[], query: string, showHidden: boolean, descending: boolean): FileEntry[] {
  const needle = query.normalize("NFC").toLocaleLowerCase();
  return entries.filter(entry => (showHidden || !entry.name.startsWith(".")) && entry.name.normalize("NFC").toLocaleLowerCase().includes(needle))
    .sort((a, b) => Number(b.type === "directory") - Number(a.type === "directory") || (descending ? -1 : 1) * a.name.localeCompare(b.name, "ko", { numeric: true }));
}
export function fileKind(entry: FileEntry): string {
  if (entry.type === "directory") return "폴더";
  if (entry.type === "symlink") return "바로가기";
  if (/\.(md|markdown)$/i.test(entry.name)) return "Markdown";
  if (entry.mime === "application/pdf") return "PDF";
  if (entry.mime.startsWith("image/")) return "이미지";
  return entry.name.includes(".") ? entry.name.split(".").pop()!.toUpperCase() : "파일";
}
