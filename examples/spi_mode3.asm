# SPI master mode 3, CPOL=1 CPHA=1, MSB first.
# uio[0] SCK, [1] MOSI, [2] MISO, [3] CS_n.
DATA 9
OE 0x0B
COMMIT 0x0B             # idle clock, deselected target
TLO 15
next_byte:
    PULL
    DATA 1
    COMMIT 8            # assert CS_n
    LSET 8
    DATA 0
    DELAY
    COMMIT 0            # complete CS setup interval
bit:
    COMMIT 1            # leading edge: change MOSI following this edge
    SHOUT 1 MSB
    DATA 1
    COMMIT 0            # two clocks of timing padding, no pin changes
    DELAY
    COMMIT 1            # trailing edge: sample MOSI/MISO
    SHIN 2 MSB          # at +2 reads the pin sampled on the trailing edge
    DATA 0
    DELAY
    DJNZ bit
    DATA 9
    COMMIT 8            # release CS_n
    PUSH
    BR ALWAYS next_byte
