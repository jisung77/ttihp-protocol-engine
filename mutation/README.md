# RTL 변이 시험 재현

[M01~M15의 치환 목록](manifest.json)은 변이마다 명세 절, 대상 구문, 바꾼 구문과 동작을 구별하는 입력을 기록한다. 각 디렉터리에는 변형한 Verilog 모듈 한 개가 있다. [실행기](run.py)는 원본에서 치환 위치가 정확히 한 곳인지 확인하고 실제 사본을 대조한 뒤 그 모듈만 교체해 시뮬레이션한다.

## 두 시험 버전 복원

저장소 루트에서 Linux, Python 3.12, Icarus Verilog 12.0과 GNU Make를 사용한다. 다음 명령은 임시 디렉터리에 격리된 의존성, 소스 및 결과를 둔다.

```sh
audit=$(mktemp -d)
python3 -m venv "$audit/venv"
. "$audit/venv/bin/activate"
python -m pip install -r test/requirements.txt
frozen=ccfb4faa10f522e61cf83d4d3c5e5371e1008bf1
updated=37963ca1189e088ff444d8dcf439d33fd5b70523
for revision in "$frozen" "$updated"; do
    mkdir "$audit/$revision"
    git archive "$revision" src test model examples | tar -x -C "$audit/$revision"
    printf '%s\n' "$revision" > "$audit/$revision/source-revision.txt"
done
printf '\n' >> "$audit/$frozen/test/test.py"
```

마지막 줄은 최초 회귀에서 보관한 `test/test.py`의 끝 빈 줄까지 복원한다. 수정 전 시험 파일들과 수정 후 시험 파일들의 SHA-256은 [실행 결과](results.json)에 있다.

## 회귀

다음 명령은 두 버전 각각에서 원본과 변이 15개에 코어 회귀를 실행한다. 기존 사본을 다시 만들 때만 `--generate`를 추가한다.

```sh
PE_MUTATION_SOURCE="$audit/$frozen" python mutation/run.py --evidence "$audit/before"
PE_MUTATION_SOURCE="$audit/$updated" python mutation/run.py --evidence "$audit/after"
```

수정 전 코어 시험을 모두 통과한 M15와 원본에 확장 프로토콜 시험 8개를 적용한다. UART TX 전 바이트, UART RX 256바이트 × 3속도 × 4위상 및 SPI 4모드를 포함한다. 이어서 같은 입력 사본에서 [STOP 에지 진단](stop_witness.py)을 재생한다.

```sh
for variant in baseline M15; do
    PE_MUTATION_SOURCE="$audit/$frozen" \
        python mutation/run.py --suite protocol --evidence "$audit/before" "$variant"
done
PE_MUTATION_SOURCE="$audit/$frozen" \
    python mutation/run.py --suite witness --evidence "$audit/before" baseline M15
```

각 하위 디렉터리의 `run.json`, `run.log`, `results.xml`, `verdict.json`에 실제 명령, 소스 SHA-256, 컴파일·실행 출력과 시험별 판정이 남는다. 이미 존재하는 실행 디렉터리는 재사용하지 않는다. 원래 XML에는 실행 환경의 절대 경로와 경과 시간이 들어간다. [공개 결과](results.json)의 XML 해시는 최초 실행 원본을 식별하고, 재실행은 소스 해시와 시험별 동작으로 대조한다.

seed는 18455다. 차등 시험의 내부 프로그램 seed는 기존 0~31을 유지한다. 원본이 통과한 회귀가 변이의 기능 차이를 검출할 때 집계한다. 별도 STOP 진단은 두 점수에서 제외한다. 15개 유효 변이에서 90% 기준은 최소 14개 검출이다. [결과표](results.md) · [검증 결함 및 수정](../docs/bug_log.md)
