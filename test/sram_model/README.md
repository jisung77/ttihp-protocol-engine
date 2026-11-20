# IHP 256×16 SRAM functional model

[실행 확인] 이 두 파일은 Tiny Tapeout CMOS5L 공식 SRAM 예제의 Git 커밋 `15ac9bc8560384d1ca7b24e5df0b7b82357d98db`에서 그대로 복사했다. 원본은 IHP Open PDK 커밋 `89d42ee6d2fd9b50b86a1aada6fee6673c3e6008`이며 저작권과 Apache-2.0 라이선스 표기는 각 파일에 있다. CMOS5L은 PDK의 공유 SRAM 뷰를 참조한다. [원본 파일](https://github.com/TinyTapeout/ttihp-cmos5l-sram-example/tree/15ac9bc8560384d1ca7b24e5df0b7b82357d98db/test/sram_model)

## 사람이 직접 확인할 지점

- RTL·GLS의 iverilog 명령에 매크로와 공통 동작 모델 두 파일이 모두 포함되는지 확인한다.
