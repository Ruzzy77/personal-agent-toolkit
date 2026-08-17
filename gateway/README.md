# Personal Agent Tunnel Gateway

이 package는 로컬에 설치된 Sense, Corpus와 Hypes를 사용자의 OpenAI Secure MCP Tunnel에
연결한다. 선택한 제품의 로컬 MCP를 실행하고 고정된 loopback 경로로 전달하며, 제품 데이터나
별도 사용자 저장소는 만들지 않는다.

설치와 연결 방법은 [`GUIDE.md`](./GUIDE.md)에 있다. Gateway를 사용하지 않아도 세 제품의
로컬 기능은 그대로 동작한다.

이 폴더가 Gateway의 소스이자 실행 경로다. 별도 owner 저장소, 생성 사본과 release builder를
두지 않는다.
