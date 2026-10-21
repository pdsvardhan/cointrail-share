// Merchant logo resolution
// Uses Google Favicon API as primary source

// Known merchant -> domain mappings
const MERCHANT_DOMAINS: Record<string, string> = {
  // Food & Delivery
  swiggy: "swiggy.com",
  zomato: "zomato.com",
  blinkit: "blinkit.com",
  zepto: "zeptonow.com",
  dunzo: "dunzo.com",
  bigbasket: "bigbasket.com",
  "bb now": "bigbasket.com",
  jiomart: "jiomart.com",
  dominos: "dominos.co.in",
  mcdonalds: "mcdonalds.com",
  "burger king": "burgerking.in",
  starbucks: "starbucks.in",
  kfc: "kfc.co.in",
  subway: "subway.com",

  // Shopping
  amazon: "amazon.in",
  flipkart: "flipkart.com",
  myntra: "myntra.com",
  ajio: "ajio.com",
  meesho: "meesho.com",
  nykaa: "nykaa.com",
  "tata cliq": "tatacliq.com",
  croma: "croma.com",
  "reliance digital": "reliancedigital.in",

  // Transport
  uber: "uber.com",
  ola: "olacabs.com",
  rapido: "rapido.bike",
  irctc: "irctc.co.in",
  makemytrip: "makemytrip.com",
  goibibo: "goibibo.com",
  yatra: "yatra.com",
  cleartrip: "cleartrip.com",
  redbus: "redbus.in",
  indigo: "goindigo.in",
  "air india": "airindia.com",
  vistara: "airvistara.com",

  // Entertainment
  netflix: "netflix.com",
  hotstar: "hotstar.com",
  "prime video": "primevideo.com",
  spotify: "spotify.com",
  youtube: "youtube.com",
  bookmyshow: "bookmyshow.com",
  "sony liv": "sonyliv.com",
  zee5: "zee5.com",
  jiocinema: "jiocinema.com",

  // Bills & Utilities
  airtel: "airtel.in",
  jio: "jio.com",
  vi: "myvi.in",
  bsnl: "bsnl.co.in",
  "tata power": "tatapower.com",
  adani: "adani.com",

  // Tech & Subscriptions
  anthropic: "anthropic.com",
  chatgpt: "openai.com",
  openai: "openai.com",
  github: "github.com",
  google: "google.com",
  apple: "apple.com",
  microsoft: "microsoft.com",
  notion: "notion.so",
  figma: "figma.com",
  vercel: "vercel.com",
  aws: "aws.amazon.com",
  digitalocean: "digitalocean.com",

  // Finance
  cred: "cred.club",
  paytm: "paytm.com",
  phonepe: "phonepe.com",
  gpay: "pay.google.com",
  "google pay": "pay.google.com",
  groww: "groww.in",
  zerodha: "zerodha.com",
  indmoney: "indmoney.com",
  coin: "coin.zerodha.com",
  razorpay: "razorpay.com",

  // Health
  practo: "practo.com",
  pharmeasy: "pharmeasy.in",
  "1mg": "1mg.com",
  netmeds: "netmeds.com",
  "cult.fit": "cult.fit",

  // Banks (for card logos)
  hdfc: "hdfcbank.com",
  icici: "icicibank.com",
  sbi: "sbi.co.in",
  axis: "axisbank.com",
  hsbc: "hsbc.co.in",
  kotak: "kotak.com",
  "yes bank": "yesbank.in",
  idfc: "idfcfirstbank.com",
};

/**
 * Get logo URL for a merchant name.
 * Uses Google Favicon API with domain mapping.
 * Returns null if no domain mapping found.
 */
export function getMerchantLogoUrl(merchant: string, size: number = 32): string | null {
  if (!merchant) return null;
  const lower = merchant.toLowerCase().trim();

  // Direct match
  if (MERCHANT_DOMAINS[lower]) {
    return `https://www.google.com/s2/favicons?domain=${MERCHANT_DOMAINS[lower]}&sz=${size}`;
  }

  // Partial match (e.g., "SWIGGY ORDER" matches "swiggy")
  for (const [key, domain] of Object.entries(MERCHANT_DOMAINS)) {
    if (lower.includes(key) || key.includes(lower)) {
      return `https://www.google.com/s2/favicons?domain=${domain}&sz=${size}`;
    }
  }

  return null;
}

/**
 * Get bank logo URL
 */
export function getBankLogoUrl(bank: string, size: number = 32): string | null {
  if (!bank) return null;
  const lower = bank.toLowerCase().trim();
  if (MERCHANT_DOMAINS[lower]) {
    return `https://www.google.com/s2/favicons?domain=${MERCHANT_DOMAINS[lower]}&sz=${size}`;
  }
  return null;
}
