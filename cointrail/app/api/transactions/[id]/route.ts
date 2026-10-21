import { badRequest, jsonBody, numericId, proxyIngest } from "@/lib/ingest-route";

/**
 * Re-file one transaction (iteration 203 E3). Body: {category, merchant?, apply_to_payee?}.
 * The row becomes a manual override; with apply_to_payee Ingest also saves a payee rule (keyed on
 * the row's UPI ID / account / exact payee) and relabels that payee's other payments.
 */
export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const id = numericId((await params).id);
  if (!id) return badRequest("Invalid transaction id");
  const body = await jsonBody(request);
  const category = typeof body?.category === "string" ? body.category.trim() : "";
  if (!category) return badRequest("category must be a non-empty string");

  const forward: Record<string, unknown> = { category };
  if (typeof body?.merchant === "string" && body.merchant.trim()) {
    forward.merchant = body.merchant.trim();
  }
  if (body?.apply_to_payee === true) forward.apply_to_payee = true;

  return proxyIngest(`/transactions/${id}`, { method: "PUT", body: JSON.stringify(forward) });
}
