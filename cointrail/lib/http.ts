/**
 * Browser-side JSON fetch that fails loudly (iteration 203 E2 M11).
 *
 * The pages used to call `fetch(...).then(r => r.json())` and render whatever came back, so an
 * Ingest outage (answered as 200 with empty lists, or as a 500 nobody looked at) showed up as
 * "No cards found" or "No recurring expenses detected" instead of an error with a Retry button.
 * `getJson` throws on any non-2xx answer; the pages catch it and show `ErrorState`.
 */

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "HttpError";
  }
}

export async function getJson<T = any>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  let body: any = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail = body && typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
    throw new HttpError(res.status, detail);
  }
  return body as T;
}

/** A short, human message for an error shown on the page. */
export function errorMessage(e: unknown): string {
  if (e instanceof HttpError && e.status >= 500) {
    return "Couldn't reach the data service, so nothing is shown rather than incomplete numbers.";
  }
  if (e instanceof HttpError) return `The request failed (${e.message}).`;
  return "Couldn't load the data. Check your connection.";
}
