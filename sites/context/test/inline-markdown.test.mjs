import test from 'node:test';
import assert from 'node:assert/strict';
import { parseMarkdown, replaceVisibleText, visibleText, editableInline, inlineHtml, replaceBodyIfCurrent } from '../lib/inline-markdown.ts';
import { emptyWorkspace, loadSources, updateDraft } from '../lib/guidance.ts';

const nodes = (root, type) => [ ...(root.type === type ? [root] : []), ...('children' in root ? root.children.flatMap(n => nodes(n, type)) : []) ];
const edit = (body, type, next, index = 0) => replaceVisibleText(body, nodes(parseMarkdown(body), type)[index], next, type === 'tableCell');

test('unchanged fields and all document bytes outside the edited field stay intact', () => {
  const body = '# 문서\n\n## 절\n\n앞 **굵게**와 [연결](https://example.com "제목") 그리고 `코드`.\n\n- 다음\n';
  assert.equal(edit(body, 'paragraph', '앞 굵게와 연결 그리고 코드.'), body);
  assert.equal(edit(body, 'paragraph', '앞 진하게와 연결 그리고 코드.'), body.replace('굵게', '진하게'));
  assert.equal(edit(body, 'paragraph', '바꾼 문단'), '# 문서\n\n## 절\n\n바꾼 문단\n\n- 다음\n');
  assert.equal(edit('## 절\r\n\r\n첫 줄\r\n', 'paragraph', '첫 줄\n둘째 줄'), '## 절\r\n\r\n첫 줄\r\n둘째 줄\r\n');
  assert.equal(edit('**굵게**', 'paragraph', ' 새 글 '), ' **새 글** ');
});

test('headings keep their markers and multiline paste does not create a new block', () => {
  assert.equal(edit('## 원래 ##\n\n본문', 'heading', '새 제목\n추가'), '## 새 제목 추가 ##\n\n본문');
  assert.equal(edit('제목\n====\n\n본문', 'heading', '바꾼 제목'), '바꾼 제목\n====\n\n본문');
});

test('list and quote continuations retain structure and literal Markdown stays literal', () => {
  assert.equal(edit('- [ ] 원래\n- 다음\n', 'paragraph', '추가\n두 줄'), '- [ ] 추가\n  두 줄\n- 다음\n');
  const result = edit('> - 원래\n> - 다음\n', 'paragraph', '1. 항목\n# 제목');
  assert.equal(result, '> - 1\\. 항목\n>   \\# 제목\n> - 다음\n');
  assert.equal(nodes(parseMarkdown(result), 'listItem').length, 2);
  assert.equal(edit('원래', 'paragraph', '*문자* &amp; <태그>'), '\\*문자\\* \\&amp; \\<태그\\>');
  assert.equal(nodes(parseMarkdown(edit('원래', 'paragraph', '문자\n---')), 'heading').length, 0);
});

test('empty table cells, pipes, line breaks and code remain inside their cell', () => {
  const body = '| A | B |\n|:---|---:|\n| | `x` |\n';
  const filled = edit(body, 'tableCell', '값 | 추가\n둘째', 2);
  assert.equal(filled, body.replace('| |', '| 값 \\| 추가<br>둘째|'));
  const coded = edit(body, 'tableCell', 'x|y', 3);
  const cells = nodes(parseMarkdown(coded), 'tableCell');
  assert.equal(cells.length, 4);
  assert.equal(visibleText(cells[3]), 'x|y');
});

test('fenced and indented code keep language, list/quote containers and safe fences', () => {
  for (const [body, expected] of [
    ['```ts\nold\n```\n', '````ts\n```\nnew\n````\n'],
    ['> ```ts\n> old\n> ```\n', '> ````ts\n> ```\n> new\n> ````\n'],
    ['- ```ts\n  old\n  ```\n', '- ````ts\n  ```\n  new\n  ````\n'],
    ['    old\n    line\n', '    ```\n    new\n'],
  ]) {
    const result = edit(body, 'code', '```\nnew');
    assert.equal(result, expected);
    assert.equal(nodes(parseMarkdown(result), 'code')[0].value, '```\nnew');
  }
});

test('reference link labels change without losing their destination definition', () => {
  for (const label of ['[원래]', '[원래][]', '[원래][원래]']) {
    const body = label + '\n\n[원래]: https://example.com "제목"\n';
    const changed = edit(body, 'paragraph', '새 이름');
    assert.equal(changed, '[새 이름][원래]\n\n[원래]: https://example.com "제목"\n');
  }
  for (const body of ['<https://example.com>', 'https://example.com']) {
    const changed = edit(body, 'paragraph', '사이트');
    assert.equal(nodes(parseMarkdown(changed), 'link')[0].url, 'https://example.com');
    assert.equal(visibleText(parseMarkdown(changed)), '사이트');
  }
});

test('rendering only emits escaped text and allowed inline markup', () => {
  const body = '<script>alert(1)</script>\n\n[이름](javascript:alert%281%29) **굵게** <br> `"<&`';
  const tree = parseMarkdown(body);
  const html = tree.children.map(n => inlineHtml(n)).join('');
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes('<strong>굵게</strong> <br> <code>&quot;&lt;&amp;</code>'));
  assert.equal(editableInline(tree.children[0]), false);
});

test('direct edits still reject stale bodies, read-only sources and incoming versions', () => {
  assert.throws(() => replaceBodyIfCurrent('새 초안', '기존 초안', '덮어쓰기'));
  assert.equal(replaceBodyIfCurrent('원래', '원래', '수정'), '수정');
  const source = { id: 'sample', kind: 'project', title: '가상 문서', reference: 'fixture', version: '1', content: { body: '원래' }, permission: 'read_only', activation: 'next_use' };
  const state = loadSources(emptyWorkspace(), [source]);
  assert.throws(() => updateDraft(state, source.id, { body: '수정' }, '1', state.entries[0].draftId));
  const writable = loadSources(emptyWorkspace(), [{ ...source, permission: 'read_write' }]);
  const draft = updateDraft(writable, source.id, { body: '수정' }, '1', writable.entries[0].draftId);
  const incoming = loadSources(draft, [{ ...source, permission: 'read_write', version: '2', content: { body: '새 원본' } }]);
  assert.throws(() => updateDraft(incoming, source.id, { body: '추가 수정' }, '1', incoming.entries[0].draftId));
});
