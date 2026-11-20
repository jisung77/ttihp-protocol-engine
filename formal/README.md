# 포트 기반 형식 검증

[실행 확인] `pe_fifo.sby`의 8개 ABC PDR 작업과 Z3 cover 작업, `pe_engine.sby safety`와 `pe_opcode.sby`의 ABC PDR 작업이 통과했다. 기준은 [§1·§2·§5·§6](../spec/spec.md)이다. 아래 명령은 WSL 리포지터리의 `formal/`에서 실행한다. SBY 2023년 Yosys 0.33 호환 버전과 Yosys 0.33, ABC, Z3 4.8.12를 사용했다.

```sh
sby -j 3 pe_fifo.sby
sby pe_engine.sby
sby pe_opcode.sby
```

## FIFO

첫 상승 에지에 리셋을 가정한다. 이후 reset_n, clear, push, pop과 8비트 입력 데이터는 매 에지 자유롭다. 모델은 깊이 4의 사용 슬롯을 단항 비트 마스크에 기록한다. 허가된 쓰기가 들어오면 최근 4개 값의 같은 비트를 밀어 넣고, 점유량으로 가장 오래된 항목을 선택한다. 모델의 수락 여부는 모델 점유량에서만 계산한다.

각 비트 작업에서 모든 도달 가능 상태의 count(0~4), empty/full에 해당하는 count=0/4, 비어 있지 않을 때 head의 해당 비트, 리셋·clear 후 공백을 확인한다. 8개 작업이 같은 임의 입력열 전체를 다루므로 합치면 바이트 전체의 순서와 값이 보장된다. 빈 큐의 head는 명세상 자유값이다. 매 에지의 사전 점유량으로 동시 요청을 판정한다. accepted 포트가 없는 모듈이므로 입출력 허가는 그 결과인 점유량과 읽을 데이터에 대해 검증한다.

| Z3 도달 목표 | 최초 증거 스텝 |
|---|---:|
| 동시 요청, 사전 점유량 0 → 1 | 3 |
| 동시 요청, 사전 점유량 1 → 1 | 4 |
| 동시 요청, 사전 점유량 3 → 3 | 6 |
| 동시 요청, 사전 점유량 4 → 3 | 7 |
| 가득 찬 큐에서 push/pop 동시 요청과 reset | 7 |
| 가득 찬 큐에서 push/pop 동시 요청과 clear | 7 |
| reset·clear 없이 쓰기/읽기 각 8회 이상 수락하고 empty에 복귀 | 11 |

마지막 witness는 깊이 4의 저장 공간을 두 차례 순환하는 트래픽이다. 공개 인터페이스로 관찰 가능한 전송에 대한 결과다.

## 엔진

`safety`는 최초 한 에지에 reset 또는 deselect를 가정한다. 이후 호스트와 프로토콜 입력, 선택, 리셋은 자유롭다. TT 핀과 §2의 pc, retire, tx_count, rx_count만 관측한다. 프로그램 SRAM의 반환값은 매 클록 임의의 16비트 값으로 허용한다. 이 확대된 입력에서도 다음 안전 속성이 무한 PDR로 성립한다.

- reset·deselect 및 두 해제 에지 동안 외부 응답·출력·OE, PC, retire와 FIFO 점유가 0이다. 각 큐의 점유량은 항상 4 이하다.
- 정지 상태의 OE는 0이다. RUNNING이면 READY이며 FAULT가 0이다. FAULT이면 정지 상태이며 OE와 retire가 0이다.
- RUN 시작 상태는 PC=0, OE=0이다. 연속 실행 중 은퇴하지 않은 에지에서는 PC와 OE를 유지한다. FAULT가 처음 올라오는 에지에서는 PC를 유지하고 은퇴하지 않는다.

`bounds`는 호스트가 reset 해제 중 REQ=0을 유지하고 ACK 전까지 REQ와 니블을 유지하는 조건에서, 실제 동기 SRAM 모델(`PE_SIM_SRAM`)과 외부 ACK의 독립 프레임 해석을 사용한다. 기본 실행은 safety만 수행한다. 진단은 `sby pe_engine.sby bounds`로 따로 실행한다. 성공한 BEGIN의 길이를 기록하여 RUNNING인 모든 에지에 `pc < length <= 256`을 검사한다. [실행 확인] 이 작업은 240초 제한에서 TIMEOUT(rc=8)으로 종료했다. PC 안정성·fault 전이의 PASS와 구분하며, 임의 길이 이미지에 대한 PC 범위는 미증명이다.

`pe_opcode.sby`는 프로그램 SRAM의 등록 출력 `instruction[15:0]`(pe_sram.dout → pe_core.instruction)을 관찰한다. SRAM 저장 배열의 원시 읽기 값만 임의화하고 실제 동기 읽기·보존 회로를 유지한다. 최초 한 에지의 reset 또는 deselect를 제외한 입력은 자유롭다. S 직전 인출 워드의 opcode를 저장하여 stall 후 R까지 추적한다. 명령 해석이 정확하다는 가정은 없다.

- [실행 확인] 연속 RUNNING 중 미은퇴 에지에서 PC는 유지된다. 은퇴한 opcode가 BR·DJNZ 이외이면 다음 PC는 9비트로 계산한 이전 PC+1과 일치한다. 255에서 0으로의 순차 래핑도 검출 대상이다.
- [실행 확인] 연속 RUNNING 중 OE가 바뀌면 그 에지에서 COMMIT이 은퇴한다. RUN 시작과 정지 시 OE는 0이다. 실행 종료 시 PC는 유지되거나 0으로 초기화된다. fault 시 PC 보존과 retire 금지는 별도 safety 작업에서 확인했다.

[미실행] SRAM 매크로의 물리 타이밍, 클록 에지 사이의 아날로그 글리치, 개별 opcode가 생성하는 모든 정상 출력은 이 증명의 범위 밖이다. 각 분기에 지정된 목적지와 분기 조건의 정확성은 별도 ISA 검증 항목이다.

실행 당시 Git 기준점, 정확한 입력 SHA-256, 명령·버전, 원본 로그·판정·VCD는 `D:\TinyTapeout\evidence\engine-mvp-1795190317902\formal\`의 `*-inputs.txt`, `run-*.sh`, `fifo_*/PASS`, `engine_exact_safety/PASS`, `opcode/PASS`, `engine_exact_bounds/status`에 있다. 실행 당시 체크아웃의 미커밋 입력은 기록한 SHA-256으로 식별한다.

## 사람이 직접 확인할 지점

- `fifo_cover/engine_0/trace5.vcd`에서 가득 찬 큐의 동시 요청 후 count가 3이 되는지 확인한다.
- `fifo_cover/engine_0/trace6.vcd`에서 8번 이상의 쓰기와 읽기가 수락된 뒤 다시 비는지 확인한다.
- 엔진 `opcode/PASS`와 `engine_exact_bounds/status`를 대조해 opcode별 상태 전이와 이미지 길이 검사의 판정을 확인한다.
