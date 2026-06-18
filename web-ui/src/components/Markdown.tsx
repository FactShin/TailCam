// Self-contained Markdown renderer — no external dependency.
//
// Documentation ships inside the SPA (imported with `?raw`) and renders to React
// nodes here so internal doc links route in-app (the whole point: never leave
// TailCam for GitHub). Supports headings, paragraphs, fenced code, blockquotes,
// ordered/unordered lists, GFM tables, horizontal rules, images, and inline
// **bold**, *italic*, `code`, and [links](...). Underscores are intentionally
// NOT italic markers — snake_case (node_key, base_url, …) is everywhere in TailCam.

import { Fragment, type ReactNode } from "react";
import { useNavigate, type NavigateFunction } from "react-router-dom";

export interface Heading {
  depth: number;
  text: string;
  id: string;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/`/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/** Pull headings (h2/h3) out of a doc for the table-of-contents rail. */
export function extractHeadings(source: string): Heading[] {
  const out: Heading[] = [];
  let inFence = false;
  for (const line of source.split("\n")) {
    if (line.trimStart().startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^(#{1,4})\s+(.*)$/.exec(line);
    if (m && m[1].length >= 2 && m[1].length <= 3) {
      const text = m[2].replace(/[*`]/g, "").trim();
      out.push({ depth: m[1].length, text, id: slugify(text) });
    }
  }
  return out;
}

// -- inline ---------------------------------------------------------------
function resolveHref(href: string): string {
  return href
    .replace(/^\.\//, "")
    .replace(/^\/docs\//, "")
    .replace(/\.md$/, "");
}

function MdLink({
  href,
  children,
  navigate,
}: {
  href: string;
  children: ReactNode;
  navigate: NavigateFunction;
}) {
  if (/^https?:\/\//.test(href)) {
    return (
      <a className="md-link md-ext" href={href} target="_blank" rel="noreferrer noopener">
        {children} ↗
      </a>
    );
  }
  const onClick = (e: React.MouseEvent) => {
    e.preventDefault();
    if (href.startsWith("#")) {
      document.getElementById(href.slice(1))?.scrollIntoView({ behavior: "smooth" });
    } else {
      navigate(`/docs/${resolveHref(href)}`);
    }
  };
  return (
    <a className="md-link" href={href} onClick={onClick}>
      {children}
    </a>
  );
}

interface InlineRule {
  re: RegExp;
  node: (m: RegExpExecArray, key: string, nav: NavigateFunction) => ReactNode;
}

const INLINE_RULES: InlineRule[] = [
  { re: /`([^`]+)`/, node: (m, k) => <code key={k} className="md-icode">{m[1]}</code> },
  {
    re: /!\[([^\]]*)\]\(([^)\s]+)\)/,
    node: (m, k) => <img key={k} className="md-img" src={m[2]} alt={m[1]} loading="lazy" />,
  },
  {
    re: /\[([^\]]+)\]\(([^)\s]+)\)/,
    node: (m, k, nav) => (
      <MdLink key={k} href={m[2]} navigate={nav}>
        {renderInline(m[1], nav, k + "l")}
      </MdLink>
    ),
  },
  { re: /\*\*([^*]+)\*\*/, node: (m, k, nav) => <strong key={k}>{renderInline(m[1], nav, k + "b")}</strong> },
  { re: /\*([^*]+)\*/, node: (m, k, nav) => <em key={k}>{renderInline(m[1], nav, k + "i")}</em> },
];

function renderInline(text: string, nav: NavigateFunction, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let i = 0;
  while (rest.length) {
    let best: { idx: number; rule: InlineRule; m: RegExpExecArray } | null = null;
    for (const rule of INLINE_RULES) {
      const m = rule.re.exec(rest);
      if (m && (best === null || m.index < best.idx)) best = { idx: m.index, rule, m };
    }
    if (!best) {
      out.push(rest);
      break;
    }
    if (best.idx > 0) out.push(rest.slice(0, best.idx));
    out.push(best.rule.node(best.m, `${keyPrefix}-${i++}`, nav));
    rest = rest.slice(best.idx + best.m[0].length);
  }
  return out;
}

// -- blocks ---------------------------------------------------------------
type Block =
  | { t: "h"; depth: number; text: string }
  | { t: "p"; text: string }
  | { t: "code"; lang: string; lines: string[] }
  | { t: "quote"; lines: string[] }
  | { t: "ul"; items: string[] }
  | { t: "ol"; items: string[] }
  | { t: "table"; head: string[]; rows: string[][] }
  | { t: "hr" };

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (line.trimStart().startsWith("```")) {
      const lang = line.trim().slice(3).trim();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) buf.push(lines[i++]);
      i++; // closing fence
      blocks.push({ t: "code", lang, lines: buf });
      continue;
    }

    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ t: "h", depth: h[1].length, text: h[2].trim() });
      i++;
      continue;
    }

    if (/^\s*([-*_])\s*\1\s*\1[-*_\s]*$/.test(line) && line.trim().length >= 3) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }

    if (line.startsWith(">")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) buf.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push({ t: "quote", lines: buf });
      continue;
    }

    // GFM table: header row followed by a |---|--- separator.
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitRow(lines[i++]));
      blocks.push({ t: "table", head, rows });
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*+]\s+/, ""));
      blocks.push({ t: "ul", items });
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+\.\s+/, ""));
      blocks.push({ t: "ol", items });
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    // Paragraph: gather until a blank line or a block starter.
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trimStart().startsWith("```") &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !lines[i].startsWith(">") &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      buf.push(lines[i++]);
    }
    blocks.push({ t: "p", text: buf.join(" ") });
  }
  return blocks;
}

export function Markdown({ source }: { source: string }) {
  const navigate = useNavigate();
  const blocks = parseBlocks(source);
  return (
    <div className="md">
      {blocks.map((b, k) => {
        const key = `b${k}`;
        switch (b.t) {
          case "h": {
            const id = slugify(b.text);
            const Tag = (`h${Math.min(b.depth, 6)}`) as keyof JSX.IntrinsicElements;
            return (
              <Tag key={key} id={id} className={`md-h md-h${b.depth}`}>
                {renderInline(b.text, navigate, key)}
              </Tag>
            );
          }
          case "p":
            return <p key={key} className="md-p">{renderInline(b.text, navigate, key)}</p>;
          case "code":
            return (
              <pre key={key} className="md-pre" data-lang={b.lang}>
                <code>{b.lines.join("\n")}</code>
              </pre>
            );
          case "quote":
            return (
              <blockquote key={key} className="md-quote">
                {renderInline(b.lines.join(" "), navigate, key)}
              </blockquote>
            );
          case "ul":
            return (
              <ul key={key} className="md-ul">
                {b.items.map((it, j) => <li key={j}>{renderInline(it, navigate, `${key}-${j}`)}</li>)}
              </ul>
            );
          case "ol":
            return (
              <ol key={key} className="md-ol">
                {b.items.map((it, j) => <li key={j}>{renderInline(it, navigate, `${key}-${j}`)}</li>)}
              </ol>
            );
          case "table":
            return (
              <div key={key} className="md-table-wrap">
                <table className="md-table">
                  <thead>
                    <tr>{b.head.map((c, j) => <th key={j}>{renderInline(c, navigate, `${key}h${j}`)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {b.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((c, j) => <td key={j}>{renderInline(c, navigate, `${key}r${r}c${j}`)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={key} className="md-hr" />;
          default:
            return <Fragment key={key} />;
        }
      })}
    </div>
  );
}
