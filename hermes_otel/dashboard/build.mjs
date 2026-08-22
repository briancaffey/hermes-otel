// Dev-time build for the hermes-otel dashboard tab.
//
// Output is a CLASSIC-SCRIPT IIFE at dist/index.js (the Hermes web server
// injects it via <script>, NOT type=module — see usePlugins.ts). React and the
// UI components are taken from window.__HERMES_PLUGIN_SDK__ at runtime (each
// TSX file imports them from ./sdk), so we never bundle our own React.
//
// Usage:  npm install && npm run build   (only contributors run this;
//         the committed dist/index.js is what ships with the plugin.)

import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");

/** @type {import('esbuild').BuildOptions} */
const opts = {
  entryPoints: ["src/index.tsx"],
  bundle: true,
  format: "iife", // classic script — the host does not use type=module
  outfile: "dist/index.js",
  platform: "browser",
  target: ["es2019"],
  jsx: "transform",
  jsxFactory: "React.createElement",
  jsxFragment: "React.Fragment",
  minify: !watch,
  sourcemap: watch ? "inline" : false,
  legalComments: "none",
  logLevel: "info",
  banner: {
    js: "/* hermes-otel dashboard — built from dashboard/src (esbuild). Edit the TSX, not this file. */",
  },
};

if (watch) {
  const ctx = await esbuild.context(opts);
  await ctx.watch();
  console.log("watching dashboard/src …");
} else {
  await esbuild.build(opts);
  console.log("built dist/index.js");
}
