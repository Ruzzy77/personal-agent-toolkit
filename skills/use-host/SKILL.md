---
name: use-host
description: Use the host_* tools to search, read, write and run commands in the owner's workspace roots on the always-on host. Paths are relative to a root; a normal write needs only root, path and content.
---

# Host 작업공간 사용

`host_roots`로 root 목록과 권한을 확인한 뒤 작업한다. 경로는 root 기준 상대 경로이며, 한 도구가 돌려준 경로는 다른 도구의 입력에 그대로 쓴다.

새 작업 폴더는 작업공간 root(`workspace`) 아래에 만들어 바로 읽고 쓰고 실행한다. 폴더를 새로 만들었다는 이유로 Corpus Space나 Connection을 등록하지 않는다. 등록은 그 폴더를 Source 색인이나 원격 Work에 연결할 때만 한다. 기존 Connection root ID도 그대로 쓸 수 있고, 같은 파일을 다른 root로 바꿔 접근해 권한을 넓히지 않는다.

- 읽기: `host_read`에 여러 파일과 행 범위를 한 번에 넘긴다. 큰 파일은 `host_search`로 좁힌 뒤 범위를 읽는다.
- 쓰기: `host_write`는 `root`, `path`, `content` 세 필드로 끝난다. 동시 수정을 막아야 할 때만 `host_read`가 돌려준 `version`을 `expected_version`으로 넣는다.
- 실행: `host_exec`는 root를 `/workspace`로 마운트한 컨테이너에서 실행한다(기본 네트워크 차단, 루트 파일시스템 읽기 전용). `profile`로 설치된 실행환경을 선택하며, 웹·문서 작업 전에 필요할 때만 `host_capabilities`의 사용 가능 프로필과 기능을 확인한다. `https_hosts`는 소유자가 미리 허용한 HTTPS 연결 목적지 중 이번 작업에 필요한 것만 지정한다. 생략하면 기존 기본 환경과 네트워크 차단을 유지한다. 허용되지 않은 주소를 다른 전송이나 SSH로 우회하지 않는다. 짧게 끝나면 결과를, 아니면 `job_id`와 `queued`/`running`을 돌려준다. 이어지는 상태와 출력은 `host_job`으로 읽고, 중단은 `host_job_cancel`. 컨테이너가 쓴 파일은 바로 `host_read`로 읽힌다.
- 지정된 업무 원본과 규정 자료는 작업공간 안에 있어도 모든 경로에서 읽기 전용이다. 쓰기는 `policy_denied`로 거부되고 컨테이너에서도 읽기 전용으로 마운트된다. 우회할 방법을 찾지 말고 사본을 만들어 작업한다.
- 오류는 `isError`와 `code: message`(`invalid_path`, `not_found`, `policy_denied`, `version_conflict`, `marker_not_found`, `marker_ambiguous` 등)로 돌아온다. 같은 요청을 그대로 반복하지 말고 원인을 고친다.
- Spark 작업공간의 경로를 클라이언트 로컬 셸이나 파일 도구로 다루지 않는다.\n\n\n## Host 파일 기능과 전송

- host_files의 list는 폴더 목록, stat은 파일 메타데이터와 version, mkdir은 새 폴더, move는 이동·이름 변경, trash는 30일 복구 가능한 삭제, trash_list는 휴지통 목록, restore는 복원이다.
- move와 trash에는 stat 또는 읽기 결과의 expected_version이 필요하다. restore는 기존 파일을 덮어쓰지 않는다. Finder 직접 삭제는 30일 복구 대상이 아니다.
- host_transfer는 최대 1 GiB, 청크 최대 8 MiB다. 반환된 transfer_url에 X-Toolkit-Transfer-Token 헤더를 사용한다. 업로드는 PUT /chunk에 Upload-Offset 헤더를 넣고, GET /status로 현재 offset을 확인해 재개한 뒤 POST /commit한다. 다운로드는 GET /content, 취소는 DELETE /status다.
- HTML 미리보기는 transfer URL에 ?preview=1을 붙여 passive preview로 요청한다. 실행 가능한 내용은 제거된 안전한 미리보기이며, 자격 증명은 URL·로그가 아닌 헤더로만 보낸다.
