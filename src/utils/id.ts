export function generateNumericIdFromUuid(): number {
  const uuid = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');

  const hex = uuid.replace(/-/g, '').slice(0, 13);
  const n = Number.parseInt(hex, 16);
  if (!Number.isFinite(n) || n <= 0) return Date.now();
  return n;
}

