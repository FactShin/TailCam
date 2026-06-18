import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Markdown, extractHeadings } from "../components/Markdown";
import {
  DEFAULT_DOC,
  DOC_GROUPS,
  DOC_ORDER,
  getDoc,
  searchDocs,
} from "../docs";
import { IconBook, IconChevL, IconChevR, IconSearch } from "../icons";

export function Docs() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const doc = getDoc(slug);
  const [query, setQuery] = useState("");

  const results = useMemo(() => searchDocs(query), [query]);
  const headings = useMemo(() => (doc ? extractHeadings(doc.body) : []), [doc]);

  // Scroll back to the top whenever the active doc changes.
  useEffect(() => {
    document.querySelector(".content")?.scrollTo({ top: 0 });
    window.scrollTo({ top: 0 });
  }, [slug]);

  if (!doc) {
    return (
      <div className="screen">
        <div className="empty">
          <div className="empty-ic"><IconBook size={32} /></div>
          <div className="empty-title">Doc not found</div>
          <p className="empty-sub">That documentation page doesn't exist.</p>
          <button className="btn btn-primary btn-md" onClick={() => navigate(`/docs/${DEFAULT_DOC}`)}>
            Go to the docs home
          </button>
        </div>
      </div>
    );
  }

  const idx = DOC_ORDER.indexOf(doc.slug);
  const prev = idx > 0 ? getDoc(DOC_ORDER[idx - 1]) : undefined;
  const next = idx < DOC_ORDER.length - 1 ? getDoc(DOC_ORDER[idx + 1]) : undefined;

  const go = (s: string) => navigate(`/docs/${s}`);

  return (
    <div className="screen docs-screen">
      <div className="docs-layout">
        {/* Left rail: search + grouped navigation */}
        <aside className="docs-side">
          <div className="docs-search">
            <IconSearch size={14} />
            <input
              type="text"
              placeholder="Search the docs…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search documentation"
            />
            {query && (
              <button className="docs-search-clear" onClick={() => setQuery("")} aria-label="Clear search">
                ✕
              </button>
            )}
          </div>

          <nav className="docs-nav">
            {query ? (
              results.length ? (
                <div className="docs-results">
                  {results.map((hit) => (
                    <button
                      key={hit.doc.slug}
                      className={`docs-result ${hit.doc.slug === doc.slug ? "is-on" : ""}`}
                      onClick={() => go(hit.doc.slug)}
                    >
                      <span className="docs-result-title">{hit.doc.title}</span>
                      <span className="docs-result-snip">{hit.snippet}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="docs-noresults">No matches for “{query}”.</p>
              )
            ) : (
              DOC_GROUPS.map((grp) => (
                <div key={grp.group} className="docs-group">
                  <div className="docs-group-label">{grp.group}</div>
                  {grp.docs.map((d) => (
                    <button
                      key={d.slug}
                      className={`docs-link ${d.slug === doc.slug ? "is-on" : ""}`}
                      onClick={() => go(d.slug)}
                      aria-current={d.slug === doc.slug ? "page" : undefined}
                    >
                      {d.title}
                    </button>
                  ))}
                </div>
              ))
            )}
          </nav>
        </aside>

        {/* Center: the rendered document */}
        <article className="docs-main panel">
          <div className="docs-head">
            <div className="kicker">
              <span className="kicker-rule" />
              <span className="microlabel lit">Documentation</span>
            </div>
            <h1 className="screen-title">{doc.title}</h1>
            <p className="screen-sub">{doc.summary}</p>
          </div>

          <Markdown key={doc.slug} source={doc.body} />

          <nav className="docs-pager">
            {prev ? (
              <button className="docs-pager-btn" onClick={() => go(prev.slug)}>
                <IconChevL size={15} />
                <span><span className="docs-pager-dir">Previous</span><span className="docs-pager-title">{prev.title}</span></span>
              </button>
            ) : <span />}
            {next ? (
              <button className="docs-pager-btn docs-pager-next" onClick={() => go(next.slug)}>
                <span><span className="docs-pager-dir">Next</span><span className="docs-pager-title">{next.title}</span></span>
                <IconChevR size={15} />
              </button>
            ) : <span />}
          </nav>
        </article>

        {/* Right rail: on-page table of contents */}
        <aside className="docs-toc">
          {headings.length > 0 && (
            <>
              <div className="docs-toc-label">On this page</div>
              <ul>
                {headings.map((h) => (
                  <li key={h.id} className={h.depth >= 3 ? "is-sub" : ""}>
                    <a
                      href={`#${h.id}`}
                      onClick={(e) => {
                        e.preventDefault();
                        document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth" });
                      }}
                    >
                      {h.text}
                    </a>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
