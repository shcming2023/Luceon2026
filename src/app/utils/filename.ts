export function fixFilenameEncoding(filename: string | undefined): string {
  if (!filename) return '';
  const hasMojiChars = /[\u0080-\u00FF]/.test(filename);
  if (!hasMojiChars) return filename;
  try {
    const latin1Bytes = Uint8Array.from(
      Array.from(filename, (char) => char.charCodeAt(0) & 0xff)
    );
    const utf8String = new TextDecoder('utf-8').decode(latin1Bytes);
    if (/[\u4E00-\u9FFF]/.test(utf8String)) {
      return utf8String;
    }
  } catch {}
  return filename;
}
