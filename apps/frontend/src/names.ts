/** Scenery folder names: what the backend accepts (PACKAGE_NAME_PATTERN). */
export const PACKAGE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$/;

export function validPackageName(name: string): boolean {
  return PACKAGE_NAME_PATTERN.test(name.trim());
}

/** Folder names allow plain letters only: "Igazságügyi palota" -> "Igazsagugyi palota". */
export function folderName(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/ß/g, "ss")
    .replace(/[^A-Za-z0-9 _-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 60)
    .trim();
}
