# Dependency advisories — status

Last checked 2026-10-20 (iteration 198, CH2) with `npm audit --omit=dev`.

## Fixed in iteration 198

- `next` 15.5.14 → 15.5.28 (latest 15.5 patch): critical DoS / middleware advisories.
- `@capacitor/android`, `@capacitor/core`, `@capacitor/cli` 8.3.1 → 8.5.3: remote content could load at the app origin. The APK must be rebuilt for the phone to get this.
- `sharp` 0.34.5 → ^0.35.5 via `overrides` in package.json. Next 15.5.28 declares `sharp: ^0.34.3 || ^0.35.5`, so this stays inside the range Next supports.
- Docker base image `node:20-slim` (end of life 2026-04-30) → `node:22-slim`.

## Known residual (accepted until the Next 16 upgrade)

- `postcss` 8.4.31 is pinned exactly by `next@15.5.28` (`dependencies.postcss = "8.4.31"`), and `next` itself is flagged moderate through it. No `overrides` fix is safe: Next's build expects that exact version. Clearing it means moving to Next 16, a major framework upgrade that needs its own iteration.
- Exposure: postcss runs only at build time on this repo's own CSS, never on user input, so the advisory is not reachable at runtime.
