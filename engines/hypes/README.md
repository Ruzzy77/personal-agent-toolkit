# Hypes local engine

Hypes의 로컬 개발·최초 이관용 Python 구현입니다. 운영 plugin은
`services/remote-context`의 원격 MCP를 사용하며 이 engine의 SQLite 자료와 자동 동기화하지
않습니다.

```sh
uv sync --frozen
./launchers/hypes-mcp
```

기본 자료는 `~/Library/Application Support/Hypes/`에 둡니다. 저장 모델과 원격 경계는
[`plugins/hypes/DESIGN.md`](../../plugins/hypes/DESIGN.md)를 따릅니다.

## 라이선스

[Apache License 2.0](LICENSE). [NOTICE](NOTICE)를 함께 따릅니다.
