# v0.2 RTL 변이 검출 결과

[실행 확인] 동결한 시뮬레이션은 컴파일에 성공한 변이 15개 중 13개(86.67%)를 검출했다. 90% 기준에 미달했다. 생존한 M05와 M12에 대응하는 독립 핀 검사 두 개를 추가한 후 15/15(100%)를 확인했다. 컴파일 불가, 시간 초과, 알려진 등가 변이는 각각 0개다. [변이 정의](catalog.json) · [입력·실행 결과 해시](results.json)

[실행 확인] 각 사본은 원본과 RTL 파일 하나에서만 달랐다. 첫 단계는 29개 핀 시나리오를 실제 Icarus RTL과 ISS에서 매 에지 비교했다. 생존 사본에는 기존 랜덤 프로그램 네 쌍, 레거시 코어 검사 여섯 개, 듀얼 UART·SPI의 네 모드와 두 RUN 위상 조합을 실행했다. 원본은 네 회귀 계층을 모두 통과했다. 첫 실제 불일치를 검출하면 해당 사본의 후속 검사는 생략했다.

| ID | 조항 | 동결 시뮬레이션 | 추가 검사 포함 | 첫 검출 벡터 | 첫 불일치 |
|---|---|---|---|---|---|
| M01 | §9.1/§9.5 | 검출 | 검출 | `test_aligned_runs_and_tagged_words` | `E272 instr_ctx: expected 1, got 0` |
| M02 | §9.3 | 검출 | 검출 | `test_aligned_runs_and_tagged_words` | `E271 pc_a: expected 2, got 00000001` |
| M03 | §9.2 | 검출 | 검출 | `test_stop_cancels_pending_wait_only_for_target(17,)` | `E333 uio_out: expected 16, got 00000000` |
| M04 | §9.2 | 검출 | 검출 | `test_aligned_runs_and_tagged_words` | `E271 sram_addr: expected 128, got 00000000` |
| M05 | §9.2 | 생존 | 검출 | `forbidden_shout` | `E358 uo_out: expected 160, got 01100000` |
| M06 | §3/§9.2 | 검출 | 검출 | `test_bank_b_last_word_checks_full_width_address(2049, 21)` | `E2354 uo_out: expected 160, got 01100000` |
| M07 | §9.3 | 검출 | 검출 | `test_wait_onset_deadline_and_late_edge(17, 22)` | `E336 pc_b: expected 5, got 00000110` |
| M08 | §9.4 | 검출 | 검출 | `test_loader_lock_mismatch_discards_frame_and_keeps_write_index` | `E111 uo_out: expected 35, got 00100010` |
| M09 | §9.4 | 검출 | 검출 | `test_loader_lock_mismatch_discards_frame_and_keeps_write_index` | `E111 uo_out: expected 35, got 00000011` |
| M10 | §9.4 | 검출 | 검출 | `test_loader_lock_mismatch_discards_frame_and_keeps_write_index` | `E117 uo_out: expected 34, got 01100000` |
| M11 | §9.2/§9.4 | 검출 | 검출 | `test_aligned_runs_and_tagged_words` | `E183 sram_addr: expected 128, got 00000000` |
| M12 | §6/§9.4 | 생존 | 검출 | `wrong_checksum` | `E430 uo_out: expected 3, got 00100000` |
| M13 | §9.1 | 검출 | 검출 | `test_mode_latches_through_reset_release(3,)` | `E3 dual_mode: expected 0, got 1` |
| M14 | §9.4 | 검출 | 검출 | `test_aligned_runs_and_tagged_words` | `E150 uo_out: expected 16, got 00110000` |
| M15 | §9.2 | 검출 | 검출 | `test_aligned_runs_and_tagged_words` | `E276 uio_out: expected 1, got 00000000` |

[실행 확인] M05는 B가 소유한 핀을 먼저 구동하고 A의 핀에 SHOUT을 실행했을 때 검출했다. B가 fault로 멈춘 뒤에도 A는 WAIT를 빠져나와 다음 COMMIT을 실행했다. M12는 워드 수를 맞추고 END 체크섬의 한 비트만 바꿨을 때 검출했다. A의 적재 세션이 폐기되는 동안 B의 FAULT와 채워 둔 TX·RX FIFO를 보존하는지 검사했다. 유효 BEGIN이 대상의 FAULT·FIFO를 초기화하므로 자기 LOADING 중 기존 FAULT=1·비어 있지 않은 FIFO를 만드는 상태는 이 시험에 포함되지 않는다. [추가 핀 검사](../../test/test_dual_faults.py)

[실행 확인] 소유권 formal의 선행 통과 기록은 원본 회로에서 얻었다. 같은 속성을 M05에 다시 적용해 ABC PDR가 39번 프레임(0부터 계산)에서 소유권 miter 출력 0의 반례를 찾았다. SBY 결과는 FAIL(종료 코드 2)이다. ABC로 같은 AIG를 다시 풀어 검산한 결과가 SBY 원본 반례와 바이트 단위로 일치했다. 상위 VCD를 생성한 Yosys에는 신호 이름 매핑 경고 129건이 있어 파형에서 개별 SVA 줄을 식별할 수 없었다. 이 formal 결과는 시뮬레이션 검출률에서 제외했다.

## 사람이 직접 확인할 지점

- M05·M12의 동결 결과와 추가 검사 열을 나란히 확인한다.
- results.json의 각 원본 JUnit 해시와 첫 실제 불일치 에지를 대조한다.
- M12의 END 실패 후 다른 컨텍스트에 남은 RX 바이트와 상태를 확인한다.
