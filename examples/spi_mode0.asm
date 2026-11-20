# SPI master mode 0, CPOL=0 CPHA=0, MSB first.
# uio[0] SCK, [1] MOSI, [2] MISO, [3] CS_n.
DATA 8
OE 0x0B
COMMIT 0x0B             # idle clock, deselected target
TLO 15
next_byte:
    PULL
    DATA 0
    COMMIT 8            # assert CS_n
    LSET 8
bit:
    SHOUT 1 MSB         # change MOSI before its leading sample edge
    DATA 1
    DELAY
    COMMIT 1            # leading edge: sample MOSI/MISO
    SHIN 2 MSB          # at +2 reads the pin sampled on the leading edge
    DATA 0
    COMMIT 0            # two clocks of timing padding, no pin changes
    DELAY
    COMMIT 1            # trailing edge
    DJNZ bit
    DATA 8
    DELAY
    COMMIT 0            # complete CS hold interval
    COMMIT 8            # release CS_n
    PUSH
    BR ALWAYS next_byte
