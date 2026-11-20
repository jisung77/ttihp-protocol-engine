# 프로토콜 예제와 어셈블러

[실행 확인] 저장소 루트에서 다음 명령으로 SPI 모드 0의 24워드(48바이트) 이미지를 만든다. 출력은 로더 WRITE에 차례로 전달할 워드의 하위 바이트·상위 바이트 쌍이다. BEGIN의 워드 수와 END의 16비트 워드 합은 호스트가 전송한다. [로더 규격](../spec/spec.md#s6)

```sh
python -m model.assembler examples/spi_mode0.asm spi_mode0.bin
```

[실행 확인] 모든 예제는 입력 FIFO나 외부 신호를 기다리며 반복할 수 있다. 아래 워드 수는 CLI가 생성한 바이트 수를 2로 나눈 값이다.

| 프로그램 | 워드 수 | 핀과 데이터 경로 |
|---|---:|---|
| `uart_tx.asm` | 18 | TX FIFO의 가장 오래된 바이트를 uio[0]으로 송신 |
| `uart_rx.asm` | 21 | uio[1] 수신, 정상 정지 비트를 확인한 뒤 RX FIFO에 기록 |
| `spi_mode0.asm`, `spi_mode2.asm` | 각각 24 | CPHA=0. TX FIFO 한 바이트와 MISO 한 바이트를 동시에 교환 |
| `spi_mode1.asm`, `spi_mode3.asm` | 각각 25 | CPHA=1. SPI 핀은 SCK/MOSI/MISO/CS_n = uio[0]/[1]/[2]/[3] |

### 소스 문법

[실행 확인] 한 줄에 한 명령을 쓰고 피연산자를 공백으로 구분한다. `#` 뒤는 주석이다. `name:`은 현재 워드의 절대 주소다. 레이블은 영문자 또는 밑줄로 시작하고 대소문자를 구별한다. 명령·모드 이름은 대소문자를 구별하지 않는다. 숫자는 부호 없는 10진수·`0x` 16진수·`0b` 2진수를 쓴다. `addr`은 레이블 또는 0~255의 워드 주소다. 숫자로 쓴 주소의 이미지 범위는 실행 시 명세 §3의 선택된 경로 규칙을 적용한다. 이미지 길이는 1~256워드다.

| 피연산자 형식 | 명령 |
|---|---|
| 없음 | `HALT`, `DELAY`, `PUSH`, `PULL` |
| `byte` (0~255) | `DATA`, `OE`, `COMMIT`, `TLO`, `THI`, `LSET`, `SRIMM` |
| `mode pin duration` | `WAIT`의 mode는 LOW/HIGH/RISE/FALL, pin은 0~7, duration은 FINITE/FOREVER |
| `pin order` | `SHIN`, `SHOUT`의 pin은 0~7, order는 MSB/LSB |
| `cond addr` | `BR`의 cond는 ALWAYS/OK/TIMEOUT/LC_ZERO |
| `addr` | `DJNZ` |

### 클록을 세는 방법

[추론] 다음 수치는 50 MHz와 명세 §2의 인출 시점으로 계산했다. 일반 명령의 완료에서 다음 일반 명령의 완료까지는 2클록이다. 다음 명령이 DELAY이면 그 간격이 2+T다. 보드에서 쓸 수 있는 속도는 실제 하드닝 결과로 확인한다. [타이밍 규칙](../spec/spec.md#s2) · [목표 조건](../spec/spec.md#s8)

- [추론] **UART TX**는 8N1, LSB 우선이다. 송신 FIFO에 바이트가 준비되면 시작 비트부터 다음 시작 비트까지 각 슬롯은 434클록이다. 데이터 비트 하나의 경로는 DATA, DELAY, DJNZ, SHOUT으로, 합은 2+(2+426)+2+2=434다. 계산 baud는 50,000,000/434 ≈ 115,207.4이며 목표 대비 +0.0064%다. 빈 FIFO에서 PULL이 기다리는 동안 TX는 HIGH다.
- [추론] **UART RX**는 먼저 HIGH를 본 뒤 동기화된 하강을 기다린다. 하강 관측을 0으로 잡으면 시작 비트 LOW 검사는 +211, 데이터 비트는 +651부터 434클록 간격, 정지 HIGH 검사는 +4127이다. 이 시각은 실행부의 동기화 표본 기준이다. 시작이 HIGH로 돌아오거나 정지가 LOW이면 바이트를 버리고 HIGH에서 재무장한다. RX FIFO가 가득 차면 PUSH에서 멈추므로 연속 수신에는 호스트가 FIFO를 비워야 한다.
- [추론] **SPI**는 CS_n 한 번에 8비트를 MSB 우선으로 교환한다. CPOL은 모드 0·1에서 0, 2·3에서 1이다. CPHA=0은 선행 SCK 에지, CPHA=1은 후행 SCK 에지에서 샘플한다. 각 반주기는 2×5+15=25클록이다. CS setup은 25클록이며 hold는 CPHA=0에서 25클록, CPHA=1에서 27클록이다. SHIN은 샘플 에지 2클록 뒤에 실행해 그 에지에서 1단에 샘플한 MISO를 읽는다. 예컨대 호스트가 0x96을 보내고 타깃이 0x69를 구동하면 RX FIFO의 기대값은 0x69다.

[미실행] 이 문서에서 제시한 계산만으로 실제 칩의 UART baud, SPI 속도, SRAM 타이밍을 측정할 수는 없다.

### 사람이 직접 확인할 지점

- TX 예제의 SHOUT과 다음 SHOUT 사이에 DELAY 외의 세 명령이 어떤 비용을 더하는지 센다.
- RX 예제의 정지 비트가 LOW일 때 PUSH 전에 분기하는지 확인한다.
- SPI 모드 0과 1에서 SHIN이 각각 어느 SCK 에지 다음에 오는지 비교한다.
