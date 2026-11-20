# 검증 실행

프로젝트 루트에서 Python 3.11 이상, Icarus Verilog와 가상환경을 사용한다. GTKWave에서는 `tb.fst`를 열고 외부 출력과 `uio_oe`를 함께 본다.

```sh
python -m pip install -r test/requirements.txt
python -m pytest -q model
TT_FULL_WIRE_SWEEP=1 python -m pytest -q model/test_program_pins.py
cd test
COCOTB_RANDOM_SEED=18455 make -B
python -m cocotb_tools.check_results results.xml
```

[실행 확인] RTL 시뮬레이션은 IHP 256×16 SRAM의 공식 기능 모델을 사용한다. 실행 중인 프로그램은 REQ/ACK 핀을 통해 적재한다. 무작위 차등 회귀는 32개 고정 seed에서 각각 100개 명령의 완료까지 ISS와 매 클록을 비교한다. 첫 차이가 나면 바이너리, 입력, 직전 최대 20클록의 기대값·관측값을 출력한다. [로더](host_driver.py) · [RTL 차등 시험](test.py)

[실행 확인] UART 수신 배속·시작 위상 전체 조합은 아래 명령으로 실행한다. 큰 파형이 필요할 때 `PLUSARGS=+NO_DUMP`를 생략한다. `results.xml`과 파형은 다음 실행 전에 별도 디렉터리로 복사한다. [핀 수준 BFM](test_protocols.py)

```sh
TT_EXTENDED=1 make -B COCOTB_TEST_MODULES=test_protocols COCOTB_RESULTS_FILE=protocols-full.xml PLUSARGS=+NO_DUMP
python -m cocotb_tools.check_results protocols-full.xml
```

[추론] 공식 GLS에는 같은 커밋에서 하드닝한 IHP `final/nl/*.nl.v`의 실제 프로젝트 넷리스트와 해당 PDK 셀 모델이 필요하다. 로컬 실행 시 넷리스트를 `test/gate_level_netlist.v`에 복사하고 올바른 `PDK_ROOT`를 설정한 뒤 `GATES=yes make -B COCOTB_RESULTS_FILE=gls.xml`로 실행한다. 공식 Actions의 GDS, precheck, gl_test는 개별 상태로 확인한다. [공식 GLS 액션](https://github.com/TinyTapeout/tt-gds-action/blob/ttihp27a/gl_test/action.yml)

## 사람이 직접 확인할 지점

- XML에 기대한 테스트가 실제로 실행됐고 실패·오류가 없는지 확인한다. 외부 핀만 보존되는 GLS에는 RTL 내부 신호 차등 시험을 적용하지 않는다.
- 20 ns 설정은 시뮬레이션 클록 주기다. 배선 후 타이밍은 같은 커밋의 GDS 산출물로 확인한다.
