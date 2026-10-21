import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";
import { pickQuery, rangeParams } from "@/lib/query";

/**
 * Every transaction behind the period's Spending figure, paged (iteration 203 E2 M1). The
 * Overview's "View All" used to open the same top-10 list it already showed.
 */
export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    if (rangeParams(searchParams) === "half") {
      return NextResponse.json({ error: "from and to go together" }, { status: 400 });
    }
    const query = pickQuery(searchParams, ["period", "from", "to", "sort", "offset", "limit"]);
    const data = await ingestFetch(`/analytics/transactions${query ? `?${query}` : ""}`);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Dashboard transactions error:", error);
    return NextResponse.json({ error: "Failed to fetch transactions" }, { status: 500 });
  }
}
