import { getDb } from "@/lib/db";
import { ensureMigrated } from "@/lib/migrate";
import { NextResponse } from "next/server";

export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  ensureMigrated();
  const { id } = await params;
  const body = await request.json();
  const db = getDb();

  const fields: string[] = [];
  const values: any[] = [];

  if (body.amount !== undefined) {
    fields.push("amount = ?");
    values.push(body.amount);
  }
  if (body.category !== undefined) {
    fields.push("category = ?");
    values.push(body.category);
  }
  if (body.subcategory !== undefined) {
    fields.push("subcategory = ?");
    values.push(body.subcategory);
  }
  if (body.date !== undefined) {
    fields.push("date = ?");
    values.push(body.date);
  }
  if (body.description !== undefined) {
    fields.push("description = ?");
    values.push(body.description);
  }
  if (body.source !== undefined) {
    fields.push("source = ?");
    values.push(body.source);
  }
  if (body.card_id !== undefined) {
    fields.push("card_id = ?");
    values.push(body.card_id);
  }
  if (body.notes !== undefined) {
    fields.push("notes = ?");
    values.push(body.notes);
  }

  if (fields.length === 0) {
    return NextResponse.json({ error: "No fields to update" }, { status: 400 });
  }

  values.push(id);
  const stmt = db.prepare(`UPDATE expenses SET ${fields.join(", ")} WHERE id = ?`);
  const result = stmt.run(...values);

  if (result.changes === 0) {
    return NextResponse.json({ error: "Expense not found" }, { status: 404 });
  }

  return NextResponse.json({ updated: result.changes });
}

// DELETE endpoint removed intentionally — prevents accidental data deletion
// Use the ingest service to manage transaction data
