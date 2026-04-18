export function fixFilenameEncoding(filename: string | undefined): string {
  if (!filename) return '';
  const hasMojiChars = /[\u00C0-\u00FF]{3,}/.test(filename);
  if (!hasMojiChars) return filename;
  try {
    const latin1Buffer = new TextEncoder().encode(filename);
    const utf8String = new TextDecoder('latin1').decode(latin1Buffer);
    if (/[\u4E00-\u9FFF]/.test(utf8String)) {
      return utf8String;
    }
  } catch {}
  return filename;
}

