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
    // iteration 203 E2 C2: trends follow the page's month / range
    const path = range
      ? `/analytics/trends?from=${range.from}&to=${range.to}`
      : `/analytics/trends?months=${searchParams.get("months") ?? "12"}`;

    const data = await ingestFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Dashboard trends error:", error);
    return NextResponse.json({ error: "Failed to fetch trends data" }, { status: 500 });
  }
}
