# 프로그래머블 프로토콜 엔진

[실행 확인] 16비트 명령을 적재해 여덟 양방향 핀의 데이터·방향·입력 샘플 시점을 제어하는 단일 컨텍스트 엔진이다. IHP CMOS5L용 Tiny Tapeout 인터페이스와 256×16 단일 포트 SRAM을 사용한다. UART TX/RX 및 네 가지 SPI 마스터 모드는 같은 RTL에서 서로 다른 프로그램으로 실행된다. [핀맵과 첫 적재 예제](docs/info.md) · [명령어와 클록 규칙](spec/spec.md)

## 첫 프로그램

호스트에서 다음 명령을 실행하면 UART 송신 예제가 로더에 넣을 수 있는 리틀엔디언 바이너리가 된다. 준비된 프로그램은 FIFO에서 바이트를 받아 핀 0에 8N1 프레임을 출력한다. [어셈블리와 예제 설명](examples/README.md)

```sh
python -m model.assembler examples/uart_tx.asm uart_tx.bin
```

## 검증

[실행 확인] 검증용 Python ISS와 회로는 같은 명세로 독립 작성했다. cocotb는 로더로 프로그램을 적재하고 외부 핀과 클록별 실행 상태를 검사한다. 별도의 UART 수신기와 SPI 타깃이 실제 RTL 핀 전이를 해석한다. [검증 실행법](test/README.md) · [검증 계획](docs/verification_plan.md)

[추론] 클록 제약은 20 ns이며 예제 시각은 50 MHz 가정이다. 제작 공정의 속도와 14일차 면적 기준은 배선 후 결과로 판정한다. [Tiny Tapeout 공식 플로](https://www.tinytapeout.com/guides/local-hardening/)

## AI 활용

[실행 확인] Jisung IC가 설계 범위와 WAIT의 첫 상승 에지 규칙을 정하고 v0.1 설계 판단을 위임했다. Claude Code의 계획·비판 검토 서브에이전트는 일반 검증 과제를 나눴다. Codex의 분리된 역할은 명세, RTL, ISS·검증 벡터와 실행을 담당했다. IHP SRAM 시뮬레이션 모델과 전원망 보조 코드의 원출처는 아래에 있다. [IHP 모델 출처](test/sram_model/README.md) · [공식 SRAM 통합 예제](https://github.com/TinyTapeout/ttihp-cmos5l-sram-example/tree/15ac9bc8560384d1ca7b24e5df0b7b82357d98db)

## 사람이 직접 확인할 지점

- 첫 핀 예제에서 DATA와 OE를 준비할 때, COMMIT할 때, HALT할 때의 핀 방향을 비교한다.
- UART와 SPI에서 외부 검사기가 샘플하는 에지를 예제의 계산과 대조한다.
