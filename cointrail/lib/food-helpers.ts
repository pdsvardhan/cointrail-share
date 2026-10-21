// Shared food email parsing helpers used by /api/food and /api/food/orders

export const FOOD_SENDERS = ["swiggy", "zomato", "blinkit", "zepto"];

export function extractRestaurant(subject: string, snippet: string): string | null {
  const text = `${subject} ${snippet}`;
  const patterns = [
    /order from\s+(.+?)\s+(?:has been|is on|was|is being|delivered)/i,
    /from\s+(.+?)\s+(?:delivered|confirmed|placed)/i,
    /Restaurant\s+(.+?)\s+Your Order/i,
    /ordering from\s+(.+?)(?:\s+ORDER|\s+Your|\s*$)/i,
  ];
  for (const p of patterns) {
    const m = text.match(p);
    if (m) return m[1].trim();
  }
  return null;
}

export function extractAmount(text: string): number | null {
  const patterns = [
    /(?:Rs\.?|₹|INR)\s*([\d,]+(?:\.\d{2})?)/i,
    /(?:amount|total|paid|fare|bill)[:\s]*(?:Rs\.?|₹|INR)?\s*([\d,]+(?:\.\d{2})?)/i,
  ];
  for (const p of patterns) {
    const m = text.match(p);
    if (m) {
      const val = parseFloat(m[1].replace(/,/g, ""));
      if (!isNaN(val)) return val;
    }
  }
  return null;
}

export function getOrderType(subject: string, snippet: string, senderEmail: string): string {
  const text = `${subject} ${snippet}`.toLowerCase();
  if (text.includes("dineout")) return "dineout";
  if (text.includes("instamart")) return "instamart";
  if (senderEmail.includes("blinkit") || text.includes("blinkit")) return "blinkit";
  if (senderEmail.includes("zepto") || text.includes("zepto")) return "zepto";
  if (text.includes("gourmet")) return "gourmet";
  return "delivery";
}

export function getPlatform(senderEmail: string): string {
  if (senderEmail.includes("swiggy")) return "swiggy";
  if (senderEmail.includes("zomato")) return "zomato";
  if (senderEmail.includes("blinkit")) return "blinkit";
  if (senderEmail.includes("zepto")) return "zepto";
  return "other";
}

export function extractDate(dateStr: string): string | null {
  if (!dateStr) return null;
  const isoMatch = dateStr.match(/(\d{4}-\d{2}-\d{2})/);
  if (isoMatch) return isoMatch[1];
  try {
    const d = new Date(dateStr);
    if (!isNaN(d.getTime())) return d.toISOString().split("T")[0];
  } catch {}
  return null;
}

export function isOrderEmail(subject: string): boolean {
  const subj = subject.toLowerCase();
  return (
    subj.includes("order") ||
    subj.includes("deliver") ||
    subj.includes("dineout") ||
    subj.includes("payment") ||
    subj.includes("instamart")
  );
}
