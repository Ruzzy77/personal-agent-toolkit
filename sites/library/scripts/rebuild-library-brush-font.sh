#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

uv run --with numpy --with pillow --with 'fonttools[woff]' scripts/build-library-brush-font.py \
  --wordmark public/icons/library/library-wordmark.png \
  --sheet font-sources/library-brush-uppercase-abc-def-ghi-v2.png ABCDEFGHI \
  --sheet font-sources/library-brush-uppercase-jkl-mno-pqr-v2.png JKLMNOPQR \
  --sheet font-sources/library-brush-uppercase-stu-vwx-yzamp-v2.png 'STUVWXYZ&' \
  --sheet font-sources/library-brush-lowercase-abc-def-ghi-v2.png abcdefghi \
  --sheet font-sources/library-brush-lowercase-jkl-mno-pqr-v2.png jklmnopqr \
  --sheet font-sources/library-brush-lowercase-stu-vwx-yzapo-v2.png "stuvwxyz'" \
  --sheet font-sources/library-brush-digits-012-v2.png 012 \
  --sheet font-sources/library-brush-digits-345-v2.png 345 \
  --sheet font-sources/library-brush-digits-678-v2.png 678 \
  --sheet font-sources/library-brush-punctuation-9dotcolon-v2.png '9.:' \
  --sheet font-sources/library-brush-punctuation-middot-hyphen-slash-v2.png '·-/' \
  --sheet font-sources/library-brush-punctuation-plus-exclaim-question-v2.png '+!?' \
  --override-sheet font-sources/library-brush-lowercase-ace-v2.png ace \
  --override-sheet font-sources/library-brush-lowercase-hif-v2.png hif \
  --override-sheet font-sources/library-brush-lowercase-jkl-v2.png jkl \
  --override-sheet font-sources/library-brush-lowercase-m-single-v2.png m \
  --override-sheet font-sources/library-brush-lowercase-n-single-v2.png n \
  --override-sheet font-sources/library-brush-lowercase-o-single-v2.png o \
  --override-sheet font-sources/library-brush-uppercase-xzamp-v2.png 'XZ&' \
  --ttf public/fonts/LibraryBrush-2.2-Regular.ttf \
  --woff2 public/fonts/LibraryBrush-2.2-Regular.woff2 \
  --specimen /tmp/LibraryBrush-2.2-specimen.png
