# 두 컨텍스트 실행과 검증

[실행 확인] 코어 A와 B의 프로그램은 같은 256×16 단일 포트 SRAM과 하나의 명령 해독·실행부를 사용한다. A는 상위 네 핀, B는 하위 네 핀을 구동한다. 각 컨텍스트에 TX·RX FIFO가 하나씩 있다. 단독 모드는 기존 256워드 이미지와 여덟 핀을 사용한다. [명세 §9](../spec/spec.md#s9) · [RTL 기준 커밋](https://github.com/jisung77/ttihp-protocol-engine/commit/0b4a2f8)

## 동시에 실행한 프로그램

[실행 확인] A의 UART TX가 `55 A6`를 송신하는 동안 B의 SPI 마스터는 `96 53`을 송신하고 `3D C6`을 수신했다. 외부 핀 검사기는 독립적으로 프레임을 복호화하고 각 SPI CS 구간 안에 실제 UART 전이가 있는지 확인했다. 수신한 바이트는 로더 명령으로 B의 RX FIFO에서 꺼내 확인했다. SPI 모드 네 가지와 RUN 시작 홀짝 두 가지를 조합한 여덟 시험 모두 통과했다. [핀 검사기](../experiments/two_context/check_pins.py) · [호스트 시나리오](../experiments/two_context/verify_v02.py)

| 핀에서 확인한 값 | 클록 단위 결과 | 50 MHz 가정에서의 환산 |
|---|---|---|
| UART 1비트 | 434클록 | 50,000,000 ÷ 434 ≈ 115,207 baud |
| SPI SCK 한 주기 | 반주기 24 + 26클록 | 50,000,000 ÷ 50 = 1 MHz |
| SPI CS setup / hold | 26 / 26 또는 28클록 | 520 ns / 520 또는 560 ns |

[실행 확인] 기존 단독용 SPI 지연 상수를 쓴 네 이미지는 모두 50클록 주기 검사에서 거절됐다. PC에서 다음 명령으로 실행 이미지, 어셈블리 소스, 프레임별 핀 측정값을 재생성할 수 있다.

```sh
python experiments/two_context/verify_v02.py dual-demo --negative
```

[실행 확인] 별도 cocotb 시험은 ISS 없이 동작한다. SPI 타깃이 매 클록에 DUT가 직전에 내보낸 CS·SCK를 보고 다음 MISO를 정한다. 두 RUN 시작 위상과 네 모드를 합쳐 82,844개 클록에서 외부 바이트와 핀 타이밍을 확인했다. [실시간 핀 검사](../test/test_dual_live.py)

## 검증 범위

- [실행 확인] 원래 29개 지향 시나리오와 고정 시드 네 쌍을 이용해 모드 선택, 호스트 잠금, WAIT의 시작·마감 에지, FIFO, STOP과 상대 명령의 동시 진행을 검사했다. RTL은 클록별 핀·내부 상태를 ISS와 대조하고 GLS는 최상위 핀만 관찰한다. [클록별 검사](../test/test_dual.py) · [시드 검사](../test/test_dual_random.py)
- [실행 확인] 깊이 제한 없는 두 PDR 검사가 듀얼 슬롯·응답 태그와 핀 소유권을 확인했다. FIFO의 여덟 비트 증명 및 도달 검사도 통과했다. 기존 단독 검증은 모드를 선택하는 처음 세 에지에 일곱 유효 단독 strap 중 하나를 고정한다. 임의 재적재 이력 전체에 대한 단독 PC 범위 증명은 240초에 TIMEOUT이었다. 실제 핀으로 적재한 511개 경계 이미지는 모두 통과했다. [formal 범위와 재현](../formal/README.md)
- [실행 확인] 실제로 컴파일한 변이 15개에서 초기 검출 수는 13개였다. 비소유 핀 SHOUT와 잘못된 END 체크섬 검사를 추가해 15개를 모두 검출했다. 원본 RTL은 두 경우 모두 통과했다. [변이별 결과](../mutation/v02/results.md)
- [실행 확인] 배포용 RP2350 드라이버로 RTL에 A·B 이미지를 적재하고 둘을 실행했다. B만 정지시키자 출력과 OE가 `0x11`에서 `0x10`이 됐다. 두 컨텍스트를 다시 리셋하기까지의 전체 절차에서 110회 요청·응답이 모두 완료됐다. [보드 인터페이스 회귀](../test/test_board_host.py) · [보드 사용법](../host/README.md)
- [미실행] 실물 RP2350에서의 적재와 오실로스코프 측정, 이 PC에서의 Docker 하드닝은 실행하지 않았다.

## 8×4 배선 기준

[실행 확인] 공식 개발 커밋 `0b4a2f8`의 TT IHP 빌드에서 SRAM 매크로 1개를 포함한 GDS를 생성했다. precheck 10개 항목이 통과했다. GLS는 22개 시험을 통과했고 RTL 내부 상태를 요구하는 기존 시험 3개는 계획대로 건너뛰었다. IHP CMOS5L PDK 커밋은 `89d42ee`, LibreLane은 `3.1.0.dev4`였다. [공식 GDS·precheck·GLS](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44885024444) · [기준 커밋 RTL·Python 회귀](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44885024541)

[실행 확인] 배선된 표준셀·클록 트리 76,257.4176 µm²에 SRAM 28,127.1040 µm²를 더한 후 코어 사각형 1,208,172.6720 µm²로 나누면 **8.6399%**다. DEF 및 GDS 인스턴스 집계가 일치했다. 20 ns 제약과 세 코너에서 최저 setup slack은 +6.5605 ns, 최저 hold slack은 +0.1014 ns였고 TNS는 0이었다. 기준 GDS의 SHA-256은 `ea319e356bdfc402dc7017fb4f4faa76d810d124c08c0c13a8899b9246b840a0`이다.

[실행 확인] 기준 배선에서 클록 트리의 max-fanout 위반 39건과 SRAM 출력 A_DOUT[8,5,4]의 max-capacitance 위반 3건이 남았다. 전원망은 초기 검사 직후 SRAM용 스트라이프가 확장되며 후배선 검사에서 VPWR·VGND 연결을 확인했다. Netgen의 최상위 LEF/DEF 추출 LVS는 SRAM을 블랙박스로 취급했고 불일치가 0이었다. Magic의 SRAM 금속 장애물 관련 표시 28건은 최종 GDS의 공식 KLayout DRC와 별도 기록한다.

## 채택한 배선 설정

[실행 확인] 클록 말단 클러스터 크기를 8로 설정하고 global routing 후 설계 보정을 켰다. RTL, 20 ns 제약 및 셀의 허용 부하는 기준 배선과 같다. 클록 팬아웃 위반은 39건에서 1건으로, SRAM 출력 부하 위반은 각 코너에서 3건에서 2건으로 줄었다. CTS 루트 `clkbuf_0_clk_regs/X`의 팬아웃은 16으로 허용값 8을 넘는다. 남은 SRAM 출력은 A_DOUT[7]과 [10]이며 최악 부하는 0.071720 pF, 한계는 0.064000 pF다. 최대 부하 초과량은 0.019226 pF에서 0.007720 pF로 줄었다. [공식 비교 빌드](https://github.com/jisung77/ttihp-protocol-engine/actions/runs/44886598957)

| 배선 후 측정값 | 기준 | 채택한 설정 |
|---|---:|---:|
| 코어 점유율 | 8.6399% | 8.7759% |
| 최저 setup slack | +6.5605 ns | +6.6262 ns |
| 최저 hold slack | +0.1014 ns | +0.0923 ns |
| 최대 전이 시간 위반 | 0 | 0 |

[실행 확인] 표준셀·클록 트리 77,901.2640 µm²와 SRAM 28,127.1040 µm²를 합한 면적은 106,028.3680 µm²다. 세 코너 모두 setup·hold TNS는 0이다. 보드에서 입출력 시간을 측정하는 작업은 남아 있다. 기능 GLS와 타이밍 분석은 각각 공식 TT의 게이트 모델과 배선 후 STA로 수행한다.

## 사람이 직접 확인할 지점

1. `dual-demo/concurrent-pins.json`에서 SPI 모드마다 UART 전이와 CS 활성 구간을 겹쳐 본다.
2. 변이 M05가 상대 핀을 쓰려 한 에지에서 FAULT와 두 출력 방향을 확인한다.
3. 공식 GDS 산출물의 해시를 precheck가 검사한 파일과 대조한다.
