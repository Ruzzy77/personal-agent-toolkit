import { fromMarkdown } from 'mdast-util-from-markdown';
import { gfmFromMarkdown } from 'mdast-util-gfm';
import { gfm } from 'micromark-extension-gfm';
import type { Nodes } from 'mdast';

export type MarkdownNode = Nodes;
export const parseMarkdown = (body: string) => fromMarkdown(body, {
  extensions: [gfm()], mdastExtensions: [gfmFromMarkdown()],
});
export const startOf = (node: MarkdownNode) => node.position?.start.offset ?? 0;
export const endOf = (node: MarkdownNode) => node.position?.end.offset ?? 0;
export function visibleText(node: MarkdownNode): string {
  if (node.type === 'break' || (node.type === 'html' && /^<br\s*\/?\s*>$/i.test(node.value))) return '\n';
  if ('value' in node) return node.value;
  if ('children' in node) return node.children.map(visibleText).join('');
  return '';
}
export function editableInline(node: MarkdownNode): boolean {
  if (['text', 'inlineCode', 'break'].includes(node.type)) return true;
  if (node.type === 'html') return /^<br\s*\/?\s*>$/i.test(node.value);
  return 'children' in node && node.children.every(editableInline);
}
function plainMarkdown(value: string, prefix: string, table: boolean, newline: string) {
  return value.replace(/\\/g, '\\\\').replace(/([`*_\[\]<>|~])/g, '\\$1')
    .replace(/&(?=#?\w+;)/g, '\\&').replace(/^(\s*)([#>+=\-])/gm, '$1\\$2')
    .replace(/^(\s*\d+)([.)])(?=\s)/gm, '$1\\$2')
    .replace(/\r?\n/g, table ? '<br>' : newline + prefix);
}
function inlineCode(value: string, table: boolean) {
  const normalized = value.replace(/\r?\n/g, ' ').replace(/\|/g, table ? '\\|' : '|');
  const longest = Math.max(0, ...Array.from(normalized.matchAll(/`+/g), m => m[0].length));
  const fence = '`'.repeat(longest + 1);
  const padding = /^`|`$/.test(normalized) || (/^ .* $/.test(normalized) && /[^ ]/.test(normalized)) ? ' ' : '';
  return normalized ? fence + padding + normalized + padding + fence : '';
}

