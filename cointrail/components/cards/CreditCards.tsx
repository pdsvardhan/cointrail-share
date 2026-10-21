"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import {
  CreditCard as CreditCardIcon,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Hourglass,
  TrendingUp,
  Activity,
  Wallet,
  ArrowRight,
  Banknote,
  CalendarClock,
  ShieldCheck,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  HelpCircle,
} from "lucide-react";
import { autoFormatCurrency } from "@/lib/formatters";
import { CARD_COLORS, BANK_COLORS, getCardColors } from "@/lib/constants";
import { daysUntil, parseLocalDate } from "@/lib/dates";
import {
  PAYMENT_STATUSES,
  type PaymentStatus,
  isDueDateEstimated,
  isOwing,
  isSettled,
  remainingDue,
  resolvePaymentStatus,
  statementAmountDue,
  summarizeCards,
} from "@/lib/card-status";
import { AnimatedCurrency, AnimatedNumber } from "@/components/shared/AnimatedCurrency";
import { GradientStatCard } from "@/components/shared/StatCard";
import { ErrorState } from "@/components/shared/ErrorState";
import { errorMessage, getJson } from "@/lib/http";
import { clampIndex } from "@/lib/ui";

/* ═══════════════════════════════════════════════════════════════════════════
   Types
   ═══════════════════════════════════════════════════════════════════════════ */

interface LatestStatement {
  month: number;
  year: number;
  total: number;
  txn_count: number;
  billing_start: string;
  billing_end: string;
  due_date: string | null;
  // iter-18 contract 3 (absent on an older Ingest):
  due_date_source?: "bank" | "estimated" | null;
  amount_due?: number | null;
  stated_total?: number | null;
  computed_total?: number | null;
  min_due?: number | null;
}

