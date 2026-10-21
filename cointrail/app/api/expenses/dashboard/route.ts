import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";
import { rangeParams } from "@/lib/query";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);

    // A date range (the 3M / 6M / 1Y / All pills, iteration 203 E1) is forwarded as is.
    const range = rangeParams(searchParams);
    if (range === "half") {
      return NextResponse.json({ error: "from and to go together" }, { status: 400 });
    }
    if (range) {
      const [overview, recurring] = await Promise.all([
        ingestFetch(`/analytics/overview?from=${range.from}&to=${range.to}`),
        ingestFetch("/recurring/detect"),
      ]);
      return NextResponse.json({ overview, recurring, period: null, range });
    }

    const now = new Date();
    const currentPeriod = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

    let period = searchParams.get("period");
    if (!period) {
      // Default to the latest month that actually has data, so the dashboard
      // isn't blank when capture is stale (e.g. the SMS feed is down). The
      // data-aware /health endpoint (Ingest) reports the last transaction date.
      try {
        const health = (await ingestFetch("/health")) as { last_transaction?: string | null };
        const lastTxn = health?.last_transaction ?? null;
        period = lastTxn ? lastTxn.slice(0, 7) : currentPeriod;
      } catch {
        period = currentPeriod;
      }
    }

    const [overview, recurring] = await Promise.all([
      ingestFetch(`/analytics/overview?period=${period}`),
      ingestFetch("/recurring/detect"),
    ]);

    // `period` is echoed so the UI can show which month it's displaying
    // (and flag when that's not the current month).
    return NextResponse.json({ overview, recurring, period, range: null });
  } catch (error) {
    console.error("Dashboard overview error:", error);
    return NextResponse.json({ error: "Failed to fetch dashboard overview" }, { status: 500 });
  }
}
