import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

// iter-18 CH2: eslint-config-next is pinned to the 15.x line to match next@15.
// 15.x ships eslintrc-style presets (flat configs only arrived in 16), so they are
// loaded through FlatCompat, as in the create-next-app@15 template.
const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  {
    ignores: ["node_modules/**", ".next/**", "data/**", "public/**", "android/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-explicit-any": "warn",
      "react/no-unescaped-entities": "off",
    },
  },
];

export default config;
