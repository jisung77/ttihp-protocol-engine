# UART 8N1, 115200 baud at the nominal 50 MHz clock, RX = uio[1].
idle:
    WAIT HIGH 1 FOREVER
    WAIT FALL 1 FOREVER        # reference edge for this frame
    THI 0
    TLO 201
    DELAY
    TLO 0
    WAIT LOW 1 FINITE          # one observation near the middle of START
    BR TIMEOUT idle            # short false start
    THI 1
    TLO 172                    # T = 428
    LSET 8
    DELAY
data_bit:
    SHIN 1 LSB
    DELAY
    DJNZ data_bit
    TLO 0
    THI 0
    WAIT HIGH 1 FINITE         # one observation near the middle of STOP
    BR TIMEOUT idle            # framing error: wait for high before rearming
    PUSH
    BR ALWAYS idle
