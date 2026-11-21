# 프로그래머블 프로토콜 엔진

[실행 확인] 16비트 명령을 적재해 여덟 양방향 핀의 데이터·방향·입력 샘플 시점을 제어하는 엔진이다. 단독 모드는 핀 8개와 256워드를 쓰고, 듀얼 모드는 두 프로그램이 각각 128워드와 출력 핀 4개를 맡아 실행부를 공유한다. IHP CMOS5L용 Tiny Tapeout 인터페이스와 256×16 단일 포트 SRAM을 사용한다. UART TX/RX 및 네 가지 SPI 마스터 모드는 같은 RTL에서 서로 다른 프로그램으로 실행된다. [핀맵과 첫 적재 예제](docs/info.md) · [명령어와 클록 규칙](spec/spec.md)

## 첫 프로그램

호스트에서 다음 명령을 실행하면 UART 송신 예제가 로더에 넣을 수 있는 리틀엔디언 바이너리가 된다. 준비된 프로그램은 FIFO에서 바이트를 받아 핀 0에 8N1 프레임을 출력한다. [어셈블리와 예제 설명](examples/README.md) · [RP2350 적재 방법](host/README.md)

```sh
python -m model.assembler examples/uart_tx.asm uart_tx.bin
```

## 검증

[실행 확인] 검증용 Python ISS와 회로는 같은 명세로 독립 작성했다. cocotb는 로더로 프로그램을 적재하고 외부 핀과 클록별 실행 상태를 검사한다. 별도의 UART 수신기와 SPI 타깃이 실제 RTL 핀 전이를 해석한다. [검증 보고서](docs/verification_report.md) · [검증 실행법](test/README.md) · [검증 계획](docs/verification_plan.md)

[실행 확인] 두 컨텍스트의 공식 배선에서 SRAM을 포함한 점유율은 8.7759%였다. 20 ns 제약과 세 코너에서 최저 setup/hold 여유는 +6.6262/+0.0923 ns였다. 잔여 전기적 경고는 클록 팬아웃 1건과 SRAM 부하 2건이다. [배선 수치와 범위](docs/two_context_results.md) [Tiny Tapeout 공식 플로](https://www.tinytapeout.com/guides/local-hardening/)

[실행 확인] 듀얼 모드에서 A의 UART TX와 B의 SPI 마스터가 동시에 동작한다. 네 가지 SPI 모드를 양쪽 시작 홀짝에서 시험하고, 매 전송의 MISO 바이트를 실제 호스트 RX 명령으로 읽어 검사했다. [듀얼 검증 결과](docs/two_context_results.md) · [동시 실행 검사](experiments/two_context/verify_v02.py) · [클록별 RTL 대조](test/test_dual.py) · [슬롯과 핀 소유권 formal](formal/pe_dual.sby)

## AI 활용

[실행 확인] Jisung IC가 설계 범위와 WAIT의 첫 상승 에지 규칙을 정하고 v0.1 설계 판단을 위임했다. Claude Code의 계획·비판 검토 서브에이전트는 일반 검증 과제를 나눴다. Codex의 분리된 역할은 명세, RTL, ISS·검증 벡터와 실행을 담당했다. v0.2에서는 Jisung IC의 정본 편집 위임에 따라 Claude Code가 RTL 및 ISS 업무를 각각 분리된 서브에이전트에 배정했다. Codex는 독립 핀 검사와 모델·RTL·게이트 대조를 수행했다. IHP SRAM 시뮬레이션 모델과 전원망 보조 코드의 원출처는 아래에 있다. [IHP 모델 출처](test/sram_model/README.md) · [공식 SRAM 통합 예제](https://github.com/TinyTapeout/ttihp-cmos5l-sram-example/tree/15ac9bc8560384d1ca7b24e5df0b7b82357d98db)

## 사람이 직접 확인할 지점

- 첫 핀 예제에서 DATA와 OE를 준비할 때, COMMIT할 때, HALT할 때의 핀 방향을 비교한다.
- UART와 SPI에서 외부 검사기가 샘플하는 에지를 예제의 계산과 대조한다.
