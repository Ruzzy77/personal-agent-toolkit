'use client';

import { Fragment, useMemo, useRef, useState, type HTMLAttributes, type ReactNode } from 'react';
import { sectionsOf } from '../lib/guidance';
import { editableInline, endOf, inlineHtml, parseMarkdown, replaceVisibleText, startOf, visibleText, type MarkdownNode } from '../lib/inline-markdown';

type Props = {
  body: string;
  title?: string;
  editable: boolean;
  onChange: (body: string, expectedBody: string) => boolean;
};

export function InlineDocument({ body, title, editable, onChange }: Props) {
  // Keep the focused DOM and source positions stable through typing and Korean IME.
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const transaction = useRef<{ base: string; expected: string } | null>(null);
  const composing = useRef(false);
  const source = snapshot ?? body;
  const tree = useMemo(() => parseMarkdown(source), [source]);
  const sections = useMemo(() => sectionsOf(source), [source]);
  const definitions = new Map(tree.children.filter(n => n.type === 'definition').map(n => [n.identifier, n]));

  function update(element: HTMLElement, node: MarkdownNode, table: boolean) {
    const current = transaction.current;
    if (!editable || !current || composing.current) return;
    const next = replaceVisibleText(current.base, node, element.innerText, table);
    if (next !== current.expected && onChange(next, current.expected)) current.expected = next;
  }
  function field(node: MarkdownNode, table = false): HTMLAttributes<HTMLElement> {
    // React owns the field boundary, not its mutable descendants. Replacing the
    // HTML on blur safely reconciles markup removed by native contenteditable.
    const html = { dangerouslySetInnerHTML: { __html: inlineHtml(node, definitions) } };
    if (!editable || (node.type !== 'code' && !editableInline(node))) return html;
    return {
      ...html,
      contentEditable: 'plaintext-only', suppressContentEditableWarning: true, spellCheck: false,
      className: 'inline-field', tabIndex: 0,
      'aria-label': node.type === 'heading' ? '제목 편집' : node.type === 'tableCell' ? '표 셀 편집' : node.type === 'code' ? '코드 편집' : '본문 편집',
      onFocus: () => { transaction.current = { base: body, expected: body }; setSnapshot(body); },
      onCompositionStart: () => { composing.current = true; },
      onCompositionEnd: e => { composing.current = false; update(e.currentTarget, node, table); },
      onInput: e => update(e.currentTarget, node, table),
      onBlur: e => {
        composing.current = false; update(e.currentTarget, node, table);
        if (transaction.current?.expected === transaction.current?.base) e.currentTarget.innerHTML = inlineHtml(node, definitions);
        transaction.current = null; setSnapshot(null);
      },
      onBeforeInput: e => { if ((e.nativeEvent as InputEvent).inputType?.startsWith('format')) e.preventDefault(); },
      onClick: e => { if ((e.target as Element).closest('a') && !e.metaKey && !e.ctrlKey) e.preventDefault(); },
      onKeyDown: e => {
        if (e.key === 'Escape' && !composing.current) { e.preventDefault(); e.currentTarget.blur(); }
        if (e.key === 'Enter' && node.type === 'heading' && !composing.current) { e.preventDefault(); e.currentTarget.blur(); }
      },
    };
  }
  function children(node: MarkdownNode): ReactNode {
    return 'children' in node ? node.children.map((child, index) => <Fragment key={index}>{render(child)}</Fragment>) : null;
  }
  function render(node: MarkdownNode): ReactNode {
    switch (node.type) {
      case 'text': return node.value;
      case 'strong': return <strong>{children(node)}</strong>;
      case 'emphasis': return <em>{children(node)}</em>;
      case 'delete': return <del>{children(node)}</del>;
      case 'inlineCode': return <code>{node.value}</code>;
      case 'break': return <br />;
      case 'link': return link(node.url, children(node), node.title);
      case 'linkReference': {
        const definition = definitions.get(node.identifier);
        return definition ? link(definition.url, children(node), definition.title) : children(node);
      }
      case 'paragraph': return <p {...field(node)} />;
      case 'heading': return node.depth <= 2 ? <h2 {...field(node)} /> : <h3 {...field(node)} />;
      case 'list': return node.ordered ? <ol start={node.start ?? 1}>{children(node)}</ol> : <ul>{children(node)}</ul>;
      case 'listItem': return <li>{node.checked != null && <input type="checkbox" checked={node.checked} readOnly tabIndex={-1} aria-label={node.checked ? '완료' : '미완료'} />}{children(node)}</li>;
      case 'blockquote': return <blockquote>{children(node)}</blockquote>;
      case 'code': return <pre><code {...field(node)} /></pre>;
      case 'thematicBreak': return <hr />;
      case 'table': return <div className="table-wrap"><table>
        <thead><tr>{node.children[0]?.children.map((cell, index) => <th key={index} style={{ textAlign: node.align?.[index] ?? undefined }}><span {...field(cell, true)} /></th>)}</tr></thead>
        <tbody>{node.children.slice(1).map((row, index) => <tr key={index}>{row.children.map((cell, column) => <td key={column} style={{ textAlign: node.align?.[column] ?? undefined }}><span {...field(cell, true)} /></td>)}</tr>)}</tbody>
      </table></div>;
      case 'html': return /^<br\s*\/?\s*>$/i.test(node.value) ? <br /> : <code>{node.value}</code>;
      case 'image': return <span>{node.alt || node.url}</span>;
      case 'imageReference': return <span>{node.alt || node.identifier}</span>;
      case 'definition': return null;
      default: return <pre>{source.slice(startOf(node), endOf(node))}</pre>;
    }
  }
  function link(url: string, label: ReactNode, tooltip?: string | null) {
    if (!/^(https?:\/\/|#)/i.test(url)) return label;
    return <a href={url} title={tooltip ?? undefined} target={url.startsWith('#') ? undefined : '_blank'} rel="noreferrer"
      onClick={e => { if (editable && !e.metaKey && !e.ctrlKey) e.preventDefault(); }}>{label}</a>;
  }
  const first = tree.children[0];
  const hiddenTitle = first?.type === 'heading' && first.depth === 1 && visibleText(first) === title ? first : null;
  return <>{sections.map(section => {
    const nodes = tree.children.filter(node => node !== hiddenTitle && startOf(node) >= section.start && startOf(node) < section.end);
    if (!nodes.length && source.trim()) return null;
    return <section className="reading-section" id={section.key} key={section.key} data-guidance-section tabIndex={-1}>
      <div className="prose inline-document">{nodes.length ? nodes.map((node, index) => <Fragment key={index}>{render(node)}</Fragment>)
        : <p {...field({ type: 'paragraph', children: [], position: { start: { line: 1, column: 1, offset: 0 }, end: { line: 1, column: 1, offset: 0 } } })} data-placeholder="본문" />}</div>
    </section>;
  })}</>;
}
