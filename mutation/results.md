# RTL 변이 회귀 결과

[실행 확인] 동결한 회귀에서 15개 중 14개(93.33%)를 검출했다. STOP 에지 검사를 추가하고 동일한 15개를 처음부터 다시 실행해 15개 모두(100%)를 검출했다. 두 버전 모두 90% 기준을 충족했다. [시험별 결과와 원본 XML 해시](results.json)

| 회귀 | 원본 통과 | 검출 | 생존 | 검출률 |
|---|---|---:|---|---:|
| 최초 동결 | 코어 5/5, 확장 프로토콜 8/8 | 14/15 | M15 | 93.33% |
| STOP 시험 추가 후 | 코어 6/6 | 15/15 | 0개 | 100% |

M15는 수정 전 코어 5개와 확장 프로토콜 8개를 모두 통과했다. 수정 후에는 정규 STOP 시험에서 `(pc, exec_start, retire)`가 기대값 `(0, 0, 0)`과 다른 `(0, 1, 1)`로 관측됐다. [STOP 시험](../test/test.py#L246) · [검증 결함 기록](../docs/bug_log.md)

| 변이 | 명세 | 의도한 변경 | 수정 전 → 후 | 검출 시험 또는 대조 기록 | 관측한 차이 |
|---|---|---|---|---|---|
| [M01](M01/project.v) | §2 | 2FF 우회 | 검출 → 검출 | [`selected_pc_wait_fifo_and_fault`](../test/test.py#L86) | K+4에 WAIT가 일찍 은퇴해 PC가 1 대신 2 |
| [M02](M02/pe_core.v) | §3 | COMMIT 마스크 무시 | 검출 → 검출 | [`seeded_cycle_differential`](../test/test.py#L150) | seed 1, K+52 출력이 0x00 대신 0x04 |
| [M03](M03/pe_core.v) | §6 | 리셋 중 OE 비트 0 구동 | 검출 → 검출 | [`first_pin_trace_and_loader_errors`](../test/test.py#L67) | 리셋 중 uio_oe가 0 대신 1 |
| [M04](M04/pe_core.v) | §4 | DELAY 조기 완료 | 검출 → 검출 | [`seeded_cycle_differential`](../test/test.py#L150) | seed 0, K+4에 retire가 0 대신 1 |
| [M05](M05/pe_core.v) | §4 | 마지막 WAIT 에지에 만료 우선 | 검출 → 검출 | [`selected_pc_wait_fifo_and_fault`](../test/test.py#L86) | K+7에 BR OK 후 PC가 6 대신 3 |
| [M06](M06/pe_core.v) | §3 | BR OK 조건을 TIMEOUT으로 변경 | 검출 → 검출 | [`selected_pc_wait_fifo_and_fault`](../test/test.py#L86) | K+7에 BR OK 후 PC가 3 대신 6 |
| [M07](M07/pe_core.v) | §3 | LC=1에서도 DJNZ 분기 | 검출 → 검출 | [`seeded_cycle_differential`](../test/test.py#L150) | seed 0, K+47에 PC가 21 대신 20 |
| [M08](M08/pe_core.v) | §3 | MSB 우선 SHIN을 역방향으로 삽입 | 검출 → 검출 | [`selected_pc_wait_fifo_and_fault`](../test/test.py#L86) | 호스트 RX가 0x01 대신 0x80 |
| [M09](M09/pe_core.v) | §3 | LSB 우선 SHOUT에서 OSR 역방향 이동 | 검출 → 검출 | [`seeded_cycle_differential`](../test/test.py#L150) | seed 4, K+60 출력이 0x71 대신 0x70 |
| [M10](M10/pe_core.v) | §3 | 범위 검사 전에 다음 PC를 8비트로 절단 | 검출 → 검출 | [`selected_pc_wait_fifo_and_fault`](../test/test.py#L86) | PC 255에서 fault 대신 PC 0으로 이동하고 RX push 발생 |
| [M11](M11/pe_core.v) | §5 | RX 점유량 3에서 엔진 PUSH 거절 | 검출 → 검출 | [`branch_fifo_reset_coverage`](../test/test.py#L202) | 실제 점유량 4의 관측 횟수가 0 |
| [M12](M12/pe_fifo.v) | §5 | FIFO 읽기 포인터를 2씩 증가 | 검출 → 검출 | [`fifo_stop_reset_and_reload`](../test/test.py#L110) | RX 순서가 [1, 3, 7, 15]에서 [1, 7, 1, 7]로 변경 |
| [M13](M13/pe_host.v) | §5–6 | TX 가득 참에서 상태 거절 반환 | 검출 → 검출 | [`fifo_stop_reset_and_reload`](../test/test.py#L110) | 호스트 응답이 재시도 1 대신 거절 2 |
| [M14](M14/pe_host.v) | §6 | 로더 합을 XOR로 계산 | 검출 → 검출 | [`first_pin_trace_and_loader_errors`](../test/test.py#L67) | 유효 이미지 END 응답이 성공 0 대신 검산 오류 3 |
| [M15](M15/pe_core.v) | §2·§6 | STOP 수락 에지의 실행 허용 | 생존 → 검출 | [`accepted_stop_preempts_same_edge_retire`](../test/test.py#L246) | K+6 STOP 수락 직후 retire가 0 대신 1 |

M01~M14의 첫 실패는 모두 XML에 남은 `AssertionError`다. M11은 네 번째 PUSH 수락에 도달하지 못해 점유량 4 관측 횟수가 0이었다. STOP 수락과 명령 완료가 같은 에지에서 겹치는 M15는 [별도 진단](stop_witness.py)에서 동작 차이를 확인했다.

## 입력과 수용 기준

최초 동결 시점의 HEAD는 `c6397f553aa1349ef04e2ecb2d9944d34fee934f`이다. 모든 RTL·모델·시험 입력 해시는 후속 커밋 `ccfb4faa10f522e61cf83d4d3c5e5371e1008bf1` 및 `test/test.py` 마지막 빈 줄 하나로 재현한다. STOP 회귀를 추가한 버전은 `37963ca1189e088ff444d8dcf439d33fd5b70523`이다. Python 3.12, Icarus Verilog 12.0, cocotb 2.0.1, seed 18455 및 IHP SRAM 기능 모델을 사용했다. [재현 명령](README.md) · [치환 목록](manifest.json) · [원본 SHA-256·시험 결과](results.json)

분모는 컴파일 가능한 비등가 변이 15개다. M01~M14는 원본과 다른 코어 단언 결과가 있고 M15는 STOP 수락 에지의 독립 재현이 있다. 컴파일 실패와 입증된 등가는 각각 0개다. 검출률 수용 기준은 90%로, 검출 14개 이상이다. [검증 계획](../docs/verification_plan.md)
