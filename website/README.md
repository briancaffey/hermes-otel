# hermes-otel docs site

Docusaurus v3.10 site published at https://briancaffey.github.io/hermes-otel.

## Local development

```bash
cd website
npm install
npm run start           # http://localhost:3000/hermes-otel/
```

## Build

```bash
npm run build           # outputs to website/build/
npm run serve           # serve the built site locally
```

## Deployment

Deployed by GitHub Actions (`.github/workflows/deploy-site.yml`) on every push to `main` that touches `website/**`. The workflow builds the static site and publishes to GitHub Pages.

## Structure

- `docs/` — all docs content (markdown + MDX), sidebar defined in `sidebars.ts`
- `src/css/custom.css` — theme customization (amber-on-dark matching Hermes Agent)
- `src/pages/` — custom React pages (currently none)
- `static/img/` — logo, favicon, other static assets
- `docusaurus.config.ts` — site config (URLs, navbar, footer, plugins)

## Adding a new doc page

1. Drop a `.md` file into the appropriate `docs/<section>/` directory with frontmatter:
   ```markdown
   ---
   sidebar_position: 3
   title: "My Page"
   description: "One-line description for SEO."
   ---
   ```
2. Add an entry to `sidebars.ts`.
3. `npm run start` and check it renders.
