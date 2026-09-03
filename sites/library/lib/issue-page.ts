import type { LibraryIssue } from '@/lib/library';

function addConnectSource(policy: string): string {
  if (/\bconnect-src\b/i.test(policy)) return policy;
  return `${policy.trim().replace(/;?$/, ';')} connect-src 'self'`;
}

function addFontSource(policy: string): string {
  if (/\bfont-src\b/i.test(policy)) {
    return policy.replace(/\bfont-src\s+([^;]*)/i, (_, sources: string) => {
      const values = sources
        .trim()
        .split(/\s+/)
        .filter(Boolean)
        .filter((value) => value !== "'none'");
      if (!values.includes("'self'")) values.push("'self'");
      return `font-src ${values.join(' ')}`;
    });
  }
  return `${policy.trim().replace(/;?$/, ';')} font-src 'self'`;
}

function escapeAttribute(value: string | number | null | undefined): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function addIssueIdentity(html: string, issue: LibraryIssue): string {
  return html.replace(/<html\b([^>]*)>/i, (_tag, attributes: string) => {
    const cleanAttributes = attributes.replace(
      /\sdata-library-(?:issue-id|collection|date|version)=(['"])[\s\S]*?\1/gi,
      '',
    );
    return (
      `<html${cleanAttributes}` +
      ` data-library-issue-id="${escapeAttribute(issue.id)}"` +
      ` data-library-collection="${escapeAttribute(issue.collection)}"` +
      ` data-library-date="${escapeAttribute(issue.date)}"` +
      ` data-library-version="${escapeAttribute(issue.version)}">`
    );
  });
}

export function renderIssuePage(issue: LibraryIssue): string {
  let html = addIssueIdentity(issue.sourceHtml, issue).replace(
    /<meta\b[^>]*http-equiv=["']Content-Security-Policy["'][^>]*>/i,
    (tag) =>
      tag.replace(
        /content=(["'])([\s\S]*?)\1/i,
        (_, quote: string, policy: string) =>
          `content=${quote}${addConnectSource(addFontSource(policy))}${quote}`,
      ),
  );
  const editor = `\n  <link rel="stylesheet" href="/library-editor.css">\n  <script src="/library-editor.js" data-library-issue-id="${escapeAttribute(issue.id)}" data-library-version="${escapeAttribute(issue.version)}" defer></script>`;
  html = html.replace(/<\/head>/i, `${editor}\n</head>`);
  return html;
}

export function issueHtmlResponse(issue: LibraryIssue, head = false): Response {
  return new Response(head ? null : renderIssuePage(issue), {
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'private, no-store',
      'X-Robots-Tag': 'noindex, nofollow',
    },
  });
}
