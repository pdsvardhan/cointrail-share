// k6 read-only smoke test for Cointrail's public API surface.
// Run: k6 run k6/smoke.js
// Pass-criteria: p(95) < 500ms for read endpoints; 0 errors.
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "30s",
  thresholds: {
    http_req_duration: ["p(95)<500"],
    http_req_failed: ["rate<0.01"],
  },
};

const BASE = __ENV.BASE_URL || "http://localhost:8501";

const READ_PATHS = [
  "/",
  "/api/cards",
  "/api/recurring",
  "/api/expenses?limit=1",
  "/api/expenses/dashboard",
  "/api/expenses/dashboard/heatmap?months=3",
];

export default function () {
  for (const p of READ_PATHS) {
    const res = http.get(`${BASE}${p}`);
    check(res, {
      [`${p} status<500`]: (r) => r.status < 500,
    });
  }
  sleep(1);
}
