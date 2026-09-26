"use client";
import { useState } from "react";
import { ImageRegionEditor } from "./ImageRegionEditor.jsx";
import { fullImageRegion } from "./image-region.js";
import { Button } from "@openai/apps-sdk-ui/components/Button";
import { Input } from "@openai/apps-sdk-ui/components/Input";
import { Textarea } from "@openai/apps-sdk-ui/components/Textarea";
import { DiagramFields } from "./DiagramFields.jsx";
const str = (value) => value === undefined || value === null ? "" : String(value);
const records = (value) => Array.isArray(value) ? value : [];
const strings = (value) => Array.isArray(value) ? value.map(str) : [];
function TextField({ label, value, onChange, rows, maxLength = 3000, disabled = false }) {
    return <label className="flow-content-field">{label}{rows
            ? <Textarea value={value} onChange={event => onChange(event.target.value)} rows={rows} maxLength={maxLength} disabled={disabled}/>
            : <Input value={value} onChange={event => onChange(event.target.value)} maxLength={maxLength} disabled={disabled}/>}
  </label>;
}
export function ContentFields({ block, onChange, onPick, resolveImageUrl, busy = false }) {
    const [cropOpen, setCropOpen] = useState(false);
    const c = block.content;
    const field = (key) => str(c[key]);
    const patch = (key, value) => onChange({ ...c, [key]: value });
    const list = (key) => records(c[key]);
    const editItem = (key, index, update) => patch(key, list(key).map((item, i) => i === index ? { ...item, ...update } : item));
    const addItem = (key, item) => patch(key, [...list(key), item]);
    const removeItem = (key, index) => patch(key, list(key).filter((_, i) => i !== index));
    const imageButton = (label, field = "src", itemId) => <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} onClick={() => onPick("image", block.id, field, itemId)}>{label}</Button>;
    const itemHeading = (label, index) => <span className="flow-content-item-heading">{label} {index + 1}</span>;
    const itemActions = (key, index, length, label) => <Button type="button" color="primary" variant="outline" pill={false} size="sm" aria-label={label + " " + (index + 1) + " 삭제"} disabled={busy || length <= 1} onClick={() => removeItem(key, index)}>삭제</Button>;
    const itemImageButton = (key, item, index) => <Button type="button" color="primary" variant="outline" pill={false} size="sm" aria-label={(item.src ? "다른 이미지 선택" : "이미지 선택") + " " + (index + 1)} disabled={busy} onClick={() => {
            const unique = typeof item.id === "string" && item.id && list(key).filter(other => other.id === item.id).length === 1;
            const id = unique ? item.id : crypto.randomUUID();
            if (!unique)
                editItem(key, index, { id });
            onPick("image", block.id, "src", id);
        }}>{item.src ? "다른 이미지 선택" : "이미지 선택"}</Button>;
    const heading = block.kind === "heading" ? null : <TextField label="소제목" value={field("heading")} onChange={value => patch("heading", value)} maxLength={500} disabled={busy}/>;
    switch (block.kind) {
        case "heading": return <div className="flow-content-fields">
      <TextField label="제목" value={field("title")} onChange={value => patch("title", value)} maxLength={500} disabled={busy}/>
      <TextField label="설명" value={field("description")} onChange={value => patch("description", value)} rows={3} maxLength={5000} disabled={busy}/>
    </div>;
        case "text": return <div className="flow-content-fields">{heading}
      {strings(c.paragraphs).map((text, index) => <div className="flow-content-item" key={index}>
        {itemHeading("문단", index)}
        <TextField label={"본문 " + (index + 1)} value={text} onChange={value => patch("paragraphs", strings(c.paragraphs).map((item, i) => i === index ? value : item))} rows={4} maxLength={20000} disabled={busy}/>
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || strings(c.paragraphs).length <= 1} onClick={() => patch("paragraphs", strings(c.paragraphs).filter((_, i) => i !== index))}>문단 삭제</Button>
      </div>)}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || strings(c.paragraphs).length >= 100} onClick={() => patch("paragraphs", [...strings(c.paragraphs), ""])}>문단 추가</Button>
    </div>;
        case "image": {
            const src = field("src");
            const preview = resolveImageUrl ? resolveImageUrl(src, block.id) : src;
            const canCrop = Boolean(preview && Number.isFinite(c.width) && c.width > 0 && Number.isFinite(c.height) && c.height > 0);
            return <div className="flow-content-fields">
      {imageButton(src ? "다른 이미지 선택" : "이미지 선택")}
      <TextField label="대체 텍스트" value={field("alt")} onChange={value => patch("alt", value)} maxLength={1000} disabled={busy}/>
      <TextField label="캡션" value={field("caption")} onChange={value => patch("caption", value)} maxLength={3000} disabled={busy}/>
      {canCrop && <div className="flow-content-list-actions">
        <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} aria-expanded={cropOpen} onClick={() => setCropOpen(value => !value)}>{cropOpen ? "영역 선택 닫기" : c.crop ? "영역 조정" : "영역 선택"}</Button>
        {c.crop && <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} onClick={() => patch("crop", null)}>자르기 해제</Button>}
      </div>}
      {canCrop && cropOpen && <ImageRegionEditor src={preview} alt={field("alt")} width={c.width} height={c.height} value={c.crop ?? fullImageRegion} disabled={busy} onChange={crop => patch("crop", crop)}/>}
    </div>;
        }
        case "comparison": return <div className="flow-content-fields">{heading}
      {list("items").map((item, index) => {
                const id = str(item.id);
                return <div className="flow-content-item" key={id || index}>
        {itemHeading("비교 이미지", index)}
        {itemImageButton("items", item, index)}
        <TextField label="이름" value={str(item.label)} onChange={value => editItem("items", index, { label: value })} maxLength={500} disabled={busy}/>
        <TextField label="대체 텍스트" value={str(item.alt)} onChange={value => editItem("items", index, { alt: value })} maxLength={1000} disabled={busy}/>
        <TextField label="캡션" value={str(item.caption)} onChange={value => editItem("items", index, { caption: value })} maxLength={3000} disabled={busy}/>
        {itemActions("items", index, list("items").length, "비교 이미지")}
      </div>;
            })}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || list("items").length >= 24} onClick={() => addItem("items", { id: crypto.randomUUID(), label: "", src: "", alt: "", caption: "" })}>비교 이미지 추가</Button>
    </div>;
        case "diagram": return <DiagramFields content={c} onChange={onChange} busy={busy}/>;
        case "media": return <div className="flow-content-fields">{heading}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} onClick={() => onPick("video", block.id, "src")}>{field("src") ? "다른 영상 선택" : "영상 선택"}</Button>
      {imageButton(field("poster") ? "대표 이미지 바꾸기" : "대표 이미지 선택", "poster")}
      <TextField label="대체 텍스트" value={field("alt")} onChange={value => patch("alt", value)} maxLength={1000} disabled={busy}/>
      <TextField label="캡션" value={field("caption")} onChange={value => patch("caption", value)} maxLength={3000} disabled={busy}/>
    </div>;
        case "table": {
            const columns = strings(c.columns), rows = Array.isArray(c.rows) ? c.rows : [];
            const updateRow = (row, column, value) => patch("rows", rows.map((cells, i) => i === row ? cells.map((cell, j) => j === column ? value : cell) : cells));
            return <div className="flow-content-fields">{heading}
        <div className="flow-content-table-edit"><table><thead><tr>{columns.map((column, index) => <th key={index}><Input aria-label={"열 " + (index + 1) + " 이름"} value={column} maxLength={500} disabled={busy} onChange={event => patch("columns", columns.map((item, i) => i === index ? event.target.value : item))}/></th>)}</tr></thead>
          <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><Input aria-label={(i + 1) + "행 " + (j + 1) + "열"} value={str(cell)} maxLength={2000} disabled={busy} onChange={event => updateRow(i, j, event.target.value)}/></td>)}</tr>)}</tbody></table></div>
        <div className="flow-content-list-actions">
          <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || rows.length >= 1000} onClick={() => patch("rows", [...rows, columns.map(() => "")])}>행 추가</Button>
          <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || rows.length <= 1} onClick={() => patch("rows", rows.slice(0, -1))}>마지막 행 삭제</Button>
          <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || columns.length >= 30} onClick={() => onChange({ ...c, columns: [...columns, ""], rows: rows.map(row => [...row, ""]) })}>열 추가</Button>
          <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || columns.length <= 1} onClick={() => onChange({ ...c, columns: columns.slice(0, -1), rows: rows.map(row => row.slice(0, -1)) })}>마지막 열 삭제</Button>
        </div>
      </div>;
        }
        case "metrics": return <div className="flow-content-fields">{heading}
      {list("items").map((item, index) => {
                const id = str(item.id);
                return <div className="flow-content-item" key={id || index}>
        {itemHeading("수치", index)}
        <TextField label="이름" value={str(item.label)} onChange={value => editItem("items", index, { label: value })} maxLength={500} disabled={busy}/>
        <div className="flow-content-field-pair su-grid">
        <TextField label="값" value={str(item.value)} onChange={value => editItem("items", index, { value })} maxLength={2000} disabled={busy}/>
        <TextField label="단위" value={str(item.unit)} onChange={value => editItem("items", index, { unit: value })} maxLength={100} disabled={busy}/>
        </div>
        <TextField label="설명" value={str(item.detail)} onChange={value => editItem("items", index, { detail: value })} maxLength={2000} disabled={busy}/>
        {itemActions("items", index, list("items").length, "수치")}
      </div>;
            })}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || list("items").length >= 40} onClick={() => addItem("items", { id: crypto.randomUUID(), label: "", value: "", unit: "", detail: "" })}>수치 추가</Button>
    </div>;
        case "chart": return <div className="flow-content-fields">{heading}
      <TextField label="공통 단위" value={field("unit")} onChange={value => patch("unit", value)} maxLength={100} disabled={busy}/>
      {list("items").map((item, index) => {
                const id = str(item.id);
                return <div className="flow-content-item" key={id || index}>
        {itemHeading("항목", index)}
        <TextField label="이름" value={str(item.label)} onChange={value => editItem("items", index, { label: value })} maxLength={500} disabled={busy}/>
        <div className="flow-content-field-pair su-grid">
        <label className="flow-content-field">값<Input type="number" min={0} value={str(item.value)} disabled={busy} onChange={event => editItem("items", index, { value: event.target.value === "" ? null : Number(event.target.value) })}/></label>
        <TextField label="개별 단위" value={str(item.unit)} onChange={value => editItem("items", index, { unit: value })} maxLength={100} disabled={busy}/>
        </div>
        {itemActions("items", index, list("items").length, "항목")}
      </div>;
            })}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || list("items").length >= 100} onClick={() => addItem("items", { id: crypto.randomUUID(), label: "", value: 0, unit: "" })}>항목 추가</Button>
      <TextField label="캡션" value={field("caption")} onChange={value => patch("caption", value)} maxLength={3000} disabled={busy}/>
    </div>;
        case "gallery": return <div className="flow-content-fields">{heading}
      {list("images").map((item, index) => {
                const id = str(item.id);
                return <div className="flow-content-item" key={id || index}>
        {itemHeading("이미지", index)}
        {itemImageButton("images", item, index)}
        <TextField label="대체 텍스트" value={str(item.alt)} onChange={value => editItem("images", index, { alt: value })} maxLength={1000} disabled={busy}/>
        <TextField label="캡션" value={str(item.caption)} onChange={value => editItem("images", index, { caption: value })} maxLength={3000} disabled={busy}/>
        {itemActions("images", index, list("images").length, "이미지")}
      </div>;
            })}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || list("images").length >= 100} onClick={() => addItem("images", { id: crypto.randomUUID(), src: "", alt: "", caption: "" })}>이미지 추가</Button>
    </div>;
        case "steps": return <div className="flow-content-fields">{heading}
      {list("steps").map((item, index) => {
                const id = str(item.id);
                return <div className="flow-content-item" key={id || index}>
        {itemHeading("단계", index)}
        <TextField label="이름" value={str(item.title)} onChange={value => editItem("steps", index, { title: value })} maxLength={500} disabled={busy}/>
        <TextField label="설명" value={str(item.text)} onChange={value => editItem("steps", index, { text: value })} rows={3} maxLength={3000} disabled={busy}/>
        {itemActions("steps", index, list("steps").length, "단계")}
      </div>;
            })}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || list("steps").length >= 100} onClick={() => addItem("steps", { id: crypto.randomUUID(), title: "", text: "" })}>단계 추가</Button>
    </div>;
        case "references": return <div className="flow-content-fields">{heading}
      {list("items").map((item, index) => {
                const id = str(item.id);
                return <div className="flow-content-item" key={id || index}>
        {itemHeading("자료", index)}
        <TextField label="이름" value={str(item.title)} onChange={value => editItem("items", index, { title: value })} maxLength={500} disabled={busy}/>
        <TextField label="설명" value={str(item.detail)} onChange={value => editItem("items", index, { detail: value })} maxLength={3000} disabled={busy}/>
        <TextField label="주소" value={str(item.href)} onChange={value => editItem("items", index, { href: value || undefined })} maxLength={2048} disabled={busy}/>
        {itemActions("items", index, list("items").length, "자료")}
      </div>;
            })}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy || list("items").length >= 100} onClick={() => addItem("items", { id: crypto.randomUUID(), title: "", detail: "" })}>자료 추가</Button>
    </div>;
        case "file": return <div className="flow-content-fields">
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} onClick={() => onPick("file", block.id, "href")}>{field("href") ? "다른 파일 선택" : "파일 선택"}</Button>
      <TextField label="파일 이름" value={field("name")} onChange={value => patch("name", value)} maxLength={500} disabled={busy}/>
      <TextField label="형식" value={field("type")} onChange={value => patch("type", value)} maxLength={100} disabled={busy}/>
      <TextField label="크기" value={field("size")} onChange={value => patch("size", value)} maxLength={100} disabled={busy}/>
      <TextField label="설명" value={field("description")} onChange={value => patch("description", value)} rows={3} maxLength={3000} disabled={busy}/>
      <TextField label="주소" value={field("href")} onChange={value => patch("href", value || undefined)} maxLength={2048} disabled={busy}/>
    </div>;
        case "code": return <div className="flow-content-fields">{heading}
      <TextField label="언어" value={field("language")} onChange={value => patch("language", value)} maxLength={100} disabled={busy}/>
      <TextField label="코드" value={field("code")} onChange={value => patch("code", value)} rows={8} maxLength={100000} disabled={busy}/>
    </div>;
        case "audio": return <div className="flow-content-fields">{heading}
      <Button type="button" color="primary" variant="outline" pill={false} size="sm" disabled={busy} onClick={() => onPick("audio", block.id, "src")}>{field("src") ? "다른 오디오 선택" : "오디오 선택"}</Button>
      <TextField label="캡션" value={field("caption")} onChange={value => patch("caption", value)} maxLength={3000} disabled={busy}/>
      <TextField label="대본" value={field("transcript")} onChange={value => patch("transcript", value)} rows={5} maxLength={50000} disabled={busy}/>
    </div>;
        case "resource": return <div className="flow-content-fields">
      <div className="flow-content-item"><strong>{field("title")}</strong>{field("detail")&&<span>{field("detail")}</span>}</div>
    </div>;
        default: return <p>이 형식은 여기서 편집할 수 없습니다.</p>;
    }
}
