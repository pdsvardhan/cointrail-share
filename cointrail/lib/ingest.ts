const INGEST_URL = process.env.INGEST_URL || "http://ingest:8600";
const INGEST_API_KEY = process.env.INGEST_API_KEY || "";

export async function ingestFetch(path: string, options?: RequestInit) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options?.headers as Record<string, string>) || {}),
  };
  if (INGEST_API_KEY) {
    headers["X-API-Key"] = INGEST_API_KEY;
  }
  const res = await fetch(`${INGEST_URL}${path}`, {
    ...options,
    headers,
  });
  if (!res.ok) throw new Error(`Ingest API error: ${res.status}`);
  return res.json();
}

/**
 * Like ingestFetch, but an HTTP error is an answer, not an exception: returns Ingest's status and
 * body so a write route can pass a 404 / 400 (and Ingest's own message) through to the page
 * (iteration 203 E3 / E5 / E9). Throws only when Ingest cannot be reached.
 */
export async function ingestRequest(
  path: string,
  options?: RequestInit
): Promise<{ status: number; body: unknown }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options?.headers as Record<string, string>) || {}),
  };
  if (INGEST_API_KEY) {
    headers["X-API-Key"] = INGEST_API_KEY;
  }
  const res = await fetch(`${INGEST_URL}${path}`, { ...options, headers });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { status: res.status, body };
}
