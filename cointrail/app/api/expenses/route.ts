import { getDb } from "@/lib/db";
import { ensureMigrated } from "@/lib/migrate";
import { NextResponse } from "next/server";
import { ingestFetch } from "@/lib/ingest";

// CC bill payment patterns — these are NOT real expenses (double counting)
const CC_PAYMENT_PATTERNS = [
  /\bcred\b/i,
  /\bcredit\s*card\s*payment\b/i,
  /\bcc\s*payment\b/i,
  /\bcard\s*bill\s*payment\b/i,
  /\bbilldesk.*card/i,
  /\bcc\s*bill\b/i,
];

function isCCPayment(description: string, category: string): boolean {
  if (!description) return false;
  const text = description.toLowerCase();
  if (text === "cred" || text.startsWith("cred ")) return true;
  return CC_PAYMENT_PATTERNS.some((p) => p.test(text));
}

function getMonthRange(offset: number): { start: string; end: string } {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth() + offset;
  const first = new Date(y, m, 1);
  const last = new Date(y, m + 1, 0);
  const fmt = (d: Date) => d.toISOString().split("T")[0];
  return { start: fmt(first), end: fmt(last) };
}

function buildCategorySummary(expenses: any[]): any[] {
  const catMap: Record<
    string,
    { total: number; count: number; merchants: Record<string, { total: number; count: number }> }
  > = {};

  for (const exp of expenses) {
    const cat = exp.category || "Uncategorized";
    if (!catMap[cat]) catMap[cat] = { total: 0, count: 0, merchants: {} };
    catMap[cat].total += exp.amount || 0;
    catMap[cat].count += 1;

    const merchant = (exp.description || "Unknown").trim();
    if (!catMap[cat].merchants[merchant]) catMap[cat].merchants[merchant] = { total: 0, count: 0 };
    catMap[cat].merchants[merchant].total += exp.amount || 0;
    catMap[cat].merchants[merchant].count += 1;
  }

  return Object.entries(catMap)
    .map(([category, data]) => ({
      category,
      total: data.total,
      count: data.count,
      merchants: Object.entries(data.merchants)
        .map(([name, m]) => ({ name, total: m.total, count: m.count }))
        .sort((a, b) => b.total - a.total),
    }))
    .sort((a, b) => b.total - a.total);
}

export async function GET(request: Request) {
  ensureMigrated();
  const { searchParams } = new URL(request.url);
  const category = searchParams.get("category");
  const source = searchParams.get("source");
  const startDate = searchParams.get("startDate");
  const endDate = searchParams.get("endDate");
  const limit = parseInt(searchParams.get("limit") || "500");

  // 1. Get local manual entries
  const db = getDb();
  let localQuery =
    "SELECT * FROM expenses WHERE source IN (\x27manual\x27, \x27cash\x27, \x27recurring\x27)";
  const localParams: any[] = [];
  if (category && category !== "all") {
    localQuery += " AND category = ?";
    localParams.push(category);
  }
  if (startDate) {
    localQuery += " AND date >= ?";
    localParams.push(startDate);
  }
  if (endDate) {
    localQuery += " AND date <= ?";
    localParams.push(endDate);
  }
  localQuery += " ORDER BY date DESC";
  const localRows = db.prepare(localQuery).all(...localParams);

  // 2. Get ingest transactions (automated: sms, card, email)
  let ingestPath = "/transactions?limit=2000&include_duplicates=false";
  if (
    source &&
    source !== "all" &&
    source !== "manual" &&
    source !== "cash" &&
    source !== "recurring"
  ) {
    ingestPath += "&source=" + source;
  }
  if (category && category !== "all") ingestPath += "&category=" + category;
  if (startDate) ingestPath += "&from=" + startDate;
  if (endDate) ingestPath += "&to=" + endDate;

  let ingestRows: any[] = [];
  try {
    const ingestData = await ingestFetch(ingestPath);
    ingestRows = (ingestData.transactions || [])
      .filter((t: any) => {
        if (t.source === "sms" || t.source === "upi") {
          const desc = t.merchant || t.raw_description || "";
          if (isCCPayment(desc, t.category || "")) return false;
        }
        return true;
      })
      .map((t: any) => ({
        id: "ingest-" + t.id,
        amount: t.amount,
        category: t.category || "Uncategorized",
        subcategory: null,
        date: t.date,
        description: t.merchant || t.raw_description || "Unknown",
        source: t.source,
        card_id: t.card_id,
        notes: t.raw_description,
        created_at: t.created_at,
      }));
  } catch (e) {
    console.error("Ingest fetch failed:", e);
  }

  // 3. Merge and sort
  let all = [...localRows, ...ingestRows];

  if (source && source !== "all") {
    if (source === "manual" || source === "cash" || source === "recurring") {
      all = localRows;
    } else {
      all = ingestRows;
    }
  }

  all.sort((a: any, b: any) => (b.date || "").localeCompare(a.date || ""));

  // Build category summary BEFORE slicing
  const categorySummary = buildCategorySummary(all);

  all = all.slice(0, limit);

  // Month total from both sources
  const now = new Date();
  const currentMonth = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
  const localMonthTotal = db
    .prepare(
      "SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE source IN (\x27manual\x27,\x27cash\x27,\x27recurring\x27) AND strftime(\x27%Y-%m\x27, date) = ?"
    )
    .get(currentMonth) as any;

  const monthStart = currentMonth + "-01";
  const monthEnd = currentMonth + "-31";
  const ingestMonthTotal = ingestRows
    .filter((r: any) => r.date >= monthStart && r.date <= monthEnd)
    .reduce((sum: number, r: any) => sum + (r.amount || 0), 0);

  // Previous month total
  const { start: prevStart, end: prevEnd } = getMonthRange(-1);
  const localPrevTotal = db
    .prepare(
      "SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE source IN (\x27manual\x27,\x27cash\x27,\x27recurring\x27) AND date >= ? AND date <= ?"
    )
    .get(prevStart, prevEnd) as any;

  let ingestPrevTotal = 0;
  try {
    const prevIngest = await ingestFetch(
      "/transactions?limit=2000&include_duplicates=false&from=" + prevStart + "&to=" + prevEnd
    );
    ingestPrevTotal = (prevIngest.transactions || [])
      .filter((t: any) => {
        if (t.source === "sms" || t.source === "upi") {
          const desc = t.merchant || t.raw_description || "";
          if (isCCPayment(desc, t.category || "")) return false;
        }
        return true;
      })
      .reduce((sum: number, t: any) => sum + (t.amount || 0), 0);
  } catch {}

  return NextResponse.json({
    expenses: all,
    monthTotal: (localMonthTotal?.total || 0) + ingestMonthTotal,
    prevMonthTotal: (localPrevTotal?.total || 0) + ingestPrevTotal,
    categorySummary,
    txnCount: all.length,
  });
}

export async function POST(request: Request) {
  ensureMigrated();
  const body = await request.json();
  const db = getDb();
  const stmt = db.prepare(
    "INSERT INTO expenses (amount, category, subcategory, date, description, source, card_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
  );
  const result = stmt.run(
    body.amount,
    body.category,
    body.subcategory || null,
    body.date,
    body.description || null,
    body.source || "manual",
    body.card_id || null,
    body.notes || null
  );
  return NextResponse.json({ id: result.lastInsertRowid }, { status: 201 });
}
