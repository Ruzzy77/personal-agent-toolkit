#!/usr/bin/env bash
# Source-level contract checks; image builds perform the executable checks.
set -euo pipefail

sandbox_dir="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$sandbox_dir/../../.." && pwd)"

bash -n "$repo/apps/host/scripts/build-sandbox.sh"
for file in "$sandbox_dir/Dockerfile" "$sandbox_dir/Dockerfile.web" "$sandbox_dir/Dockerfile.documents"; do
  ! grep -Eq '(^|[^[:alnum:]])latest([^[:alnum:]]|$)' "$file"
done

grep -Fq 'org.personal-agent.profile="base"' "$sandbox_dir/Dockerfile"
grep -Fq "org.personal-agent.features='[\"python\",\"git\",\"uv\",\"ripgrep\"]'" "$sandbox_dir/Dockerfile"
grep -Fq 'org.personal-agent.profile="web"' "$sandbox_dir/Dockerfile.web"
grep -Fq "org.personal-agent.features='[\"python\",\"git\",\"uv\",\"ripgrep\",\"node\",\"npm\"]'" "$sandbox_dir/Dockerfile.web"
grep -Fq 'org.personal-agent.profile="documents"' "$sandbox_dir/Dockerfile.documents"
grep -Fq "org.personal-agent.features='[\"python\",\"git\",\"uv\",\"ripgrep\",\"document-files\",\"docx\",\"xlsx\",\"pptx\",\"pdf\",\"image\"]'" "$sandbox_dir/Dockerfile.documents"
grep -Fq 'document_files_release.py source' "$sandbox_dir/Dockerfile.documents"
grep -Fq 'import document_files, docx, openpyxl, pptx, pypdf, PIL, reportlab' "$sandbox_dir/Dockerfile.documents"
! grep -Fq 'COPY plugins/document-files /build/plugins/document-files' "$sandbox_dir/Dockerfile.documents"
grep -Fq 'uv sync --locked --no-dev --no-cache' "$sandbox_dir/Dockerfile.documents"
grep -Fq 'rm -rf /build /root/.cache/uv' "$sandbox_dir/Dockerfile.documents"
grep -Fq 'mktemp -d' "$repo/apps/host/scripts/build-sandbox.sh"
grep -Fq 'plugins/document-files/uv.lock' "$repo/apps/host/scripts/build-sandbox.sh"
grep -Fq 'build_documents() (' "$repo/apps/host/scripts/build-sandbox.sh"
grep -Fq 'build_documents() (' "$repo/apps/host/scripts/build-sandbox.sh"
grep -Fq "trap 'rm -rf" "$repo/apps/host/scripts/build-sandbox.sh"
grep -Fq ' EXIT' "$repo/apps/host/scripts/build-sandbox.sh"
! grep -Fq ' RETURN' "$repo/apps/host/scripts/build-sandbox.sh"
grep -Fq -- '--test-runtime is a standalone test-only action' "$repo/apps/host/scripts/install-linux.sh"
grep -Fq 'mktemp -d -t personal-agent-host-test.XXXXXX' "$repo/apps/host/scripts/install-linux.sh"
grep -Fq 'apps/sync" "$REPO/apps/host[test]' "$repo/apps/host/scripts/install-linux.sh"
