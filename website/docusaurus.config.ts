import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'hermes-otel',
  tagline: 'OpenTelemetry tracing for Hermes Agent — Phoenix, Langfuse, LangSmith, SigNoz, Jaeger, Tempo',
  favicon: 'img/favicon.svg',

  url: 'https://briancaffey.github.io',
  baseUrl: '/hermes-otel/',

  organizationName: 'briancaffey',
  projectName: 'hermes-otel',
  trailingSlash: false,

  onBrokenLinks: 'warn',

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  themes: [
    '@docusaurus/theme-mermaid',
    [
      require.resolve('@easyops-cn/docusaurus-search-local'),
      {
        hashed: true,
        language: ['en'],
        indexBlog: false,
        docsRouteBasePath: '/',
        highlightSearchTermsOnTargetPage: true,
      },
    ],
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/briancaffey/hermes-otel/edit/main/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/hermes-otel-banner.png',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    docs: {
      sidebar: {
        hideable: true,
        autoCollapseCategories: true,
      },
    },
    navbar: {
      title: 'hermes-otel',
      logo: {
        alt: 'hermes-otel',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docs',
          position: 'left',
          label: 'Docs',
        },
        {
          to: '/getting-started/quickstart',
          label: 'Quickstart',
          position: 'left',
        },
        {
          to: '/backends/overview',
          label: 'Backends',
          position: 'left',
        },
        {
          href: 'https://github.com/nousresearch/hermes-agent',
          label: 'Hermes Agent',
          position: 'right',
        },
        {
          href: 'https://github.com/briancaffey/hermes-otel',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Quickstart', to: '/getting-started/quickstart'},
            {label: 'Installation', to: '/getting-started/installation'},
            {label: 'Configuration', to: '/configuration/overview'},
            {label: 'Architecture', to: '/architecture/span-hierarchy'},
          ],
        },
        {
          title: 'Backends',
          items: [
            {label: 'Phoenix', to: '/backends/phoenix'},
            {label: 'Langfuse', to: '/backends/langfuse'},
            {label: 'LangSmith', to: '/backends/langsmith'},
            {label: 'SigNoz', to: '/backends/signoz'},
            {label: 'Jaeger', to: '/backends/jaeger'},
            {label: 'Grafana Tempo', to: '/backends/tempo'},
          ],
        },
        {
          title: 'More',
          items: [
            {label: 'GitHub', href: 'https://github.com/briancaffey/hermes-otel'},
            {label: 'Issues', href: 'https://github.com/briancaffey/hermes-otel/issues'},
            {label: 'Hermes Agent', href: 'https://github.com/nousresearch/hermes-agent'},
            {label: 'OpenTelemetry', href: 'https://opentelemetry.io'},
          ],
        },
      ],
      copyright: `Apache 2.0 · Built by <a href="https://github.com/briancaffey">Brian Caffey</a> · ${new Date().getFullYear()}`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'json', 'python', 'toml', 'ini', 'docker'],
    },
    mermaid: {
      theme: {light: 'neutral', dark: 'dark'},
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
