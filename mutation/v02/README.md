# 두 컨텍스트 RTL 변이 회귀

[실행 확인] M01–M15는 각각 한 구문만 바꾼 별도 회로다. 각 디렉터리에는 변경된 모듈 원본을 보관한다. 재생기는 기준 Git 커밋에서 나머지 모듈을 복원하고 카탈로그를 적용한 바이트열이 보관 사본과 같은지 확인한다. [기준 커밋](source-revision.txt) · [명세 절과 치환식](catalog.json) · [시험별 결과](results.md)

저장소 루트에서 Icarus Verilog, GNU Make, Git과 `test/requirements.txt`의 Python 도구를 준비한다. 아래 명령은 매번 새 디렉터리에 RTL 전체, 시뮬레이터 로그, XML 및 판정을 저장한다.

```sh
python mutation/v02/replay.py clean /tmp/dual-clean --suite directed
python mutation/v02/replay.py M01 /tmp/dual-m01 --suite directed
python mutation/v02/replay.py M05 /tmp/dual-m05 --suite faults
python mutation/v02/replay.py M12 /tmp/dual-m12 --suite faults
```

[실행 확인] 최초 회귀는 `directed`를 전체에 적용하고 생존 변이에 `random-legacy`, `protocol`을 순서대로 적용했다. `faults`는 그 뒤에 추가한 검사다. 정상 회로와 변이의 종료 코드는 각 결과의 `verdict.json`에 기록한다. 검출률 분자는 원본에서 통과한 assertion을 깨뜨린 서로 다른 변이 수다. 분모는 컴파일·실행 가능한 비등가 변이 수다.

## 사람이 직접 확인할 지점

- M05와 M12의 보강 전후 XML에서 같은 RTL 사본이 서로 다른 판정을 받는지 확인한다.
