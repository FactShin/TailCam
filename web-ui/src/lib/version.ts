// Tiny semver-ish compare: "1.8.0" >= "1.8"; ignores pre-release/build tags.
// Unknown/empty versions never satisfy a minimum.
export function versionAtLeast(a: string | null | undefined, b: string): boolean {
  if (!a) return false;
  const parse = (v: string) =>
    v.trim().replace(/^v/i, "").split(/[-+]/)[0].split(".").map((p) => parseInt(p, 10) || 0);
  const pa = parse(a);
  const pb = parse(b);
  const n = Math.max(pa.length, pb.length);
  for (let i = 0; i < n; i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x !== y) return x > y;
  }
  return true;
}
