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
    const category = searchParams.get("category");

    let path: string;
    if (range) {
      // iteration 203 E1: the range pills
      path = `/analytics/categories?from=${range.from}&to=${range.to}`;
    } else {
      const now = new Date();
      const period =
        searchParams.get("period") ??
        `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
      path = `/analytics/categories?period=${period}`;
    }
    if (category) path += `&category=${encodeURIComponent(category)}`;

    const data = await ingestFetch(path);
    return NextResponse.json(data);
  } catch (error) {
    console.error("Dashboard categories error:", error);
    return NextResponse.json({ error: "Failed to fetch category data" }, { status: 500 });
  }
}
