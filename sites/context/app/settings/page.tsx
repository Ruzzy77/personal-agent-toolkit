import Link from "next/link";
import { requireOwnerUser } from "../../lib/owner-service";
export const dynamic="force-dynamic";
export default async function Settings(){
 await requireOwnerUser("/settings");
 return <main id="main-content" className="su-workspace su-stack" data-gap="section"><h1>설정</h1><section className="su-section"><h2>자료 관리</h2><div className="su-row"><Link className="su-btn" href="/manage">Corpus</Link><Link className="su-btn" href="/manage/sense">Sense</Link><Link className="su-btn" href="/manage/library">Library</Link><Link className="su-btn" href="/manage/design">디자인 자료</Link></div></section><section className="su-section"><h2>작업공간 연결</h2><p>Finder와 에이전트에서 Spark의 같은 파일을 사용할 수 있습니다.</p><a href="smb://spark-1de5.tail556ab.ts.net/Agent-Workspace">Finder로 열기</a></section></main>;
}