interface CardSummary {
  id: number;
  bank: string;
  card_name: string;
  last4: string;
  network: string | null;
  billing_group: string | null;
  statement_day: number | null;
  due_day: number | null;
  latest_statement: LatestStatement | null;
  is_paid: boolean;
  payment?: {
    total: number;
    count: number;
    latest_date: string | null;
    payments: { amount: number; date: string }[];
  } | null;
  unbilled: { total: number; count: number };
  active_emis?: number;
  total_spend: number;
  total_txns: number;
  actual_due?: number | null;
  // iter-18 contract 3: decided by Ingest in IST.
  payment_status?: string | null;
  remaining_due?: number | null;
  current_cycle?: boolean | null;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Card States — Ingest's payment_status (iter-18 contract 3); the browser no
   longer decides "overdue" itself (it did so from a UTC-parsed date, M9).
   ═══════════════════════════════════════════════════════════════════════════ */

type CardState = PaymentStatus;

function getState(c: CardSummary): CardState {
  return resolvePaymentStatus(c);
}

/** Sort/priority order: most urgent first (overdue, partial, unpaid, not_confirmed, ...). */
const STATE_ORDER = PAYMENT_STATUSES;

const STATE_CONFIG: Record<
  CardState,
  { label: string; Icon: React.ElementType; color: string; bg: string }
> = {
  overdue: {
    label: "Overdue",
    Icon: AlertTriangle,
    color: "text-white/80 dark:text-white/70",
    bg: "bg-white/12",
  },
  partial: {
    label: "Part paid",
    Icon: Hourglass,
    color: "text-white/70 dark:text-white/60",
    bg: "bg-white/10",
  },
  unpaid: {
    label: "Unpaid",
    Icon: Hourglass,
    color: "text-white/60 dark:text-white/50",
    bg: "bg-white/8",
  },
  // Neutral on purpose: Ingest cannot tell whether this bill was paid (e.g. paid via an
  // app that sends no bank SMS), so it is neither an alarm nor a tick.
  not_confirmed: {
    label: "Not confirmed",
    Icon: HelpCircle,
    color: "text-white/50 dark:text-white/45",
    bg: "bg-white/5",
  },
  paid: {
    label: "Paid",
    Icon: CheckCircle2,
    color: "text-white/60 dark:text-white/50",
    bg: "bg-white/8",
  },
  no_dues: {
    label: "No dues",
    Icon: ShieldCheck,
    color: "text-white/60 dark:text-white/50",
    bg: "bg-white/8",
  },
};

/** Small "est." marker for a due date Ingest estimated from the card's configured due day. */
function EstMarker({ card, className }: { card: CardSummary; className: string }) {
  if (!isDueDateEstimated(card)) return null;
  return (
    <span
      className={className}
      title="Estimated from the card's usual due day; the bank's due date has not been captured"
    >
      est.
    </span>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Card Colors — Option A
   ═══════════════════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════════════════════
   Network Logo SVGs
   ═══════════════════════════════════════════════════════════════════════════ */

function VisaLogo({ className = "w-10 h-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 780 500" className={className} fill="none">
      <path
        d="M293.2 348.7l33.4-195.8h53.4l-33.4 195.8h-53.4zm246.8-191c-10.6-4-27.2-8.3-47.9-8.3-52.8 0-90 26.6-90.2 64.7-.3 28.2 26.6 43.9 46.9 53.3 20.9 9.6 27.9 15.8 27.8 24.4-.1 13.2-16.7 19.2-32.1 19.2-21.4 0-32.8-3-50.4-10.2l-6.9-3.1-7.5 44c12.5 5.5 35.7 10.2 59.7 10.5 56.2 0 92.7-26.3 93.1-67 .2-22.3-14-39.3-44.8-53.3-18.6-9.1-30.1-15.1-30-24.3 0-8.1 9.7-16.8 30.6-16.8 17.4-.3 30.1 3.5 39.9 7.5l4.8 2.3 7.2-42.8zm131.4-4.8h-41.3c-12.8 0-22.4 3.5-28 16.3l-79.4 179.7h56.1s9.2-24.1 11.2-29.4c6.1 0 60.6.1 68.4.1 1.6 6.9 6.5 29.3 6.5 29.3h49.6l-43.3-196zm-65.8 126.4c4.4-11.3 21.3-54.7 21.3-54.7-.3.5 4.4-11.3 7.1-18.7l3.6 16.9s10.2 46.8 12.4 56.5h-44.4zM283.5 152.9l-52.3 133.6-5.6-27.1c-9.7-31.2-39.9-65-73.7-81.9l47.8 171.1 56.6-.1 84.2-195.6h-56.9z"
        fill="white"
      />
      <path
        d="M146.9 152.9H60.8l-.7 4c67.1 16.2 111.5 55.4 129.9 102.5l-18.8-90.1c-3.2-12.4-12.6-16-24.3-16.4z"
        fill="rgba(255,255,255,0.7)"
      />
    </svg>
  );
}

function MastercardLogo({ className = "w-10 h-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 780 500" className={className}>
      <circle cx="310" cy="250" r="170" fill="rgba(235,0,27,0.9)" />
      <circle cx="470" cy="250" r="170" fill="rgba(255,159,0,0.85)" />
      <path
        d="M390 120.7c-40.4 32.6-66.3 82.1-66.3 137.3s25.9 104.7 66.3 137.3c40.4-32.6 66.3-82.1 66.3-137.3S430.4 153.3 390 120.7z"
        fill="rgba(255,91,0,0.9)"
      />
    </svg>
  );
}

function RupayLogo({ className = "w-10 h-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 32" className={className}>
      <text
        x="50"
        y="22"
        textAnchor="middle"
        fontFamily="Arial, sans-serif"
        fontWeight="800"
        fontSize="18"
        fill="white"
        letterSpacing="1"
      >
        RuPay
      </text>
      <rect x="5" y="27" width="90" height="2" rx="1" fill="url(#rupay-grad)" />
      <defs>
        <linearGradient id="rupay-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#00bcd4" />
          <stop offset="50%" stopColor="#ff9800" />
          <stop offset="100%" stopColor="#4caf50" />
        </linearGradient>
      </defs>
    </svg>
  );
}

function AmexLogo({ className = "w-10 h-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 32" className={className}>
      <text
        x="50"
        y="21"
        textAnchor="middle"
        fontFamily="Arial, sans-serif"
        fontWeight="800"
        fontSize="14"
        fill="white"
        letterSpacing="2"
      >
        AMEX
      </text>
      <rect
        x="10"
        y="2"
        width="80"
        height="28"
        rx="3"
        stroke="rgba(255,255,255,0.4)"
        strokeWidth="1.5"
        fill="none"
      />
    </svg>
  );
}

function NetworkLogo({ network, className }: { network: string | null; className?: string }) {
  if (!network) return null;
  const n = network.toLowerCase();
  if (n.includes("visa")) return <VisaLogo className={className} />;
  if (n.includes("master")) return <MastercardLogo className={className} />;
  if (n.includes("rupay")) return <RupayLogo className={className} />;
  if (n.includes("amex")) return <AmexLogo className={className} />;
  if (n.includes("+")) {
    const parts = network.split("+").map((s) => s.trim());
    return (
      <div className="flex gap-1">
        {parts.map((p, i) => (
          <NetworkLogo key={i} network={p} className={className} />
        ))}
      </div>
    );
  }
  return (
    <span className="text-[9px] font-bold tracking-wider text-white/50 uppercase">{network}</span>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   EMV Chip
   ═══════════════════════════════════════════════════════════════════════════ */

function EmvChip({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size * 0.75} viewBox="0 0 48 36" fill="none">
      <rect
        x="0.5"
        y="0.5"
        width="47"
        height="35"
        rx="5"
        fill="url(#chip-grad)"
        stroke="rgba(200,170,90,0.7)"
        strokeWidth="1.5"
      />
      <rect
        x="3"
        y="3"
        width="42"
        height="30"
        rx="3"
        fill="none"
        stroke="rgba(180,150,80,0.2)"
        strokeWidth="0.5"
      />
      <line x1="0.5" y1="12" x2="47.5" y2="12" stroke="rgba(160,130,60,0.5)" strokeWidth="0.7" />
      <line x1="0.5" y1="24" x2="47.5" y2="24" stroke="rgba(160,130,60,0.5)" strokeWidth="0.7" />
      <line x1="16" y1="0.5" x2="16" y2="35.5" stroke="rgba(160,130,60,0.4)" strokeWidth="0.7" />
      <line x1="32" y1="0.5" x2="32" y2="35.5" stroke="rgba(160,130,60,0.4)" strokeWidth="0.7" />
      <rect
        x="17"
        y="13"
        width="14"
        height="10"
        rx="1.5"
        fill="rgba(220,190,100,0.15)"
        stroke="rgba(200,170,80,0.3)"
        strokeWidth="0.5"
      />
      <defs>
        <linearGradient id="chip-grad" x1="0" y1="0" x2="48" y2="36">
          <stop offset="0%" stopColor="#c9a03e" />
          <stop offset="30%" stopColor="#e8cc70" />
          <stop offset="60%" stopColor="#f0d88e" />
          <stop offset="100%" stopColor="#b08828" />
        </linearGradient>
      </defs>
    </svg>
  );
}

function SilverEmvChip({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size * 0.75} viewBox="0 0 48 36" fill="none">
      <rect
        x="0.5"
        y="0.5"
        width="47"
        height="35"
        rx="5"
        fill="url(#silver-chip-grad)"
        stroke="rgba(180,185,195,0.6)"
        strokeWidth="1.2"
      />
      <rect
        x="3"
        y="3"
        width="42"
        height="30"
        rx="3"
        fill="none"
        stroke="rgba(160,165,175,0.15)"
        strokeWidth="0.5"
      />
      <line x1="0.5" y1="12" x2="47.5" y2="12" stroke="rgba(140,145,155,0.35)" strokeWidth="0.6" />
      <line x1="0.5" y1="24" x2="47.5" y2="24" stroke="rgba(140,145,155,0.35)" strokeWidth="0.6" />
      <line x1="16" y1="0.5" x2="16" y2="35.5" stroke="rgba(140,145,155,0.3)" strokeWidth="0.6" />
      <line x1="32" y1="0.5" x2="32" y2="35.5" stroke="rgba(140,145,155,0.3)" strokeWidth="0.6" />
      <rect
        x="17"
        y="13"
        width="14"
        height="10"
        rx="1.5"
        fill="rgba(190,195,205,0.12)"
        stroke="rgba(170,175,185,0.25)"
        strokeWidth="0.5"
      />
      <defs>
        <linearGradient id="silver-chip-grad" x1="0" y1="0" x2="48" y2="36">
          <stop offset="0%" stopColor="#b8bcc5" />
          <stop offset="30%" stopColor="#d0d4dc" />
          <stop offset="60%" stopColor="#c5c9d2" />
          <stop offset="100%" stopColor="#a8acb5" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Physical Card Replica — chip + date top, billed/paid/unbilled only
   ═══════════════════════════════════════════════════════════════════════════ */

function PhysicalCardReplica({
  card,
  colors,
}: {
  card: CardSummary;
  colors: { bg: string; accent: string; shine: string };
}) {
  const stmt = card.latest_statement;
  const paidAmt = card.payment?.total || 0;
  // The bill (bank-stated, else net of cashback/refunds), not the gross total (H2).
  const billedAmt = statementAmountDue(card);

  const emiCount = card.active_emis || 0;

  return (
    <div
      className={`relative w-full bg-gradient-to-br ${colors.bg} overflow-hidden rounded-xl`}
      style={{ aspectRatio: "0.68" }}
    >
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundSize: "14px 14px",
          backgroundImage: `linear-gradient(${colors.accent}50 1px, transparent 1px), linear-gradient(90deg, ${colors.accent}50 1px, transparent 1px)`,
        }}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "linear-gradient(155deg, rgba(255,255,255,0.2) 0%, rgba(255,255,255,0.05) 30%, transparent 60%)",
        }}
      />
      <div
        className="absolute -top-10 -right-10 h-32 w-32 rounded-full blur-3xl"
        style={{
          backgroundColor: colors.accent,
          opacity: 0.2,
        }}
      />
      <div className="absolute top-0 right-0 left-0 h-[1px] bg-gradient-to-r from-transparent via-white/25 to-transparent" />

      <div className="relative z-10 flex h-full flex-col p-3.5">
        {/* Row 1: Chip left, date right */}
        <div className="flex items-center justify-between">
          <SilverEmvChip size={28} />
          {stmt && (
            <span
              className="text-[10px] font-bold text-white/80"
              style={{ textShadow: "0 1px 4px rgba(0,0,0,0.8)" }}
            >
              {MS[stmt.month - 1]} {stmt.year}
            </span>
          )}
        </div>

        {/* Billed / Paid / Unbilled — moved up with mt-3 instead of mt-auto */}
        <div className="mt-3 space-y-1.5">
          <div className="flex items-center justify-between">
            <span
              className="text-[9px] font-bold tracking-wider text-white/60 uppercase"
              style={{ textShadow: "0 1px 3px rgba(0,0,0,0.7)" }}
            >
              Billed
            </span>
            <span
              className="text-[12px] font-bold text-white"
              style={{
                fontVariantNumeric: "tabular-nums",
                textShadow: "0 1px 4px rgba(0,0,0,0.8)",
              }}
            >
              {billedAmt > 0 ? autoFormatCurrency(billedAmt) : "\u2014"}
            </span>
          </div>
          <div className="h-[1px] w-full bg-white/10" />
          <div className="flex items-center justify-between">
            <span
              className="text-[9px] font-bold tracking-wider text-white/60 uppercase"
              style={{ textShadow: "0 1px 3px rgba(0,0,0,0.7)" }}
            >
              Paid
            </span>
            <span
              className="text-[12px] font-bold text-white/80"
              style={{
                fontVariantNumeric: "tabular-nums",
                textShadow: "0 1px 4px rgba(0,0,0,0.8)",
              }}
            >
              {paidAmt > 0 ? autoFormatCurrency(paidAmt) : "\u2014"}
            </span>
          </div>
          <div className="h-[1px] w-full bg-white/10" />
          <div className="flex items-center justify-between">
            <span
              className="text-[9px] font-bold tracking-wider text-white/60 uppercase"
              style={{ textShadow: "0 1px 3px rgba(0,0,0,0.7)" }}
            >
              Unbilled
            </span>
            <span
              className="text-[12px] font-bold text-white/80"
              style={{
                fontVariantNumeric: "tabular-nums",
                textShadow: "0 1px 4px rgba(0,0,0,0.8)",
              }}
            >
              {card.unbilled.total > 0 ? autoFormatCurrency(card.unbilled.total) : "\u2014"}
            </span>
          </div>
        </div>

        {/* EMI info */}
        <div className="pt-2">
          <div className="mb-1.5 h-[1px] w-full bg-white/8" />
          <div className="flex items-center justify-between">
            <span className="text-[8px] font-bold tracking-wider text-white/45 uppercase">
              EMIs
            </span>
            <span
              className="text-[10px] font-medium text-white/60"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {emiCount > 0 ? `${emiCount} active` : "None"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════════════════════════════════════ */

const MS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDue(d: string | null | undefined) {
  const date = parseLocalDate(d);
  return date ? `${MS[date.getMonth()]} ${date.getDate()}` : "—";
}
function fmtDateRange(start: string, end: string) {
  const [, sm, sd] = start.split("-").map(Number);
  const [, em, ed] = end.split("-").map(Number);
  return `${MS[sm - 1]} ${sd} - ${MS[em - 1]} ${ed}`;
}
function nextStmt(day: number) {
  const n = new Date();
  let m = n.getMonth(),
    y = n.getFullYear();
  if (n.getDate() >= day) {
    m++;
    if (m > 11) {
      m = 0;
      y++;
    }
  }
  return `${MS[m]} ${day}`;
}

function mergeCards(cards: CardSummary[]): CardSummary[] {
  const groups: Record<string, CardSummary[]> = {};
  const solo: CardSummary[] = [];
  for (const c of cards) {
    if (c.billing_group) {
      (groups[c.billing_group] ??= []).push(c);
    } else solo.push(c);
  }
  const merged = [...solo];
  for (const [, g] of Object.entries(groups)) {
    if (g.length <= 1) {
      merged.push(...g);
      continue;
    }
    const p = g.find((c) => c.latest_statement) || g[0];
    merged.push({
      ...p,
      card_name: p.card_name.replace(" Amex", "").replace(" MasterCard", ""),
      network:
        g
          .map((c) => c.network)
          .filter(Boolean)
          .join(" + ") || p.network,
      unbilled: {
        total: g.reduce((s, c) => s + c.unbilled.total, 0),
        count: g.reduce((s, c) => s + c.unbilled.count, 0),
      },
      total_spend: g.reduce((s, c) => s + c.total_spend, 0),
      total_txns: g.reduce((s, c) => s + c.total_txns, 0),
    });
  }
  return merged;
}

/* ═══════════════════════════════════════════════════════════════════════════
   3D Card Tile — taller, more spacing, text fades on tilt
   ═══════════════════════════════════════════════════════════════════════════ */

function CardTile3D({ card }: { card: CardSummary }) {
  const [hovered, setHovered] = useState(false);
  const colors = getCardColors(card.bank, card.card_name);
  const state = getState(card);
  const StatusIcon = STATE_CONFIG[state].Icon;
  const stmt = card.latest_statement;
  const settled = isSettled(state);

  return (
    <Link href={`/cards/${card.id}`}>
      <div
        className="relative cursor-pointer"
        style={{
          width: "100%",
          height: "220px",
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-end",
          perspective: "2500px",
          zIndex: hovered ? 30 : 1,
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {/* Cube edge strip */}
        <div
          className="absolute rounded-xl"
          style={{
            left: 0,
            right: 0,
            top: 0,
            height: "100%",
            transition: "all 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
            transform: hovered
              ? "perspective(900px) translateY(-5%) rotateX(38deg) translateZ(-3px)"
              : "perspective(900px) rotateX(0deg) translateZ(-3px)",
            background: `linear-gradient(to bottom, ${colors.accent}40, ${colors.accent}15)`,
            opacity: hovered ? 1 : 0,
            zIndex: 0,
          }}
        />

        {/* Layer 1: Flat card — tilts back, content fades */}
        <div
          className={`absolute inset-0 bg-gradient-to-br ${colors.bg} overflow-hidden rounded-xl`}
          style={{
            transition: "all 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
            transform: hovered
              ? "perspective(900px) translateY(-5%) rotateX(38deg) translateZ(0)"
              : "perspective(900px) rotateX(0deg) translateZ(0)",
            boxShadow: hovered
              ? "2px 35px 32px -8px rgba(0,0,0,0.75)"
              : "0 4px 16px rgba(0,0,0,0.3)",
            zIndex: 1,
          }}
        >
          <div
            className="absolute inset-0 opacity-[0.05]"
            style={{
              backgroundSize: "20px 20px",
              backgroundImage: `linear-gradient(${colors.accent}40 1px, transparent 1px), linear-gradient(90deg, ${colors.accent}40 1px, transparent 1px)`,
            }}
          />
          <div
            className="pointer-events-none absolute inset-0 rounded-xl"
            style={{
              background:
                "linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0) 50%)",
            }}
          />
          <div className="absolute top-0 right-0 left-0 h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent" />
          <div
            className="absolute -top-12 -right-12 h-32 w-32 rounded-full blur-3xl"
            style={{
              backgroundColor: colors.accent,
              opacity: 0.15,
            }}
          />

          {/* Dark overlay when tilted */}
          <div
            className="pointer-events-none absolute inset-0 rounded-xl"
            style={{
              backgroundColor: "rgba(0,0,0,0.6)",
              opacity: hovered ? 1 : 0,
              transition: "opacity 0.5s",
              zIndex: 15,
            }}
          />

          {/* Card content */}
          <div
            className="relative z-10 flex h-full flex-col justify-between p-4"
            style={{
              opacity: hovered ? 0.2 : 1,
              transition: "opacity 0.5s",
            }}
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-[10px] font-bold tracking-[0.2em] text-white/55 uppercase">
                  {card.bank}
                </p>
                <h3 className="mt-0.5 text-sm font-bold tracking-wide text-white">
                  {card.card_name}
                </h3>
              </div>
              <StatusIcon className="h-5 w-5 text-white/60" />
            </div>

            <div className="flex items-center gap-2.5">
              {[0, 1, 2].map((g) => (
                <div key={g} className="flex gap-[3px]">
                  {[0, 1, 2, 3].map((d) => (
                    <div key={d} className="h-[4px] w-[4px] rounded-full bg-white/30" />
                  ))}
                </div>
              ))}
              <span
                className="ml-1.5 text-base font-bold tracking-[0.2em]"
                style={{ color: colors.accent, fontVariantNumeric: "tabular-nums" }}
              >
                {card.last4}
              </span>
            </div>

            <div className="flex items-end justify-between">
              <div className="flex-1">
                {settled && stmt ? (
                  <>
                    <p className="text-[10px] font-bold tracking-widest text-white/60 uppercase">
                      Current Spend
                    </p>
                    <p
                      className="text-base font-bold text-white"
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {card.unbilled.count > 0 ? autoFormatCurrency(card.unbilled.total) : "\u2014"}
                    </p>
                  </>
                ) : stmt ? (
                  <>
                    <p className="text-[10px] font-bold tracking-widest text-white/60 uppercase">
                      {state === "overdue"
                        ? "Overdue"
                        : state === "not_confirmed"
                          ? "Not confirmed"
                          : "Amount Due"}
                    </p>
                    <p
                      className="text-base font-bold text-white"
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {autoFormatCurrency(remainingDue(card))}
                    </p>
                  </>
                ) : (
                  <p className="text-[10px] text-white/30">No statements</p>
                )}
              </div>
              <div className="text-right">
                {stmt && settled && card.statement_day ? (
                  <>
                    <p className="text-[10px] font-bold tracking-widest text-white/60 uppercase">
                      Next Stmt
                    </p>
                    <p className="text-[11px] font-semibold text-white/70">
                      {nextStmt(card.statement_day)}
                    </p>
                  </>
                ) : stmt ? (
                  <>
                    <p className="text-[10px] font-bold tracking-widest text-white/60 uppercase">
                      {state === "overdue" ? "Was Due" : "Due"}
                    </p>
                    <p className="text-[11px] font-semibold text-white/70">
                      {fmtDue(stmt.due_date)}
                      <EstMarker
                        card={card}
                        className="ml-1 text-[9px] font-medium text-white/40"
                      />
                    </p>
                  </>
                ) : null}
              </div>
            </div>

            <div className="-mt-1 flex items-end justify-end">
              <NetworkLogo network={card.network} className="h-4 w-8" />
            </div>
          </div>
        </div>

        {/* Layer 3: Physical card — contained within bounds */}
        <div
          className="absolute z-20"
          style={{
            width: "60%",
            bottom: "15%",
            left: "50%",
            opacity: hovered ? 1 : 0,
            transition: "all 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
            transform: hovered
              ? "translateX(-50%) translate3d(0, -8%, 100px)"
              : "translateX(-50%) translate3d(0, 0, 0)",
            filter: hovered ? "drop-shadow(0 15px 30px rgba(0,0,0,0.6))" : "none",
            maxHeight: "82%",
          }}
        >
          <PhysicalCardReplica card={card} colors={colors} />
        </div>
      </div>
    </Link>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Summary Panel — Compact Upcoming Payments
   ═══════════════════════════════════════════════════════════════════════════ */

type SortKey = "due" | "amount" | "status";

function SummaryPanel({ cards }: { cards: CardSummary[] }) {
  const [sortBy, setSortBy] = useState<SortKey>("due");
  // iter-18 contract 3: Total Due = remaining_due over unpaid/partial/overdue cards (not the
  // gross total minus payments); Paid This Cycle only counts current-cycle statements, so
  // dormant cards' months-old payments no longer inflate it (audit M11).
  const summary = summarizeCards(cards);
  const totalUnbilled = cards.reduce((s, c) => s + c.unbilled.total, 0);
  const unbilledTxns = cards.reduce((s, c) => s + c.unbilled.count, 0);
  // Not-confirmed bills are listed (so they can be checked) but not added to Total Due.
  const upcomingDues = cards
    .filter((c) => c.latest_statement)
    .map((c) => ({
      card: c,
      state: getState(c),
      remaining: remainingDue(c),
      daysLeft: daysUntil(c.latest_statement!.due_date),
    }))
    .filter(({ state }) => isOwing(state) || state === "not_confirmed");
  const sortedDues = [...upcomingDues].sort((a, b) => {
    const da = a.daysLeft ?? Number.POSITIVE_INFINITY;
    const db = b.daysLeft ?? Number.POSITIVE_INFINITY;
    if (sortBy === "due") return da - db;
    if (sortBy === "amount") return b.remaining - a.remaining;
    return STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state) || da - db;
  });
  const allClear = summary.owingCount === 0 && summary.notConfirmedCount === 0;
  const pendingParts = [
    summary.owingCount > 0
      ? `${summary.owingCount} card${summary.owingCount !== 1 ? "s" : ""} pending`
      : null,
    summary.notConfirmedCount > 0 ? `${summary.notConfirmedCount} not confirmed` : null,
  ].filter(Boolean);
  const staleStatements = cards.length - summary.currentCycleCount;

  return (
    <div className="mb-8 space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <GradientStatCard
          title="Total Due"
          value={summary.totalDue}
          sub={allClear ? "All bills cleared" : pendingParts.join(" · ")}
          icon={allClear ? ShieldCheck : Banknote}
          gradientKey={allClear ? "shield" : "red"}
          delay={0}
        />
        <GradientStatCard
          title="Paid This Cycle"
          value={summary.paidThisCycle}
          sub={`${summary.currentCycleSettledCount} of ${summary.currentCycleCount} current statement${summary.currentCycleCount !== 1 ? "s" : ""} cleared`}
          icon={Wallet}
          gradientKey="green"
          delay={1}
        />
        <GradientStatCard
          title="Unbilled Spend"
          value={totalUnbilled}
          sub={`${unbilledTxns} transaction${unbilledTxns !== 1 ? "s" : ""} this cycle`}
          icon={Activity}
          gradientKey="blue"
          delay={2}
        />
        {/* Counts statements of the current cycle only: a dormant card whose last statement
            is from March is not "received" this cycle. */}
        <GradientStatCard
          title="Statements"
          value={summary.currentCycleCount}
          displayText={`${summary.currentCycleCount} / ${cards.length}`}
          sub={
            staleStatements > 0
              ? `${staleStatements} card${staleStatements !== 1 ? "s" : ""} with no statement in 45 days`
              : "Every card has a current statement"
          }
          icon={TrendingUp}
          gradientKey="purple"
          delay={3}
          isCurrency={false}
        />
      </div>

      {sortedDues.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.5, ease: [0.25, 1, 0.5, 1] }}
          className="rounded-2xl border p-5"
          style={{ borderColor: "var(--border)", backgroundColor: "var(--card)" }}
        >
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CalendarClock className="h-4 w-4 text-white/40" />
              <span className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
                Upcoming Payments
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <ArrowUpDown className="h-3 w-3 text-white/30" />
              <select
                aria-label="Sort credit cards by"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortKey)}
                className="cursor-pointer appearance-none rounded-lg border px-1.5 py-0.5 text-[11px] font-medium"
                style={{
                  color: "var(--foreground)",
                  backgroundColor: "var(--card)",
                  borderColor: "var(--border)",
                }}
              >
                <option value="due">Due Date</option>
                <option value="amount">Amount</option>
                <option value="status">Status</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            {sortedDues.map(({ card: c, state: st, remaining, daysLeft }, i) => {
              const scfg = STATE_CONFIG[st];
              const cstmt = c.latest_statement!;
              const cardColors = getCardColors(c.bank, c.card_name);
              const dueText =
                st === "overdue"
                  ? daysLeft !== null && daysLeft < 0
                    ? `${-daysLeft}d overdue`
                    : "Overdue"
                  : isOwing(st) && daysLeft === 0
                    ? "Due today"
                    : isOwing(st) && daysLeft !== null && daysLeft > 0 && daysLeft <= 3
                      ? `${daysLeft}d left`
                      : `Due ${fmtDue(cstmt.due_date)}`;
              return (
                <motion.div
                  key={c.id}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.3, delay: 0.5 + i * 0.06, ease: [0.25, 1, 0.5, 1] }}
                >
                  <Link href={`/cards/${c.id}`} className="block">
                    <div
                      className="group flex items-center gap-2.5 rounded-xl p-2.5 transition-colors"
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLDivElement).style.backgroundColor =
                          "var(--card-hover)";
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLDivElement).style.backgroundColor = "transparent";
                      }}
                    >
                      <div
                        className="w-1 flex-shrink-0 self-stretch rounded-full"
                        style={{
                          background: `linear-gradient(to bottom, ${cardColors.accent}, ${cardColors.accent}60)`,
                        }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span
                            className="truncate text-sm font-semibold"
                            style={{ color: "var(--foreground)" }}
                          >
                            {c.card_name}
                          </span>
                          <span
                            className="flex-shrink-0 font-mono text-[10px]"
                            style={{ color: "var(--text-muted)" }}
                          >
                            {c.last4}
                          </span>
                          <span
                            className={`inline-flex items-center gap-0.5 rounded-full px-1 py-0.5 text-[10px] font-semibold ${scfg.bg} ${scfg.color} flex-shrink-0`}
                          >
                            <scfg.Icon className="h-2.5 w-2.5" />
                            {scfg.label}
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-1.5">
                          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                            {dueText}
                            <EstMarker card={c} className="ml-0.5 opacity-70" />
                          </span>
                          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                            &middot;
                          </span>
                          <span
                            className="font-mono text-[10px]"
                            style={{ color: "var(--text-muted)" }}
                          >
                            {fmtDateRange(cstmt.billing_start, cstmt.billing_end)}
                          </span>
                        </div>
                      </div>
                      <div className="flex-shrink-0 text-right">
                        <p
                          className="text-sm font-bold"
                          style={{
                            // Not-confirmed amounts are shown muted: they are not in Total Due.
                            color:
                              st === "not_confirmed" ? "var(--text-muted)" : "var(--foreground)",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {autoFormatCurrency(remaining)}
                        </p>
                        {c.payment && c.payment.total > 0 && (
                          <p className="text-[10px] text-white/35">
                            {autoFormatCurrency(c.payment.total)} paid
                          </p>
                        )}
                      </div>
                      <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-white/25 opacity-0 transition-opacity group-hover:opacity-100" />
                    </div>
                  </Link>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Mobile Card Carousel
   ═══════════════════════════════════════════════════════════════════════════ */

function CardCarousel({ cards }: { cards: CardSummary[] }) {
  const [requested, setActive] = useState(0);
  const touchStartY = useRef(0);
  const touchDeltaY = useRef(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const total = cards.length;
  // Always a card that exists: a filter that leaves fewer cards used to keep the old index and
  // hide every card (iteration 203 E2 B5). The parent also remounts this per filter.
  const active = clampIndex(requested, total);
  const goTo = (idx: number) => {
    if (idx < 0) idx = 0;
    if (idx >= total) idx = total - 1;
    setActive(idx);
  };
  const onTouchStart = (e: React.TouchEvent) => {
    touchStartY.current = e.touches[0].clientY;
    touchDeltaY.current = 0;
  };
  const onTouchMoveHandler = (e: React.TouchEvent) => {
    e.preventDefault();
    touchDeltaY.current = e.touches[0].clientY - touchStartY.current;
  };
  const onTouchEndHandler = () => {
    const threshold = 50;
    if (touchDeltaY.current < -threshold) goTo(active + 1);
    else if (touchDeltaY.current > threshold) goTo(active - 1);
    touchDeltaY.current = 0;
  };
  const getCardStyle = (index: number): React.CSSProperties => {
    const diff = index - active;
    const absDiff = Math.abs(diff);
    const base = {
      position: "absolute" as const,
      top: "50%",
      left: "50%",
      width: "85%",
      transition: "all 0.8s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
    };
    if (absDiff > 2)
      return {
        ...base,
        opacity: 0,
        pointerEvents: "none",
        transform: "translate(-50%, -50%) scale(0.7) translateZ(-500px)",
        zIndex: 0,
      };
    if (diff === 0)
      return {
        ...base,
        opacity: 1,
        pointerEvents: "auto",
        transform: "translate(-50%, -50%) scale(1.1) translateZ(0)",
        zIndex: 10,
      };
    if (absDiff === 1)
      return {
        ...base,
        opacity: 0.6,
        pointerEvents: "none",
        transform: `translate(-50%, calc(-50% + ${diff * 150}px)) scale(0.9) translateZ(-100px)`,
        zIndex: 5,
      };
    return {
      ...base,
      opacity: 0.3,
      pointerEvents: "none",
      transform: `translate(-50%, calc(-50% + ${diff * 200}px)) scale(0.8) translateZ(-300px)`,
      zIndex: 2,
    };
  };

  return (
    <div className="relative">
      <div
        ref={containerRef}
        className="relative overflow-hidden"
        style={{ height: "320px", perspective: "1200px", touchAction: "none" }}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMoveHandler}
        onTouchEnd={onTouchEndHandler}
      >
        {cards.map((card, idx) => (
          <div key={card.id} style={getCardStyle(idx)}>
            <CardTile3D card={card} />
          </div>
        ))}
      </div>
      <div className="mt-4 flex items-center justify-center gap-4">
        <button
          aria-label="Previous card"
          onClick={() => goTo(active - 1)}
          disabled={active === 0}
          className="rounded-full border p-2 transition-colors disabled:opacity-30"
          style={{ borderColor: "var(--border)", color: "var(--foreground)" }}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div className="flex items-center gap-1.5">
          {cards.map((_, idx) => (
            <button
              key={idx}
              aria-label={`Show card ${idx + 1}`}
              onClick={() => setActive(idx)}
              className="rounded-full transition-all"
              style={{
                width: idx === active ? "20px" : "6px",
                height: "6px",
                backgroundColor:
                  idx === active
                    ? getCardColors(cards[idx].bank, cards[idx].card_name).accent
                    : "var(--text-muted)",
                opacity: idx === active ? 1 : 0.4,
                transition: "all 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
              }}
            />
          ))}
        </div>
        <button
          aria-label="Next card"
          onClick={() => goTo(active + 1)}
          disabled={active === total - 1}
          className="rounded-full border p-2 transition-colors disabled:opacity-30"
          style={{ borderColor: "var(--border)", color: "var(--foreground)" }}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Main — with dot background
   ═══════════════════════════════════════════════════════════════════════════ */

export default function CreditCards() {
  const [rawCards, setRawCards] = useState<CardSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<CardState | "all">("all");

  // An Ingest outage is an error with Retry, not "No cards found" (iteration 203 E2 M11).
  const fetchData = useCallback(async () => {
    try {
      const data = await getJson("/api/cards");
      setRawCards(data.cards || []);
      setError(null);
    } catch (e) {
      console.error("Failed to fetch cards", e);
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const retry = () => {
    setLoading(true);
    setError(null);
    fetchData();
  };

  const cards = mergeCards(rawCards);
  const statesPresent = new Set(cards.map((c) => getState(c)));
  const activeStates = STATE_ORDER.filter((s) => statesPresent.has(s));

  const filtered = filter === "all" ? cards : cards.filter((c) => getState(c) === filter);
  const sorted = [...filtered].sort(
    (a, b) =>
      STATE_ORDER.indexOf(getState(a)) - STATE_ORDER.indexOf(getState(b)) ||
      a.bank.localeCompare(b.bank)
  );

  return (
    <div className="relative min-h-screen">
      {/* Dot background pattern */}
      <div className="absolute inset-0 [background-image:radial-gradient(#404040_1px,transparent_1px)] [background-size:20px_20px] dark:[background-image:radial-gradient(#252525_1px,transparent_1px)]" />
      <div className="pointer-events-none absolute inset-0 bg-[var(--background)] [mask-image:radial-gradient(ellipse_at_center,transparent_20%,black)]" />

      <div className="relative z-10 mx-auto max-w-[1400px]">
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: [0.25, 1, 0.5, 1] }}
          className="mb-8"
        >
          <h1
            className="text-3xl font-bold tracking-tight sm:text-4xl"
            style={{ color: "var(--foreground)" }}
          >
            Credit Cards
          </h1>
          <p className="mt-1 text-sm" style={{ color: "var(--foreground-secondary)" }}>
            Track and manage your cards in one place
          </p>
        </motion.div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-8 w-8 animate-spin text-white/30" />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={retry} />
        ) : cards.length === 0 ? (
          <div className="py-20 text-center">
            <CreditCardIcon className="mx-auto mb-4 h-16 w-16 text-white/20" />
            <h3 className="text-xl" style={{ color: "var(--foreground-secondary)" }}>
              No cards found
            </h3>
          </div>
        ) : (
          <>
            <SummaryPanel cards={cards} />

            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.4, delay: 0.3 }}
              className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <h2 className="text-xl font-semibold" style={{ color: "var(--foreground)" }}>
                My Cards
              </h2>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => setFilter("all")}
                  className={`rounded-full px-4 py-2 text-xs font-semibold transition-all ${filter === "all" ? "bg-[var(--foreground)] text-[var(--background)] shadow-sm" : "border border-[var(--border)] bg-[var(--card)] text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)]"}`}
                >
                  All Cards
                </button>
                {activeStates.map((s) => {
                  const c = STATE_CONFIG[s];
                  return (
                    <button
                      key={s}
                      onClick={() => setFilter(s)}
                      className={`flex items-center gap-1.5 rounded-full px-4 py-2 text-xs font-semibold transition-all ${filter === s ? "bg-[var(--foreground)] text-[var(--background)] shadow-sm" : "border border-[var(--border)] bg-[var(--card)] text-[var(--foreground-secondary)] hover:bg-[var(--card-hover)]"}`}
                    >
                      <c.Icon className="h-3.5 w-3.5" />
                      {c.label}
                    </button>
                  );
                })}
              </div>
            </motion.div>

            <div className="block md:hidden">
              {/* remounted per filter so it starts on the first card (E2 B5) */}
              <CardCarousel key={filter} cards={sorted} />
            </div>

            {/* Extra top padding so tilted cards don't overlap heading/buttons */}
            <div className="hidden gap-x-5 gap-y-10 pt-6 md:grid md:grid-cols-5">
              {sorted.map((card, i) => (
                <motion.div
                  key={card.id}
                  initial={{ opacity: 0, y: 24 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5, delay: 0.4 + i * 0.07, ease: [0.25, 1, 0.5, 1] }}
                >
                  <CardTile3D card={card} />
                </motion.div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
