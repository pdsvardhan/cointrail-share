# Design Tokens — Cointrail

> Last updated: 2026-04-02 (Iteration 10)

## Color Palette

### Semantic

- **Success (paid):** `#34d399` (emerald-400) / `#10b981` (emerald-500)
- **Warning (unpaid):** `#fbbf24` (amber-400) / `#f59e0b` (amber-500)
- **Urgent (due-soon):** `#fb923c` (orange-400) / `#f97316` (orange-500)
- **Error (overdue):** `#f87171` (red-400) / `#ef4444` (red-500)
- **Info:** `#60a5fa` (blue-400) / `#3b82f6` (blue-500)

### Backgrounds (Light Mode)

- **Page:** `#f8f9fc`
- **Card:** `#ffffff`
- **Card hover:** `#f1f5f9`
- **Border:** `#e2e8f0`
- **Border subtle:** `#f1f5f9`

### Backgrounds (Dark Mode)

- **Page:** `#06060b`
- **Card:** `#0e0e18`
- **Card hover:** `#14141f`
- **Border:** `#1c1c2e`
- **Border subtle:** `#141422`

### Credit Card Colors (Metallic Dark)

| Card             | Base Gradient                 | Accent    | Inspired By          |
| ---------------- | ----------------------------- | --------- | -------------------- |
| HDFC Millennia   | `#0c1b3a → #162d5a → #0a1628` | `#c8a961` | Navy + gold chip     |
| HDFC Swiggy      | `#4a1a00 → #7a2e0a → #3a1200` | `#ff6b2c` | Swiggy orange        |
| HDFC Tata Neu    | `#3a0a2e → #5c1848 → #2a0620` | `#e84393` | Tata Neu magenta     |
| SBI AURUM        | `#1a1400 → #2d2200 → #0f0c00` | `#d4a843` | Black + gold premium |
| SBI Cashback     | `#1a1e2e → #252a3a → #12151f` | `#8892a8` | Slate + silver       |
| ICICI Amazon Pay | `#0a1520 → #142535 → #060e18` | `#ff9900` | Amazon orange        |
| ICICI Sapphiro   | `#0a2a2a → #0f3d3d → #061e1e` | `#2dd4bf` | Teal metallic        |
| HSBC Platinum    | `#2a2a2e → #3a3a40 → #1a1a1e` | `#a0a0b0` | Platinum silver      |
| Axis Airtel      | `#3a0a0a → #5c1515 → #2a0505` | `#ef4444` | Airtel red           |

## Typography

### Font Families

- **Primary:** Plus Jakarta Sans (weights: 300-800)
- **Mono:** JetBrains Mono (weights: 400-600) — for amounts, card numbers, dates
- **CDN:** Google Fonts

### Size Scale (approximate)

| Name | Size    | Usage                                 |
| ---- | ------- | ------------------------------------- |
| xs   | 10px    | card labels, tracking-wider uppercase |
| sm   | 12-13px | secondary text, subtitles             |
| base | 14px    | body, descriptions                    |
| lg   | 18px    | card titles, section headers          |
| xl   | 20-24px | amounts on cards                      |
| 2xl  | 30-36px | page titles                           |

## Border Radius

- **Small:** 8px (badges, buttons)
- **Medium:** 12px (cards, inputs, stat cards)
- **Large:** 16px (credit card tiles, panels)
- **Round:** 9999px (pill filters, avatars)

## Shadows

- **Card (light):** `0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03)`
- **Card hover (light):** `0 4px 16px rgba(0,0,0,0.08), 0 8px 32px rgba(0,0,0,0.04)`
- **Card (dark):** `0 1px 3px rgba(0,0,0,0.3), 0 4px 12px rgba(0,0,0,0.2)`
- **Credit cards:** shadow-2xl (Tailwind) + hover:shadow-3xl

## Animation

- **Card tilt:** spring (stiffness: 300, damping: 30) via motion/react
- **Hover overlay:** opacity 0.2s + content slide-up 0.3s with 0.2s delay
- **Content fade:** opacity 0.15s on hover
- **Card icon spin:** rotate 360° over 0.6s on hover
- **Easing:** ease-out for CSS, spring for motion
- **Anti-pattern:** Don't use initial={{ opacity: 0 }} in motion.div (breaks SSR)

## Component Patterns

### Credit Card Tiles

- **Style:** Metallic dark gradient + grid texture overlay + accent corner glow
- **Height:** h-56 (224px)
- **Grid:** 4 columns on xl, 2 on md, 1 on mobile
- **Hover:** 3D tilt (±15°) + glare + shimmer + overlay with details
- **Click:** Navigate to /cards/[id]
- **Status:** Icon-only at bottom (no text badge on card)

### Stat Cards

- **Style:** White card (light) / dark card (dark) + colored icon bubble
- **Layout:** 3 columns, salary-cycle-based stats
- **Content:** Still Owe, Paid This Cycle, Statements (X/Y)

### Filters

- **Style:** Pill buttons (rounded-full)
- **Active:** bg-foreground text-background
- **States shown:** Only states present in data, with icon

## Card States

| State    | Color   | Icon         | Meaning                                   |
| -------- | ------- | ------------ | ----------------------------------------- |
| Paid     | emerald | CheckCircle2 | Statement fully paid                      |
| Unpaid   | amber   | CircleDashed | Statement received, not paid, not overdue |
| Due Soon | orange  | Clock        | Due within 3 days                         |
| Overdue  | red     | AlertCircle  | Past due date                             |

## Design Decisions

| Date       | Decision                       | Reason                                                       |
| ---------- | ------------------------------ | ------------------------------------------------------------ |
| 2026-04-02 | Metallic dark cards            | User preferred dark premium feel over light cards            |
| 2026-04-02 | Per-card unique gradients      | Avoid monotone blues — each card should be visually distinct |
| 2026-04-02 | Grid texture overlay           | Creates metallic feel using accent color at 6% opacity       |
| 2026-04-02 | Salary cycle stats (24th-23rd) | User pays all CCs after salary on 24th                       |
| 2026-04-02 | No network badge on cards      | VISA/MC/RuPay not useful to user                             |
| 2026-04-02 | Click=navigate, no flip        | User wants direct navigation to card detail                  |
| 2026-04-02 | Light mode default             | Reference design was light; user has dark/light toggle       |
| 2026-04-02 | Plus Jakarta Sans font         | Distinctive fintech feel, replacing Inter                    |
| 2026-04-02 | Remove Monthly Overview        | User doesn't need month comparison, just owe/paid/upcoming   |
