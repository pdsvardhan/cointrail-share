import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const date = searchParams.get("date");
    if (!date) {
      return NextResponse.json({ error: "date parameter required" }, { status: 400 });
    }
    const data = await ingestFetch(`/analytics/day-transactions?date=${date}`);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Day transactions error:", error);
    return NextResponse.json({ error: "Failed to fetch day transactions" }, { status: 500 });
  }
}
