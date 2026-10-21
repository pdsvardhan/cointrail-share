# Expenses Redesign JIG (Session F Prep)

Reference kit for redesigning the Expenses dashboard in Figma or equivalent design tool.

## Current Structure (as of Iteration 27)

### File: `components/expenses/ExpenseTracker.tsx` (~2477 lines, 5 tabs)

**Tab bar:** Overview · Categories · Food · Shopping · Trends

### OverviewTab (`line 645`)

1. **Hero row:** 5 `GradientStatCard` (Total / Daily Avg / Highest Day / Transactions / Top Category)
2. **Row 2:** Donut chart (Spending Breakdown) + Daily Spending bars (clickable to open day panel)
3. **Row 3:** Heatmap (calendar, 6 months) with 3fr:1fr right sidebar (Monthly Summary → Day Transactions Panel)
4. **Row 4:** Biggest Transactions (left) + Recurring Payments list (right)

### CategoriesTab (`line 1239`)

1. Compact ranking bars (top 12 categories, inline progress bars)
2. Category card grid (3 cols), each expands to show:
   - Merchant cards (with logo, count, share %)
   - Merchant click → Deep drill-down (monthly trend, recent txns with enrichment)

### FoodTab (`line 1988`)

- 4 stat cards: Total Food / Orders / Top Restaurant / Restaurants count
- **Search + sort** (spend / orders / avg)
- Restaurant cards (3 cols) with domain logos, expandable to show Swiggy item-level detail

### ShoppingTab (`line 2173`)

- 4 stat cards: Total Shopping / Purchases / Top Merchant / Merchants count
- **Search + sort** (spend / buys / avg)
- Merchant cards (1 col), expandable to show Amazon product list + order IDs

### TrendsTab (`line 1710`)

- Monthly spend area chart (12 months)
- Multi-line category trends (toggleable)
- Day-of-week bar chart + Top merchants horizontal bar chart

## Design Tokens

- **Palette:** CSS vars in `app/globals.css` — `--foreground`, `--background`, `--card`, `--border`, `--accent-blue`, `--accent-amber`, `--accent-emerald`, etc.
- **Category colors:** `lib/constants.ts` — `CATEGORY_COLORS` (20+ distinct hues per category)
- **Chart palette:** `CHART_COLORS` array (blue → violet → cyan → emerald → amber → red → pink → teal → orange → lime)
- **Typography:** Inter (body), Outfit (currency/numbers), JetBrains Mono (monospace)
- **Glass effect:** `.glass-card` utility class (backdrop-blur + transparent bg + border)

## Component Patterns (reuse from Credit Cards page)

Reference for visual consistency — see `components/cards/CardList.tsx`:

- **3D tilt stat cards** (mouse-tracking perspective transform)
- **Gradient banner headers** per card
- **Inline SVG network logos** (Visa/MC/RuPay/Amex chips)
- **Metallic silver/grey icon palette** for status indicators
- **Tilted text fade on hover** (3D cube edge reveal)

## Figma MCP Prompts (when ready)

Prompts to run with the Figma MCP when user is ready for wireframes:

1. "Design the Expenses Overview tab: 5 hero stat cards (glassmorphism, mouse-track 3D), donut chart with floating radial gradient segments and hover glow, adjacent daily-spend bars that become amber when clicked, below them a 6-month calendar heatmap with right sidebar that swaps between monthly summary and clicked-day transactions. Dark theme, emerald + blue accents."

2. "Design the Food tab: 4 stat cards at top (Utensils/Hash/Flame/Activity icons), search bar + Spend/Orders/Avg toggle, below a 3-column restaurant grid with circular logos (Swiggy/Zomato favicons), each expands to show monthly orange bars + Swiggy item-level enrichment (item names from email parser)."

3. "Design mobile version — collapse 5 stat cards to horizontal swipe carousel, tabs become bottom nav, heatmap scrolls horizontally, category cards full-width stack."

## Session F Status

- JIG doc: ✓ this file
- Figma wireframe generation: **deferred** — needs user present to iterate on designs

## Known Gaps (for Session F or Step 4)

- No responsive testing done on actual mobile device yet (pending Android app build)
- Recharts' built-in tooltip positioning occasionally clips on narrow viewports
- Daily bar click handler uses Recharts Bar onClick — verify mobile tap behavior differs from hover
