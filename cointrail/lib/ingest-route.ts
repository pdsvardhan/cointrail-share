import { NextResponse } from "next/server";
import { ingestRequest } from "@/lib/ingest";

/**
 * Forward one request to Ingest and answer the page (iteration 203 E3 / E5 / E6 / E9).
 *
 * Same contract as the read routes (E2 M11): Ingest unreachable, or failing with a 5xx, is a 502
 * carrying the error — never an empty answer that reads like "nothing there". An Ingest 4xx (an
 * unknown id, a value it refuses) is passed through with Ingest's own message, so the page can say
 * what went wrong. `emptyOnError` is merged into the 502 body for pages that read a list field.
 */
export async function proxyIngest(
  path: string,
  init?: RequestInit,
  emptyOnError: Record<string, unknown> = {}
): Promise<NextResponse> {
  let answer: { status: number; body: unknown };
  try {
    answer = await ingestRequest(path, init);
  } catch (e) {
    console.error(`Ingest ${init?.method ?? "GET"} ${path} failed:`, e);
    return NextResponse.json(
      { ...emptyOnError, error: e instanceof Error ? e.message : "Ingest unavailable" },
      { status: 502 }
    );
  }
  const { status, body } = answer;
  if (status >= 500) {
    return NextResponse.json(
      { ...emptyOnError, error: detailOf(body) ?? `Ingest error ${status}` },
      { status: 502 }
    );
  }
  if (status >= 400) {
    return NextResponse.json({ error: detailOf(body) ?? `Ingest error ${status}` }, { status });
  }
  return NextResponse.json(body ?? {});
}

function detailOf(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const b = body as { detail?: unknown; error?: unknown };
  if (typeof b.detail === "string") return b.detail;
  if (typeof b.error === "string") return b.error;
  return null;
}

/** A positive integer path id, or null (nothing else is forwarded into an Ingest path). */
export function numericId(id: string): string | null {
  return /^\d{1,12}$/.test(id) ? id : null;
}

export function badRequest(message: string): NextResponse {
  return NextResponse.json({ error: message }, { status: 400 });
}

/** The JSON body of a request, or null when it is not a JSON object. */
export async function jsonBody(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const body = await request.json();
    return body && typeof body === "object" && !Array.isArray(body) ? body : null;
  } catch {
    return null;
  }
}
