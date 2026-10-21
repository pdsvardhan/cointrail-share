import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";
import { pickQuery, rangeParams } from "@/lib/query";

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const merchant = searchParams.get("merchant");

    if (!merchant) {
      return NextResponse.json({ error: "merchant parameter required" }, { status: 400 });
    }
    const range = rangeParams(searchParams);
    if (range === "half") {
      return NextResponse.json({ error: "from and to go together" }, { status: 400 });
    }

    // iteration 203 E2 B4: the drill is the clicked merchant inside its category and the
    // page's window; `months` stays for callers that send no range.
    let path = `/analytics/merchant-detail?${pickQuery(searchParams, ["merchant", "category"])}`;
    path += range
      ? `&from=${range.from}&to=${range.to}`
      : `&months=${searchParams.get("months") || "12"}`;

    const data = await ingestFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Merchant detail error:", error);
    return NextResponse.json({ error: "Failed to fetch merchant detail" }, { status: 500 });
  }
}
