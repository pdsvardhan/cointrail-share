import { ingestFetch } from "@/lib/ingest";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await ingestFetch("/recurring/detect");
    return NextResponse.json(data);
  } catch (e: any) {
    console.error("Failed to fetch recurring detection:", e);
    // 502, not an empty list: the page said "No recurring expenses detected · Need at least 3
    // months of data" during an Ingest outage and its Retry never appeared (iteration 203 E2 M11).
    return NextResponse.json(
      { recurring: [], error: e?.message ?? "Ingest unavailable" },
      { status: 502 }
    );
  }
}
