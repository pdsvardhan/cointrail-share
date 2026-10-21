import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";

export async function GET() {
  try {
    const data = await ingestFetch("/cards/summary");
    return NextResponse.json(data);
  } catch (e: any) {
    console.error("Cards fetch failed:", e);
    // 502, not 200 with an empty list: the page said "No cards found" during an Ingest outage
    // and never offered Retry (iteration 203 E2 M11).
    return NextResponse.json({ cards: [], error: e.message }, { status: 502 });
  }
}
