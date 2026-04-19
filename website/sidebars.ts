import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'index',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/quickstart',
        'getting-started/installation',
        'getting-started/concepts',
      ],
    },
    {
      type: 'category',
      label: 'Backends',
      collapsed: false,
      items: [
        'backends/overview',
        'backends/phoenix',
        'backends/langfuse',
        'backends/langsmith',
        'backends/signoz',
        'backends/jaeger',
        'backends/tempo',
        'backends/otlp',
        'backends/multi-backend',
      ],
    },
    {
      type: 'category',
      label: 'Configuration',
      collapsed: true,
      items: [
        'configuration/overview',
        'configuration/yaml',
        'configuration/environment-variables',
        'configuration/sampling',
        'configuration/privacy',
        'configuration/conversation-capture',
        'configuration/batch-export',
      ],
    },
    {
      type: 'category',
      label: 'Architecture',
      collapsed: true,
      items: [
        'architecture/overview',
        'architecture/span-hierarchy',
        'architecture/attributes',
        'architecture/turn-summary',
        'architecture/tool-identity',
        'architecture/orphan-sweep',
      ],
    },
    {
      type: 'category',
      label: 'Development',
      collapsed: true,
      items: [
        'development/contributing',
        'development/testing',
        'development/releasing',
        'development/debug-logging',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: true,
      items: [
        'reference/env-vars',
        'reference/config-schema',
        'reference/span-attributes',
        'reference/hooks',
        'reference/limitations',
      ],
    },
  ],
};

export default sidebars;
