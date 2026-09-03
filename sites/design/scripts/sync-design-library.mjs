import { execFileSync } from "node:child_process";
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const galleryRoot = path.resolve(scriptDir, "..");
const designRoot = path.join(galleryRoot, "designs");
const catalogTool = path.join(designRoot, "tools", "bundle.py");

if (!existsSync(catalogTool)) {
  throw new Error(`디자인 파일을 찾을 수 없습니다: ${catalogTool}`);
}

const rawCatalog = execFileSync("python3", [catalogTool, "--catalog"], {
  cwd: designRoot,
  encoding: "utf8",
});
const catalog = JSON.parse(rawCatalog);

if (!Array.isArray(catalog.patterns) || catalog.patterns.length === 0) {
  throw new Error("등록된 디자인 패턴이 없습니다.");
}

if (!Array.isArray(catalog.recipes) || catalog.recipes.length === 0) {
  throw new Error("공개 가능한 디자인 레시피가 없습니다.");
}

writeFileSync(
  path.join(galleryRoot, "app", "design-catalog.json"),
  `${JSON.stringify(catalog, null, 2)}\n`,
);

const previewRoot = path.join(galleryRoot, "public", "previews");
const templateRoot = path.join(galleryRoot, "public", "templates");

for (const target of [previewRoot, templateRoot]) {
  rmSync(target, { recursive: true, force: true });
  mkdirSync(target, { recursive: true });
}

for (const design of catalog.recipes) {
  const designDir = path.join(designRoot, design.id);
  const styleguideSource = path.join(designDir, design.styleguide);
  const previewDir = path.join(previewRoot, design.id);

  if (!existsSync(styleguideSource)) {
    throw new Error(`${design.id}의 스타일가이드를 찾을 수 없습니다.`);
  }

  mkdirSync(previewDir, { recursive: true });
  const styleguide = readFileSync(styleguideSource, "utf8").replaceAll(
    'href="../index.html"',
    'href="/" target="_top"',
  );
  writeFileSync(path.join(previewDir, "index.html"), styleguide);

  const assetSource = path.join(designDir, "assets");
  if (existsSync(assetSource)) {
    cpSync(assetSource, path.join(previewDir, "assets"), { recursive: true });
  }

  if (design.format_guide) {
    const formatGuideSource = path.join(designDir, design.format_guide);
    if (!existsSync(formatGuideSource)) {
      throw new Error(`${design.id}의 형식 규칙을 찾을 수 없습니다.`);
    }
    copyFileSync(formatGuideSource, path.join(previewDir, "formats.json"));
  }

  for (const documentName of ["README.md", "DESIGN.md", "writing.md"]) {
    const documentSource = path.join(designDir, documentName);
    if (existsSync(documentSource)) {
      const destination = path.join(previewDir, documentName);
      if (documentName === "README.md") {
        const readme = readFileSync(documentSource, "utf8").replaceAll(
          "](templates/",
          `](/templates/${design.id}/templates/`,
        );
        writeFileSync(destination, readme);
      } else {
        copyFileSync(documentSource, destination);
      }
    }
  }

  const designTemplateDir = path.join(templateRoot, design.id);
  mkdirSync(designTemplateDir, { recursive: true });

  for (const templatePath of Object.values(design.templates || {})) {
    const normalizedTemplatePath = path.normalize(templatePath);
    if (
      path.isAbsolute(normalizedTemplatePath) ||
      normalizedTemplatePath === ".." ||
      normalizedTemplatePath.startsWith(`..${path.sep}`)
    ) {
      throw new Error(`${design.id}의 템플릿 경로가 올바르지 않습니다: ${templatePath}`);
    }

    const source = path.join(designDir, templatePath);
    if (!existsSync(source)) {
      throw new Error(`${design.id}의 템플릿을 찾을 수 없습니다: ${templatePath}`);
    }

    const destination = path.join(designTemplateDir, normalizedTemplatePath);
    mkdirSync(path.dirname(destination), { recursive: true });
    copyFileSync(source, destination);
  }
}

const galleryIndexPath = path.join(designRoot, "index.html");
if (existsSync(galleryIndexPath)) {
  const formatNames = { web: "웹·앱", document: "문서", slides: "슬라이드", image: "이미지" };
  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const cards = catalog.recipes
    .map((design) => {
      const gallery = design.gallery || {};
      const swatches = (gallery.swatches || [])
        .map((color) => `<span style="background: ${escapeHtml(color)};"></span>`)
        .join("");
      const chip = (design.formats || [])
        .map((format) => formatNames[format] || format)
        .join(" · ");
      return [
        `        <article class="card gl-card gl-card--${design.id}">`,
        `          <div class="gl-strip" aria-hidden="true">${swatches}</div>`,
        `          <div class="card-body">`,
        `            <h2>${escapeHtml(gallery.korean_name || design.name)}</h2>`,
        `            <p class="desc">${escapeHtml(gallery.note || design.description)}</p>`,
        `            <div class="gl-meta"><span class="chip">${escapeHtml(chip)}</span></div>`,
        `            <div class="gl-links">`,
        `              <a class="btn btn-tinted" href="${design.id}/${design.styleguide}">미리보기</a>`,
        `              <a class="btn btn-plain" href="${design.id}/${design.format_guide}">형식 규칙</a>`,
        `              <a class="btn btn-plain" href="${design.id}/README.md">사용 안내</a>`,
        `            </div>`,
        `          </div>`,
        `        </article>`,
      ].join("\n");
    })
    .join("\n\n");

  const indexHtml = readFileSync(galleryIndexPath, "utf8");
  const markerPattern = /(<!-- gallery:cards[^>]*-->)[\s\S]*?(<!-- \/gallery:cards -->)/;
  if (!markerPattern.test(indexHtml)) {
    throw new Error("designs/index.html에 gallery:cards 마커가 없습니다.");
  }
  writeFileSync(
    galleryIndexPath,
    indexHtml.replace(markerPattern, (match, open, close) => `${open}\n${cards}\n        ${close}`),
  );
}

const sourceReadme = path.join(designRoot, "README.md");
if (existsSync(sourceReadme)) {
  const sourceTitle = readFileSync(sourceReadme, "utf8").match(/^#\s+(.+)$/m)?.[1];
  if (sourceTitle) {
    catalog.library.source_title = sourceTitle;
    writeFileSync(
      path.join(galleryRoot, "app", "design-catalog.json"),
      `${JSON.stringify(catalog, null, 2)}\n`,
    );
  }
}

console.log(
  `${catalog.patterns.length}개 패턴과 ${catalog.recipes.length}개 레시피를 갤러리에 반영했습니다.`,
);
