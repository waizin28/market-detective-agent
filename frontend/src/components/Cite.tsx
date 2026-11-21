import { Fragment, type ReactNode } from "react";

const CITE_RE = /\[?(E-[0-9A-F]{6})\]?/g;

/** Render text with evidence ids ([E-3F9A1C]) as clickable citation chips. */
export function CitedText({
  text,
  onCite,
  known,
}: {
  text: string;
  onCite: (id: string) => void;
  known?: Set<string>;
}) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE_RE)) {
    const id = m[1];
    if (m.index! > last) parts.push(text.slice(last, m.index));
    if (!known || known.has(id)) {
      parts.push(
        <button key={`${id}-${m.index}`} className="cite" onClick={() => onCite(id)} title="Open evidence">
          {id}
        </button>,
      );
    }
    last = m.index! + m[0].length;
  }
  parts.push(text.slice(last));
  return <>{parts.map((p, i) => <Fragment key={i}>{p}</Fragment>)}</>;
}
