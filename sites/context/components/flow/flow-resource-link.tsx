"use client";

import Link from "next/link";
import type { MouseEventHandler } from "react";
import type { FlowLinkedResource } from "../../lib/flow-content";
import { flowResourceHref } from "../../lib/flow-handoff";

export function FlowResourceLink({reference,variant="button",onClick}: {
  reference:FlowLinkedResource|null|undefined;
  variant?:"button"|"text";
  onClick?:MouseEventHandler<HTMLAnchorElement>;
}) {
  const href=reference?flowResourceHref(reference):null;
  if(!href)return null;
  return <Link href={href} className={variant==="button"?"su-btn":undefined} onClick={onClick}>작업 화면에서 열기</Link>;
}