/** Replace only the edited field's source span, never round-trip the document. */
export function replaceVisibleText(body: string, node: MarkdownNode, nextText: string, table = false): string {
  const original = visibleText(node);
  const next = nextText.replace(/\r\n/g, '\n').replace(/\n/g, node.type === 'heading' ? ' ' : '\n');
  if (next === original) return body;
  const start = startOf(node), end = endOf(node);
  const raw = body.slice(start, end);
  const newline = body.includes('\r\n') ? '\r\n' : '\n';
  const lineStart = body.lastIndexOf('\n', start - 1) + 1;
  const prefix = body.slice(lineStart, start).replace(/(?:[-+*]|\d+[.)])\s+/g, match => ' '.repeat(match.length)).replace(/\[[ xX]\][ \t]+$/, '');
  if (node.type === 'code') {
    const opening = raw.match(/^([ \t]*)([`~]{3,})([^\r\n]*)(?:\r?\n|$)/);
    if (opening) {
      const character = opening[2][0];
      const longest = Math.max(opening[2].length - 1, ...Array.from(next.matchAll(character === '`' ? /`+/g : /~+/g), m => m[0].length));
      const fence = character.repeat(longest + 1);
      const continuation = prefix + opening[1];
      return body.slice(0, start) + opening[1] + fence + opening[3] + newline + continuation
        + next.replace(/\n/g, newline + continuation) + newline + continuation + fence + body.slice(end);
    }
    const indent = raw.match(/^[ \t]+/)?.[0] ?? '    ';
    return body.slice(0, start) + indent + next.replace(/\n/g, newline + prefix + indent) + body.slice(end);
  }
  if (!('children' in node) || !editableInline(node)) throw new Error('이 부분은 마크다운 편집에서 수정해 주세요.');

  let left = 0, right = 0;
  while (left < original.length && left < next.length && original[left] === next[left]) left++;
  while (right < original.length - left && right < next.length - left && original[original.length - 1 - right] === next[next.length - 1 - right]) right++;
  const removedEnd = original.length - right;
  const inserted = next.slice(left, next.length - right);
  let offset = 0, didInsert = false;
  function serialize(part: MarkdownNode): string {
    const source = body.slice(startOf(part), endOf(part));
    if ('children' in part) {
      const pieces = part.children.map(serialize).join('');
      if (!pieces) return '';
      const first = part.children[0], last = part.children.at(-1)!;
      if (part.type === 'link' && !source.startsWith('[') && pieces !== body.slice(startOf(first), endOf(last))) {
        return '[' + pieces + '](<' + part.url.replace(/[<>]/g, c => encodeURIComponent(c)) + '>)';
      }
      if (part.type === 'linkReference' && part.referenceType !== 'full' && pieces !== body.slice(startOf(first), endOf(last))) {
        return '[' + pieces + '][' + (part.label ?? part.identifier) + ']';
      }
      if (['strong', 'emphasis', 'delete'].includes(part.type)) {
        if (!pieces.trim()) return pieces;
        const leading = pieces.match(/^\s*/)?.[0] ?? '', trailing = pieces.match(/\s*$/)?.[0] ?? '';
        return leading + body.slice(startOf(part), startOf(first)) + pieces.trim() + body.slice(endOf(last), endOf(part)) + trailing;
      }
      return body.slice(startOf(part), startOf(first)) + pieces + body.slice(endOf(last), endOf(part));
    }
    const value = visibleText(part), from = offset, to = from + value.length;
    offset = to;
    const a = Math.max(0, Math.min(value.length, left - from));
    const b = Math.max(a, Math.min(value.length, removedEnd - from));
    const ownsInsertion = !didInsert && left >= from && (removedEnd > left ? left < to : left <= to);
    const changed = value.slice(0, a) + (ownsInsertion ? inserted : '') + value.slice(b);
    if (ownsInsertion) didInsert = true;
    if (changed === value) return source;
    return part.type === 'inlineCode' ? inlineCode(changed, table) : plainMarkdown(changed, prefix, table, newline);
  }
  const content = node.children.map(serialize).join('');
  const first = node.children[0], last = node.children.at(-1);
  const emptyPrefix = raw.match(table ? /^\|?[ \t]*/ : /^[ \t]*/)?.[0] ?? '';
  const from = first ? startOf(first) : start + emptyPrefix.length;
  const to = last ? endOf(last) : from;
  const replacement = first ? content : plainMarkdown(next, prefix, table, newline);
  return body.slice(0, from) + replacement + body.slice(to);
}

const escapeHtml = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
/** Only our Markdown AST becomes HTML; pasted or stored raw HTML is never trusted. */
export function inlineHtml(node: MarkdownNode, definitions: Map<string, Extract<MarkdownNode, { type: 'definition' }>> = new Map()): string {
  const inner = () => 'children' in node ? node.children.map(n => inlineHtml(n, definitions)).join('') : '';
  switch (node.type) {
    case 'text': return escapeHtml(node.value);
    case 'code': case 'inlineCode': return node.type === 'code' ? escapeHtml(node.value) : '<code>' + escapeHtml(node.value) + '</code>';
    case 'strong': return '<strong>' + inner() + '</strong>';
    case 'emphasis': return '<em>' + inner() + '</em>';
    case 'delete': return '<del>' + inner() + '</del>';
    case 'break': return '<br>';
    case 'html': return /^<br\s*\/?\s*>$/i.test(node.value) ? '<br>' : '<code>' + escapeHtml(node.value) + '</code>';
    case 'link': case 'linkReference': {
      const target = node.type === 'link' ? node : definitions.get(node.identifier);
      if (!target || !/^(https?:\/\/|#)/i.test(target.url)) return inner();
      return '<a href="' + escapeHtml(target.url) + '"' + (target.title ? ' title="' + escapeHtml(target.title) + '"' : '')
        + (target.url.startsWith('#') ? '' : ' target="_blank" rel="noreferrer"') + '>' + inner() + '</a>';
    }
    case 'image': case 'imageReference': return escapeHtml(node.alt ?? '');
    default: return inner();
  }
}

export function replaceBodyIfCurrent(current: string, expected: string, next: string): string {
  if (current !== expected) throw new Error('수정안이 바뀌었습니다. 현재 내용을 확인한 뒤 다시 수정해 주세요.');
  return next;
}
