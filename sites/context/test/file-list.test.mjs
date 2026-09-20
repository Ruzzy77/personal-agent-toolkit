import test from 'node:test';
import assert from 'node:assert/strict';
import { displayedFiles, fileKind } from '../lib/file-list.ts';
const file = (name, type = 'file', mime = 'text/plain') => ({ name, path: name, type, mime, bytes: 25 });

test('file display hides dotfiles without changing source entries and keeps folders first', () => {
  const entries = [file('문서10.md'), file('.git', 'directory'), file('문서2.md'), file('작업', 'directory')];
  assert.deepEqual(displayedFiles(entries, '', false, false).map(e => e.name), ['작업', '문서2.md', '문서10.md']);
  assert.deepEqual(displayedFiles(entries, '', false, true).map(e => e.name), ['작업', '문서10.md', '문서2.md']);
  assert.equal(displayedFiles(entries, '', true, false).length, 4);
  assert.equal(entries[0].name, '문서10.md');
});

test('search handles Korean normalization and filename case', () => {
  const entries = [file('한글.md'.normalize('NFD')), file('README.md')];
  assert.equal(displayedFiles(entries, '한글', false, false).length, 1);
  assert.equal(displayedFiles(entries, 'readme', false, false).length, 1);
});

test('loaded pages retain visible entries and hidden-file toggles are reversible', () => {
  const first = Array.from({ length: 100 }, (_, i) => file('.hidden' + i));
  const next = [file('보이는 문서.md')];
  assert.equal(displayedFiles(first, '', false, false).length, 0);
  assert.deepEqual(displayedFiles([...first, ...next], '', false, false).map(e => e.name), ['보이는 문서.md']);
  assert.equal(displayedFiles([...first, ...next], '', true, false).length, 101);
});

test('file metadata uses available type information instead of invented dates', () => {
  assert.equal(fileKind(file('폴더', 'directory')), '폴더');
  assert.equal(fileKind(file('README.md')), 'Markdown');
  assert.equal(fileKind(file('photo.png', 'file', 'image/png')), '이미지');
  assert.equal(fileKind(file('sheet.pdf', 'file', 'application/pdf')), 'PDF');
  assert.equal(fileKind(file('LICENSE')), '파일');
});
