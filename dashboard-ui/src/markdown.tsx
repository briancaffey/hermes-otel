// React renderer for markdown.ts: every node becomes an element, never HTML.
import { React } from "./sdk";
import { Block, Inline, parseBlocks } from "./md";

function Inlines({ nodes }: { nodes: Inline[] }) {
  return (
    <>
      {nodes.map((n, i) => {
        if (n.t === "text") return <React.Fragment key={i}>{n.v}</React.Fragment>;
        if (n.t === "code")
          return (
            <code key={i} className="otel-code">
              {n.v}
            </code>
          );
        if (n.t === "strong")
          return (
            <strong key={i}>
              <Inlines nodes={n.children} />
            </strong>
          );
        if (n.t === "em")
          return (
            <em key={i}>
              <Inlines nodes={n.children} />
            </em>
          );
        return (
          <a key={i} className="otel-link" href={n.href} target="_blank" rel="noreferrer noopener">
            <Inlines nodes={n.children} />
          </a>
        );
      })}
    </>
  );
}

function BlockView({ b }: { b: Block }) {
  switch (b.t) {
    case "heading": {
      const Tag = `h${Math.min(6, b.level)}` as any;
      return (
        <Tag className={`otel-md-h otel-md-h${Math.min(4, b.level)}`}>
          <Inlines nodes={b.children} />
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p className="otel-md-p">
          <Inlines nodes={b.children} />
        </p>
      );
    case "code":
      return (
        <pre className="otel-pre otel-md-code" data-lang={b.lang || undefined}>
          {b.text}
        </pre>
      );
    case "list": {
      const Tag = (b.ordered ? "ol" : "ul") as any;
      return (
        <Tag className={b.ordered ? "otel-md-ol" : "otel-md-ul"}>
          {b.items.map((it, i) => (
            <li key={i}>
              <Inlines nodes={it} />
            </li>
          ))}
        </Tag>
      );
    }
    case "quote":
      return (
        <blockquote className="otel-md-quote">
          <Inlines nodes={b.children} />
        </blockquote>
      );
    case "table":
      return (
        <div className="otel-md-tablewrap">
          <table className="otel-md-table">
            <thead>
              <tr>
                {b.header.map((c, i) => (
                  <th key={i}>
                    <Inlines nodes={c} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td key={j}>
                      <Inlines nodes={c} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return <hr className="otel-md-rule" />;
  }
}

/** Markdown text as elements. */
export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text || "");
  return (
    <div className="otel-md">
      {blocks.map((b, i) => (
        <BlockView key={i} b={b} />
      ))}
    </div>
  );
}
