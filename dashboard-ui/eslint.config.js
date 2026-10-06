// ESLint flat config for the dashboard tab. `npm run lint` runs in CI next to
// `tsc` and vitest. The rules are deliberately few: the type checker catches
// most mistakes, this catches the ones it cannot (unused code, no `any`
// creep beyond the SDK boundary, no console in pages).
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["node_modules/**", "../hermes_otel/dashboard/dist/**"] },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.ts", "src/**/*.tsx", "test/**/*.ts", "test/**/*.tsx"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-console": ["error", { allow: ["error", "warn"] }],
      "prefer-const": "error",
      eqeqeq: ["error", "smart"],
    },
  }
);
