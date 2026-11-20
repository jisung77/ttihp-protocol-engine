import subprocess
import sys

import pytest

from model.assembler import AssemblyError, assemble


def test_all_instructions_by_hand():
    source = """entry: HALT
        DATA 0x01
        OE 0x80
        COMMIT 255
        TLO 0xAA
        THI 85
        WAIT FALL 7 FOREVER
        DELAY
        SHIN 2 MSB
        SHOUT 3 LSB
        BR OK final
        LSET 8
        DJNZ entry
        PUSH
        PULL
        final: SRIMM 0b10010110 # asymmetric test byte
    """
    assert assemble(source) == bytes.fromhex(
        "00 00 01 08 80 10 ff 18 aa 20 55 28 3f 30 00 38 "
        "02 40 0b 48 0f 51 08 58 00 60 00 68 00 70 96 78"
    )


def test_mode_fields_and_absolute_last_word():
    src = """wait LOW 0 finite
    WAIT HIGH 1 FINITE
    WAIT RISE 4 FOREVER
    SHIN 7 LSB
    SHOUT 0 MSB
    BR ALWAYS 0
    BR TIMEOUT last
    BR LC_ZERO last
    """ + "HALT\n" * 247 + "last: HALT"
    image = assemble(src)
    assert len(image) == 512
    assert image[:16] == bytes.fromhex("00 30 09 30 34 30 0f 40 00 48 00 50 ff 52 ff 53")


@pytest.mark.parametrize("source", [
    "", "label:", "HALT\ntrailing:", "HALT\n" * 257, "DATA 256", "OE -1", "TLO 0xGG",
    "x: HALT\nx: HALT", "9bad: HALT", "BR ALWAYS no_label", "BR SOMETIMES 0",
    "WAIT HIGH 8 FINITE", "WAIT FALL 0", "WAIT FALL 0 NEVER", "SHIN 1 SOMETIMES",
    "SHOUT 3 MSB 1", "HALT 0", "DATA", "go", "DJNZ 256",
])
def test_invalid_source(source):
    with pytest.raises(AssemblyError):
        assemble(source)


def test_cli_does_not_replace_output_on_parse_error(tmp_path):
    src = tmp_path / "example.asm"
    out = tmp_path / "image.bin"
    cmd = [sys.executable, "-m", "model.assembler", str(src), str(out)]
    src.write_text("DATA 1\nOE 1\nCOMMIT 1\nHALT")
    subprocess.run(cmd, check=True)
    assert out.read_bytes() == bytes.fromhex("01 08 01 10 01 18 00 00")
    src.write_text("HALT\nDATA 500")
    failure = subprocess.run(cmd, capture_output=True, text=True)
    assert failure.returncode != 0 and "line 2" in failure.stderr
    assert out.read_bytes() == bytes.fromhex("01 08 01 10 01 18 00 00")
