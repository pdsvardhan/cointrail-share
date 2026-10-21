import {
  Utensils,
  ShoppingBag,
  Car,
  Zap,
  Home,
  Heart,
  Tv,
  Plane,
  GraduationCap,
  User,
  CreditCard,
  HelpCircle,
  Fuel,
  Smartphone,
  Package,
  Landmark,
  Shield,
  Gift,
  Globe,
  Coffee,
  Building2,
  Users,
  Send,
  ArrowLeftRight,
  ArrowRight,
} from "lucide-react";

/* ═══════════════════════════════════════════════════════════════════════════
   Category Colors — hex color map for pie charts, bar charts, etc.
   ═══════════════════════════════════════════════════════════════════════════ */

export const CATEGORY_COLORS: Record<string, string> = {
  "Food & Dining": "#f97316",
  Transport: "#3b82f6",
  Shopping: "#ec4899",
  "Bills & Utilities": "#eab308",
  "Rent & Housing": "#6366f1",
  Health: "#ef4444",
  Entertainment: "#a855f7",
  Travel: "#06b6d4",
  Education: "#14b8a6",
  Personal: "#f43f5e",
  "EMI & Loans": "#f59e0b",
  Investments: "#10b981",
  Miscellaneous: "#6b7280",
  Groceries: "#84cc16",
  Fuel: "#fb923c",
  Subscriptions: "#8b5cf6",
  Uncategorized: "#6b7280",
  Transfer: "#64748b",
  Insurance: "#0ea5e9",
  "Government & Taxes": "#71717a",
  "Housemate Expenses": "#f472b6",
  "Family Remittance": "#c084fc",
  "Home Construction": "#d97706",
  "Self Transfer": "#475569",
  Finance: "#0ea5e9",
};

/* ═══════════════════════════════════════════════════════════════════════════
   Category Badge Colors — Tailwind class strings for Badge components
   Maps to same categories as CATEGORY_COLORS but for UI badge styling.
   ═══════════════════════════════════════════════════════════════════════════ */

export const CATEGORY_BADGE_COLORS: Record<string, string> = {
  "Food & Dining": "bg-orange-500/20 text-orange-300 border-orange-500/30",
  Transport: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  Shopping: "bg-pink-500/20 text-pink-300 border-pink-500/30",
  "Bills & Utilities": "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  "Rent & Housing": "bg-indigo-500/20 text-indigo-300 border-indigo-500/30",
  Health: "bg-green-500/20 text-green-300 border-green-500/30",
  Entertainment: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  Travel: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  Education: "bg-teal-500/20 text-teal-300 border-teal-500/30",
  Personal: "bg-rose-500/20 text-rose-300 border-rose-500/30",
  "EMI & Loans": "bg-red-500/20 text-red-300 border-red-500/30",
  Investments: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  Miscellaneous: "bg-gray-500/20 text-gray-300 border-gray-500/30",
  Groceries: "bg-lime-500/20 text-lime-300 border-lime-500/30",
  Fuel: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  Subscriptions: "bg-violet-500/20 text-violet-300 border-violet-500/30",
  "Tech & Subscriptions": "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  Uncategorized: "bg-gray-500/20 text-gray-300 border-gray-500/30",
  Transfer: "bg-slate-500/20 text-slate-300 border-slate-500/30",
  Insurance: "bg-teal-500/20 text-teal-300 border-teal-500/30",
  "Government & Taxes": "bg-zinc-500/20 text-zinc-300 border-zinc-500/30",
  "Housemate Expenses": "bg-pink-500/20 text-pink-300 border-pink-500/30",
  "Family Remittance": "bg-purple-500/20 text-purple-300 border-purple-500/30",
  "Home Construction": "bg-amber-600/20 text-amber-300 border-amber-600/30",
  "Self Transfer": "bg-slate-500/20 text-slate-300 border-slate-500/30",
  Finance: "bg-sky-500/20 text-sky-300 border-sky-500/30",
};

/* ═══════════════════════════════════════════════════════════════════════════
   Category Icons — lucide icon component map
   ═══════════════════════════════════════════════════════════════════════════ */

export const CATEGORY_ICONS: Record<string, React.ElementType> = {
  "Food & Dining": Utensils,
  Transport: Car,
  Shopping: ShoppingBag,
  "Bills & Utilities": Zap,
  "Rent & Housing": Home,
  Health: Heart,
  Entertainment: Tv,
  Travel: Plane,
  Education: GraduationCap,
  Personal: User,
  "EMI & Loans": CreditCard,
  Investments: Landmark,
  Miscellaneous: HelpCircle,
  Groceries: Package,
  Fuel: Fuel,
  Subscriptions: Smartphone,
  Uncategorized: HelpCircle,
  Transfer: ArrowRight,
  Insurance: Shield,
  "Government & Taxes": Globe,
  "Gifts & Donations": Gift,
  "Housemate Expenses": Users,
  "Family Remittance": Send,
  "Home Construction": Building2,
  "Self Transfer": ArrowLeftRight,
  Finance: Landmark,
};

