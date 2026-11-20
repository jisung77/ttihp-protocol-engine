# 검증 결함 기록

## V-001 · STOP 수락 에지의 잘못된 은퇴 펄스

| 항목 | 기록 |
|---|---|
| 명세 | §2에서 STOP 수락 에지에는 retire가 없다. §6에서 STOP은 같은 클록의 엔진 명령에 우선한다. [§2](../spec/spec.md#s2) · [§6](../spec/spec.md#s6) |
| 재현 입력 | 한 워드 `0x5000`(BR 0)을 적재해 RUN한다. RUN 수락 K 직후 STOP의 두 니블을 연속 전송하면 K+6 실행 에지에서 STOP을 수락한다. [재현 시험](../mutation/stop_witness.py) |
| 증상 | [실행 확인] 원본과 M15 양쪽에서 STOP 응답은 0이고 실행이 끝난다. 같은 에지 직후 원본 retire는 0, M15 retire는 1이다. 변이는 수정 전 코어 시험 5개를 모두 통과했다. [M15 사본](../mutation/M15/pe_core.v) · [변이 결과](../mutation/results.json) |
| 원인·검출 계층 | 당시 cocotb 회귀는 STOP 뒤의 상태를 검사했다. STOP이 명령과 겹치는 에지의 retire를 직접 검사해야 이 동작 차이를 구별한다. 원본 RTL은 해당 재현을 통과했다. |
| 수정 커밋 | `37963ca1189e088ff444d8dcf439d33fd5b70523`에서 [정규 STOP 회귀](../test/test.py#L246)를 추가했다. 원본은 BR 0과 대기 중 WAIT HIGH가 각각 STOP과 겹칠 때 모두 통과했다. M15는 새 시험에서만 실패했다. 실제 `(pc, exec_start, retire)`는 `(0, 1, 1)`, 기대값은 `(0, 0, 0)`이었다. [수정 전후 시험별 판정 및 XML SHA-256](../mutation/results.json) |
