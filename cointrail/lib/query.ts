/**
 * `a=1&b=2` built from the given keys of a request's query, in the order given, skipping the
 * absent ones (values URL-encoded). The API routes forward exactly these to Ingest.
 */
export function pickQuery(params: URLSearchParams, keys: string[]): string {
  return keys
    .map((k) => [k, params.get(k)] as const)
    .filter(([, v]) => v !== null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(v!)}`)
    .join("&");
}

/** True when the request names a date range; `from` and `to` must come together. */
export function rangeParams(params: URLSearchParams): { from: string; to: string } | null | "half" {
  const from = params.get("from");
  const to = params.get("to");
  if (!from && !to) return null;
  if (!from || !to) return "half";
  return { from, to };
}