/* ═══════════════════════════════════════════════════════════════════════════
   Month Short Names
   ═══════════════════════════════════════════════════════════════════════════ */

export const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/* ═══════════════════════════════════════════════════════════════════════════
   Card Colors — unified gradient config for credit card visuals
   Used by: CreditCards page (shine), CardDetail page (accent), both use bg
   ═══════════════════════════════════════════════════════════════════════════ */

export interface CardColorConfig {
  bg: string;
  accent: string;
  shine: string;
}

export const CARD_COLORS: Record<string, CardColorConfig> = {
  Millennia: {
    bg: "from-[#0a1628] via-[#122448] to-[#081020]",
    accent: "#c8a961",
    shine: "rgba(200,169,97,0.15)",
  },
  Swiggy: {
    bg: "from-[#7a3200] via-[#9e4500] to-[#5c2000]",
    accent: "#ffe0b2",
    shine: "rgba(255,224,178,0.15)",
  },
  "Tata Neu": {
    bg: "from-[#6a1b4d] via-[#ad1457] to-[#4a0e35]",
    accent: "#f48fb1",
    shine: "rgba(244,143,177,0.15)",
  },
  AURUM: {
    bg: "from-[#1a1400] via-[#2d2200] to-[#0a0800]",
    accent: "#d4a843",
    shine: "rgba(212,168,67,0.2)",
  },
  Cashback: {
    bg: "from-[#1e2a3a] via-[#2c3e50] to-[#152030]",
    accent: "#90a4ae",
    shine: "rgba(144,164,174,0.15)",
  },
  "Amazon Pay": {
    bg: "from-[#0d1b2a] via-[#1b2838] to-[#0a1520]",
    accent: "#ff9900",
    shine: "rgba(255,153,0,0.15)",
  },
  Sapphiro: {
    bg: "from-[#0a2e2a] via-[#115e54] to-[#062220]",
    accent: "#4db6ac",
    shine: "rgba(77,182,172,0.15)",
  },
  Platinum: {
    bg: "from-[#37474f] via-[#546e7a] to-[#263238]",
    accent: "#b0bec5",
    shine: "rgba(176,190,197,0.2)",
  },
  Airtel: {
    bg: "from-[#6d1010] via-[#8b1a1a] to-[#4a0000]",
    accent: "#ef9a9a",
    shine: "rgba(239,154,154,0.12)",
  },
};

/* ═══════════════════════════════════════════════════════════════════════════
   Bank Colors — fallback gradients keyed by bank name
   ═══════════════════════════════════════════════════════════════════════════ */

export const BANK_COLORS: Record<string, CardColorConfig> = {
  HDFC: {
    bg: "from-[#0c1b3a] via-[#162d5a] to-[#0a1628]",
    accent: "#3b82f6",
    shine: "rgba(59,130,246,0.15)",
  },
  ICICI: {
    bg: "from-[#2a1500] via-[#3d2000] to-[#1a0e00]",
    accent: "#f97316",
    shine: "rgba(249,115,22,0.15)",
  },
  SBI: {
    bg: "from-[#0f0a2e] via-[#1a1248] to-[#0a0620]",
    accent: "#7c3aed",
    shine: "rgba(124,58,237,0.15)",
  },
  HSBC: {
    bg: "from-[#2a0a0a] via-[#3d1515] to-[#1a0505]",
    accent: "#dc2626",
    shine: "rgba(220,38,38,0.15)",
  },
  Axis: {
    bg: "from-[#2a0a1a] via-[#3d1528] to-[#1a050f]",
    accent: "#ec4899",
    shine: "rgba(236,72,153,0.15)",
  },
};

/* ═══════════════════════════════════════════════════════════════════════════
   Recharts Theme Colors — for axis ticks and grid
   ═══════════════════════════════════════════════════════════════════════════ */

// Deprecated: use var(--chart-axis) CSS variable directly in Recharts props
export const CHART_AXIS_COLOR = "var(--chart-axis)";
// Deprecated: use var(--chart-grid) CSS variable directly in Recharts props
export const CHART_GRID_COLOR = "var(--chart-grid)";

/* ═══════════════════════════════════════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════════════════════════════════════ */

export function getCategoryColor(cat: string): string {
  return CATEGORY_COLORS[cat] || "#6b7280";
}

export function getCategoryBadgeColor(cat: string): string {
  return CATEGORY_BADGE_COLORS[cat] || CATEGORY_BADGE_COLORS["Uncategorized"];
}

export function getCategoryIcon(cat: string): React.ElementType {
  return CATEGORY_ICONS[cat] || HelpCircle;
}

export function getCardColors(bank: string, name: string): CardColorConfig {
  return (
    CARD_COLORS[name] ||
    BANK_COLORS[bank] || {
      bg: "from-[#1a1a2e] via-[#252540] to-[#0f0f1a]",
      accent: "#6366f1",
      shine: "rgba(99,102,241,0.15)",
    }
  );
}
