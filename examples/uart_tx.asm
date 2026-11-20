# UART 8N1, 115200 baud at the nominal 50 MHz clock, TX = uio[0].
DATA 1
OE 1
COMMIT 1               # drive idle high
THI 1
TLO 170                # T = 426
BR ALWAYS get_byte

start_bit:
    LSET 8
    DELAY
data_bit:
    SHOUT 0 LSB
    DATA 1             # stage stop high without changing the active TX pin
    DELAY
    DJNZ data_bit
    COMMIT 1           # stop
    DELAY
get_byte:
    PULL               # empty FIFO extends idle
    DATA 0
    COMMIT 1           # start
    BR ALWAYS start_bit
