# 포트 기반 형식 검증

[실행 확인] `pe_fifo.sby`의 8개 ABC PDR 작업과 Z3 cover 작업, `pe_engine.sby safety`와 `pe_opcode.sby`의 ABC PDR 작업이 통과했다. 기준은 [§1·§2·§5·§6](../spec/spec.md)이다. 아래 명령은 WSL 리포지터리의 `formal/`에서 실행한다. SBY 2023년 Yosys 0.33 호환 버전과 Yosys 0.33, ABC, Z3 4.8.12를 사용했다.

```sh
sby -j 3 pe_fifo.sby
sby pe_engine.sby
sby pe_opcode.sby
sby pe_dual.sby slots ownership mode_cover
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

## 단독 엔진

[실행 확인] §9.1의 단독 동작을 검증할 때 `pe_engine_formal`과 `pe_opcode_formal`은 E1~E3에 안정된 일곱 단독 strap 중 하나를 가정한다. E3 이후 ui[7:5]에는 이 제한이 없다. 같은 RTL에 각 strap으로 §7 프로그램을 적재했을 때 일곱 경우 모두 K+6에 구동하고 K+8에 해제했다.

[실행 확인] 기존 단독 속성 `!running → uio_oe == 0`은 듀얼에서 그대로 사용할 수 없다. 예를 들어 한 컨텍스트의 COMMIT이 출력 소유권을 위반한 뒤 그 대상을 STATUS로 선택하면 FAULT=1, RUNNING=0, READY=1이다. 상대가 계속 실행 중이므로 핀 OE는 0x01 또는 0x10이다. 단독 strap 범위에서 원래 안전성·opcode assertions를 그대로 증명하고 듀얼은 §9.5의 개별 관측값으로 검사한다.

`safety`는 최초 한 에지에 reset 또는 deselect를 가정한다. 위의 부트 strap 조건을 제외하면 호스트와 프로토콜 입력, 선택, 리셋은 자유롭다. TT 핀과 §2의 pc, retire, tx_count, rx_count만 관측한다. 프로그램 SRAM의 반환값은 매 클록 임의의 16비트 값으로 허용한다. 이 확대된 입력에서도 다음 안전 속성이 무한 PDR로 성립한다.

- reset·deselect 및 두 해제 에지 동안 외부 응답·출력·OE, PC, retire와 FIFO 점유가 0이다. 각 큐의 점유량은 항상 4 이하다.
- 정지 상태의 OE는 0이다. RUNNING이면 READY이며 FAULT가 0이다. FAULT이면 정지 상태이며 OE와 retire가 0이다.
- RUN 시작 상태는 PC=0, OE=0이다. 연속 실행 중 은퇴하지 않은 에지에서는 PC와 OE를 유지한다. FAULT가 처음 올라오는 에지에서는 PC를 유지하고 은퇴하지 않는다.

[실행 확인] `bounds_abstract`는 ABC PDR로 단독 모드의 모든 RUNNING 표본에서 **PC < 호스트 핀으로 선언한 길이 ≤ 256**을 무한 깊이로 증명했다. 길이는 외부 ACK와 니블로 성공한 BEGIN을 해석해 기록한다. 처음 세 에지의 단독 strap과 §6의 REQ·데이터 유지 규칙을 포함한 기존 환경 가정 다섯 개를 사용한다. 기존 단언 11개 전부를 검사한다.

[추론] 이 증명은 SRAM 읽기 데이터(16비트), 호스트가 적재한 워드 수(9비트), 누적 체크섬(16비트)을 각 클록에 자유롭게 선택하게 한다. 매번 실제 회로의 세 값을 선택하면 실제 실행도 재현되므로 증명된 범위는 실제 회로에도 적용된다. 이 때문에 짧은 적재를 END가 허용하는 실행도 탐색한다. END가 올바른 개수·체크섬만 수락하는지는 별도 속성이다. 이 작업의 수치 상한은 256이며, 길이 256 자체에서 8비트 PC의 래핑 동작은 아래 opcode 속성이 다룬다.

```sh
sby -d pc-proof pe_engine.sby bounds_abstract
python3 pc_bounds/reach.py pc-proof/model/design_prep.il pc-witness
```

[실행 확인] 위 SAT 도달 검사는 같은 준비 모델에 합법적인 호스트 핀 벡터를 주고 경계 상태의 존재를 확인한다. 길이 1·PC 0과 거절 코드 3 뒤 재적재한 길이 256·PC 255에서 각각 RUNNING=1인 VCD를 얻었다. 세 자유 신호는 고정하지 않는다. 실제 메모리 256워드를 모두 쓰는 사례는 아래 SRAM 핀 시험에서 확인한다.

[실행 확인] 원래 `bounds` 작업은 행동 SRAM과 적재 카운트·체크섬을 모두 유지한다. 이 경로와 SRAM 출력만 자유롭게 둔 이전 실험은 각각 240초에서 TIMEOUT이었다. 기본 `sby pe_engine.sby`는 기존 safety만 실행한다.

[실행 확인] 별도의 행동 SRAM 핀 시험은 적재 중 reset·선택 해제, 거부된 BEGIN, 정상 재적재, 길이 1의 PC 0과 길이 256의 PC 255를 포함한 13개 사례를 확인했다. 주소 검사 `next_pc >= length`에서 등호를 제거한 검증용 사본은 길이 1에 DATA 0을 적재하자 RUN 수락 뒤 두 번째 에지에 PC=1, RUNNING=1이 돼 원래 단언을 위반했다. 정상 회로는 그 에지에 PC=0, FAULT=1이었다. [핀 벡터](pc_bounds/pins_tb.sv) · [공식 회귀 실행 명령](../.github/workflows/test.yaml)

`pe_opcode.sby`는 프로그램 SRAM의 등록 출력 `instruction[15:0]`(pe_sram.dout → pe_core.instruction)을 관찰한다. SRAM 저장 배열의 원시 읽기 값만 임의화하고 실제 동기 읽기·보존 회로를 유지한다. 최초 한 에지에 reset 또는 deselect를 가정하며 E1~E3에는 위의 단독 strap 조건을 사용한다. 다른 입력은 자유롭다. S 직전 인출 워드의 opcode를 저장하여 stall 후 R까지 추적한다. 명령 해석이 정확하다는 가정은 없다.

- [실행 확인] 연속 RUNNING 중 미은퇴 에지에서 PC는 유지된다. 은퇴한 opcode가 BR·DJNZ 이외이면 다음 PC는 9비트로 계산한 이전 PC+1과 일치한다. 255에서 0으로의 순차 래핑도 검출 대상이다.
- [실행 확인] 연속 RUNNING 중 OE가 바뀌면 그 에지에서 COMMIT이 은퇴한다. RUN 시작과 정지 시 OE는 0이다. 실행 종료 시 PC는 유지되거나 0으로 초기화된다. fault 시 PC 보존과 retire 금지는 별도 safety 작업에서 확인했다.

[미실행] SRAM 매크로의 물리 타이밍, 클록 에지 사이의 아날로그 글리치, 개별 opcode가 생성하는 모든 정상 출력은 이 증명의 범위 밖이다. 각 분기에 지정된 목적지와 분기 조건의 정확성은 별도 ISA 검증 항목이다.

실행 당시 Git 기준점, 정확한 입력 SHA-256, 명령·버전, 원본 로그·판정·VCD는 `D:\TinyTapeout\evidence\engine-mvp-1795190317902\formal\`의 `*-inputs.txt`, `run-*.sh`, `fifo_*/PASS`, `engine_exact_safety/PASS`, `opcode/PASS`, `engine_exact_bounds/status`에 있다. 실행 당시 체크아웃의 미커밋 입력은 기록한 SHA-256으로 식별한다.

## 듀얼 엔진

[실행 확인] `pe_dual.sby slots`와 `ownership`의 무한 깊이 PDR가 통과했다. 전자는 슬롯 소유자·은퇴 상호배제·SRAM 태그 전달, 후자는 무권한 COMMIT·SHOUT에서 대상 OE 해제와 상대 핀 보존을 검사한다. 규범상의 공유 포트 관측값만 노출하며, 원시 SRAM 저장 배열은 비결정적인 데이터로 대체한다. `mode_cover`는 Z3에서 E3 뒤 듀얼 모드를 5스텝에 도달했다. [형식 속성](pe_dual_formal.sv)

[실행 확인] 같은 파일의 Icarus witness는 원래 행동 SRAM에 프로그램 두 개를 BEGIN/WRITE/END/RUN으로 적재한다. A/B × 위반 명령 2종의 네 경우에 두 대상 모두 인출·은퇴했고, fault 직전 양쪽 핀 구동과 직후 대상 OE만 해제되는 것을 확인했다. 아래 명령은 `formal/`에서 실행한다.

```sh
iverilog -g2012 -DPE_SIM_SRAM -DPE_DUAL_PIN_WITNESS -s pe_dual_pin_witness -o /tmp/dual-witness.vvp pe_dual_formal.sv ../src/*.v
vvp /tmp/dual-witness.vvp
```

### 두 컨텍스트의 PC와 SRAM 주소

[실행 확인] §6과 §9의 호스트 ACK에서 첫 니블의 대상과 성공한 BEGIN의 16비트 길이를 복원한다. 뱅크별 ABC PDR 두 작업이 통과했다. 성공한 RUN부터 같은 대상의 STOP·BEGIN 또는 초기화까지 매 에지에서 PC는 그 대상의 선언 길이보다 작다. 유효 인출의 물리 주소는 인출 직전 PC에 A는 0, B는 128을 더한 값이다. 명령 인출 값, 적재 워드 수와 체크섬은 임의 값으로 열어 두며, BEGIN 길이와 PC는 실제 회로에서 결정된다. [핀 기반 속성](pe_dual_bounds.sv) · [SBY 설정](pe_dual_bounds.sby)

[실행 확인] 독립 REVIEW 검사는 두 컨텍스트의 실제 RUNNING을 매 에지에 함께 관측한다. 호스트가 다른 쪽을 선택한 동안에도 실행 중인 PC는 핀에서 복원한 자기 길이보다 작았다. 마지막 성공 RUN의 길이를 따로 보관하면 정지 후 더 짧은 BEGIN을 받더라도 그 이전 RUN 길이에 대한 범위가 유지된다. 성공 RUN 표본의 PC는 0이고, 초기화 이후 두 PC의 최상위 비트는 0이다. [귀납 속성](dual_pc_bounds/review.sv) · [Yosys 재현 절차](dual_pc_bounds/review.ys)

[실행 확인] 행동 SRAM에서는 길이 1과 128을 두 방향으로 적재했다. 두 프로그램이 함께 실행되는 동안 각자의 마지막 PC와 주소 0·127·128·255의 인출을 확인했다. B의 홀수 인출 주소를 한 워드 앞으로 옮긴 격리 사본은 PDR 51스텝과 핀 시험 4,929번째 에지에서 검출됐다. [핀 시험](dual_pc_bounds/pins_tb.sv)

```sh
# formal/ 에서 실행
sby -d dual-a pe_dual_bounds.sby bank_a
sby -d dual-b pe_dual_bounds.sby bank_b
python3 dual_pc_bounds/reach.py dual-a/model/design_prep.il pair_1_128 0 1 128
python3 dual_pc_bounds/reach.py dual-b/model/design_prep.il pair_128_1 1 128 1
yosys -l review.log dual_pc_bounds/review.ys
iverilog -g2012 -DPE_SIM_SRAM -s dual_pc_bounds_pins -o /tmp/dual-pc.vvp dual_pc_bounds/pins_tb.sv ../src/*.v
vvp /tmp/dual-pc.vvp +trace
```

## 사람이 직접 확인할 지점

- `dual-pc-bounds` 산출물의 `pins.vcd`에서 B의 PC 127 인출 주소가 255이고 같은 구간의 A PC가 0인지 확인한다.
- `fifo_cover/engine_0/trace6.vcd`에서 8번 이상의 쓰기와 읽기가 수락된 뒤 다시 비는지 확인한다.
- `bounds_abstract` 로그의 귀납 불변식 검증 성공과 핀 로그의 길이 256·PC 255 표본을 확인한다.
