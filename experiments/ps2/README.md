# 동결된 v0.1로 PS/2 키보드 수신

[실행 확인] 검증 계획에서 미리 선정한 PS/2를 처음 시도하기 전, 2026-11-21 04:52 UTC에 커밋 d27a259의 ISA·RTL·어셈블러·ISS를 고정했다. 변경 전후 src Git 트리는 f77dcf4fe7cfcece2d6fcb564b6d7f9be4818e47이다. 같은 어셈블러로 수신 프로그램을 빌드하면 46워드(92바이트), SHA-256 92845eb17ad611b63df16a3deb59a0b63664f06194badb008056ed2049229938이다. [동결 파일별 해시](frozen-inputs.json) · [사전 선정 근거](../../docs/verification_plan.md) · [고정한 회로의 공식 실행](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44863887949)

[추론] CLOCK=uio[0], DATA=uio[1]이고 키보드가 두 선을 구동한다. 수신 프로그램은 외부 클록의 HIGH와 LOW를 차례로 기다리고, 하강 직후 데이터가 안정된 동안 표본을 읽는다. 1바이트는 시작 1비트, LSB 우선 데이터 8비트, 홀수 패리티 1비트, 정지 1비트다. 이 실험은 유휴 상태에서 시작해 11클록을 끝까지 보낸 장치→호스트 프레임을 다룬다. 호스트가 FIFO를 제때 읽는 조건이며 잘린 프레임의 복구와 호스트→장치 명령은 시험 범위 밖이다. [수신 프로그램](../../examples/ps2_rx.asm) · [Digilent Nexys2 설명서, 8쪽](https://digilent.com/reference/_media/nexys/nexys2/nexys2_rm.pdf)

| [실행 확인] 검증 계층 | 자극 | 관측한 결과 |
|---|---|---|
| 독립 ISS | 엔진 2 MHz × PS/2 10·12.5·16 kHz, 엔진 50 MHz × PS/2 16 kHz. 각 조합에서 256종 정상 바이트와 9개 오류 프레임 | 정상 1,024/1,024바이트 순서 일치, 오류 36/36개 미입큐. 총 10,545,998 엔진 에지에서 FAULT=0, OE=0 |
| RTL 핀, 공개 배선 후 넷리스트 핀 | 각 계층의 전체 시험에 275프레임. 2 MHz에서 전체 바이트 값과 세 가지 외부 속도를 검사하고, 50 MHz에서 12.5 kHz 연속 2프레임을 별도 검사 | 각 계층에서 정상 268바이트 순서 일치, 오류 7개 미입큐. 71묶음에서 읽기 후 RX 재시도 응답. 256종 중 128프레임은 상승 에지에 반대 데이터 |
| 검사기 음성 대조 | 송신선의 잘못된 패리티 한 비트를 정상으로 복구하고 예상 결과는 동결 | 정상으로 바뀐 프레임이 실제 RX에 들어오자 두 번째 읽기에서 기대 0x81, 실제 0x01로 실패 |

[실행 확인] Claude Code의 프로그램 작성자와 프로토콜 리뷰어가 역할을 나누었고, Codex의 ISS·핀 검증자는 수신 어셈블리와 RTL 본문을 열지 않고 같은 바이너리를 검사했다. 핀 시험은 실제 고정 로더로 적재하고 STATUS·RX 응답·OE를 관측한다. 공개 넷리스트 SHA-256은 bedf9151ca2489f89f1255c5a9565ffc803368eba4049ede829c66a6291f5022다. [핀 검사기](../../test/test_heldout_ps2.py)

## 재현

저장소 루트에서 다음을 실행한다. 기본 cocotb 회귀에도 두 핀 시험이 포함된다. 전체 256종을 추가할 때 PS2_FULL=1을 사용한다. 환경은 기존 v0.1 회귀와 같다.

```sh
python -m model.assembler examples/ps2_rx.asm /tmp/ps2_rx.bin
sha256sum /tmp/ps2_rx.bin
python experiments/ps2/check_iss.py /tmp/ps2_rx.bin
(cd test && PS2_FULL=1 make COCOTB_TEST_MODULES=test_heldout_ps2 && python -m cocotb_tools.check_results results.xml)
git diff --exit-code d27a2592d16f156b7ae0f16a2ff42825e53a9cb5 8644881957b3a93adc175bda7f87e4e34409097c -- src spec/spec.md model/assembler.py model/iss.py
```

[미실행] 실물 RP2350과 PS/2 키보드를 연결한 측정.

## 사람이 직접 확인할 지점

- 어셈블한 이미지의 해시와 고정 입력에 대한 git diff를 확인한다.
- 패리티 오류 뒤의 정상 바이트가 순서대로 도착하고, 오류만 보낸 묶음에서 RX가 비어 있는지 본다.
- 핀 검사기에서 상승 에지만 반전한 자극과 OE 감시 코드를 확인한다.
