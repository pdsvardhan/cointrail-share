import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";
import { rangeParams } from "@/lib/query";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const range = rangeParams(searchParams);
    if (range === "half") {
      return NextResponse.json({ error: "from and to go together" }, { status: 400 });
    }
    // iteration 203 E2 C2: the heatmap follows the page's month / range
    const path = range
      ? `/analytics/heatmap?from=${range.from}&to=${range.to}`
      : `/analytics/heatmap?months=${searchParams.get("months") ?? "6"}`;

    const data = await ingestFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Dashboard heatmap error:", error);
    return NextResponse.json({ error: "Failed to fetch heatmap data" }, { status: 500 });
  }
}
