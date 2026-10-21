import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { searchParams } = new URL(request.url);
  const period = searchParams.get("period") || undefined;
  const from = searchParams.get("from") || undefined;
  const to = searchParams.get("to") || undefined;
  const limit = searchParams.get("limit") || "500";

  let path = `/cards/${id}/transactions?limit=${limit}`;
  if (period) path += `&period=${period}`;
  if (from) path += `&from=${from}`;
  if (to) path += `&to=${to}`;

  try {
    const data = await ingestFetch(path);
    return NextResponse.json(data);
  } catch (e: any) {
    console.error("Card transactions fetch failed:", e);
    // 502 so the page shows an error with Retry, not "No transactions for this period" (E2 M11).
    return NextResponse.json({ transactions: [], error: e.message }, { status: 502 });
  }
}
