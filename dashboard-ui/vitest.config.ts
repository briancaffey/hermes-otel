import { defineConfig } from "vitest/config";

// Pure helpers run in node; the page smoke tests need a DOM and the host SDK
// shim (test/sdk-shim.ts) installed before `src/sdk.ts` reads window.
export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./test/sdk-shim.ts"],
    include: ["test/**/*.test.ts", "test/**/*.test.tsx"],
  },
  esbuild: { jsx: "transform", jsxFactory: "React.createElement", jsxFragment: "React.Fragment" },
});
