# 단일 컨텍스트 I²C 쓰기

[실행 확인] v0.2 엔진의 기존 명령으로 7비트 주소 하나에 1바이트를 쓴다. 주소와 데이터는 이미지를 만들 때 정한다. 일반 주소 0x08–0x77과 데이터 0x00–0xff 조합 28,672개를 프로젝트 정본 어셈블러로 조립했으며, 각 이미지는 189워드(378바이트)다. 단독 모드의 프로그램 공간은 256워드다. [명령어와 로더](../../spec/spec.md)

## 이미지 만들기

저장소 루트의 Linux 또는 WSL 셸에서 실행한다.

```sh
python -m experiments.i2c_write.write_image 0x52 0xa5 > i2c.asm
python -m model.assembler i2c.asm i2c.bin
```

[실행 확인] RUN을 내릴 때 두 선은 이미 HIGH이며 이 컨트롤러만 버스를 사용한다고 가정한다. 단독 모드에서 표준 BEGIN/WRITE/END로 이미지를 적재한 다음 RUN한다. SDA는 uio[0], SCL은 uio[1]이다. 두 핀의 출력값은 0이며 OE를 켜면 LOW, 끄면 외부 풀업이 선을 올린다. 전송 순서는 START → 주소 바이트 0xA4 → 응답 → 데이터 0xA5 → 응답 → STOP이다. 주소 NACK이면 데이터를 보내지 않고 STOP으로 간다. 프로그램은 응답 한 번마다 RX FIFO에 ACK=0 또는 NACK=1을 넣고 HALT한다. [프로그램 생성기](write_image.py) · [호스트 적재](../../host/README.md)

## 핀에서 검증하기

```sh
python -m pytest -q experiments/i2c_write
python -m experiments.i2c_write.run i2c-results.json --traces i2c-pins
(cd test && make COCOTB_TEST_MODULES=test_i2c_write)
```

[실행 확인] 테스트 타깃은 마스터의 실제 핀을 보고 주소·쓰기 비트를 해석해 응답한다. 수동 검사기는 양쪽의 LOW 구동을 합쳐 전송 바이트, 응답 소유권과 타이밍을 판정한다. 각 캡처는 클록마다 컨트롤러의 OUT, OE, 타깃 LOW 마스크를 1바이트씩 차례로 기록한다. RTL과 GLS에서 같은 입력으로 얻은 ISS 캡처와 직접 비교할 수 있다. [타깃과 로더](exercise.py) · [수동 검사기](check_bus.py) · [sigrok 원본 디코더로 재생](sigrok.md) · [cocotb 대조](../../test/test_i2c_write.py)

[실행 확인] 20 ns 클록의 ISS에서 0x52/0xA5에 두 번 ACK한 경우 RUN 수락에서 START까지는 267클록이었다. 평상시와 유한 스트레칭을 포함한 144개 응답 조합에서 가장 짧은 관측 SCL 주기는 528클록이었다. 528 × 20 ns = 10.56 µs이며, 그 주기의 역수는 약 94.7 kHz다. RUN 전후 최초 유휴 시간은 별도 STOP과 다음 START 사이의 버스 유휴 시간 측정값으로 쓸 수 없다. [NXP UM10204, Standard-mode 타이밍 표](https://www.nxp.com/docs/en/user-guide/UM10204.pdf)

[실행 확인] 모든 SCL 해제 뒤 실제 입력이 HIGH가 될 때까지 기다린다. 타깃이 SCL을 계속 LOW로 잡으면 호스트 STOP으로 엔진을 정지하고 OE를 해제할 수 있다. 이때 선로 SCL은 여전히 LOW이므로 버스에 STOP은 생기지 않는다.

[미실행] 실물 풀업·배선에 따른 상승 시간과 상대 기기 호환성은 측정 전이다. 이 예제가 다루는 전송은 단일 마스터의 1바이트 쓰기다.

## 사람이 직접 확인할 지점

- 주소 0x52에 NACK를 돌려주면 주소 바이트 0xA4 뒤에 STOP이 오고 RX에 1 하나가 남는지 확인한다.
- ACK의 아홉 번째 클록에는 마스터의 SDA OE가 0이며 타깃이 SDA를 당기는지 확인한다.
- 영구 스트레칭 사례에서 호스트 STOP 뒤의 OE와 실제 SCL 레벨을 각각 확인한다.
