# RP2350에서 프로그램 적재

[추론] Tiny Tapeout MicroPython 펌웨어에서 해당 칩을 선택하고 프로젝트 클록을 켠 후 사용한다. `ui_in.value`, `uo_out.value`, `uio_oe_pico.value`, `reset_project`는 보드 객체의 인터페이스다. 100 kHz 이상 동작하는 프로젝트 클록을 전제로 리셋 해제 후 50 µs 기다린다. 첫 적재 확인에는 1 MHz를 사용할 수 있다. UART/SPI 예제 속도는 프로젝트 클록에 비례한다. [공식 보드 API](https://github.com/TinyTapeout/tt-micropython-firmware/blob/main/src/ttboard/demoboard.py)

PC에서 `python -m model.assembler examples/uart_tx.asm uart_tx.bin`을 실행한다. 생성한 `uart_tx.bin`과 `host/rp2350_loader.py`를 RP2350 파일시스템에 복사한다. 선택된 설계의 보드 REPL에서 다음을 실행한다.

```python
from rp2350_loader import Loader

tt.clock_project_PWM(1_000_000)
host = Loader(tt)
host.reset()
with open("uart_tx.bin", "rb") as program:
    host.load(program.read())
assert host.try_send(0x55)
host.run()
```

[추론] `try_send`는 TX FIFO가 가득 차면 False, `try_receive`는 RX FIFO가 비면 None을 반환한다. 상태 거절과 ACK 만료는 예외다. 선택 해제 후에는 `reset()`과 전체 적재를 다시 수행한다. 실제 보드 실행 증거는 아직 없다.

## 사람이 직접 확인할 지점

- 로드 직후 `host.status() & 4`가 4인지 확인한다.
- 1 MHz로 예제를 실행하면 UART 한 비트의 설계값은 434 µs다. 오실로스코프에서 핀 0의 0x55 전이 간격을 확인한다.
