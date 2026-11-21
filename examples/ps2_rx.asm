# PS/2 device-to-host receive. uio[0] = CLK, uio[1] = DATA, inputs only (no DATA/OE/COMMIT).
# T = 0, so every FINITE WAIT is one look at one cycle: OK means level matched, TIMEOUT means it did not.
    THI 0
    TLO 0
idle:
    WAIT HIGH 0 FOREVER        # clock idle high
    WAIT LOW 0 FOREVER         # fall 1: START
    LSET 8
    WAIT LOW 1 FINITE          # START must be 0
    BR TIMEOUT bad_start
even:                          # data ones so far: even
    WAIT HIGH 0 FOREVER
    WAIT LOW 0 FOREVER         # falls 2..9: data bit
    SHIN 1 LSB
    WAIT LOW 1 FINITE
    BR TIMEOUT even_one        # bit = 1, so state becomes odd
    DJNZ even
    BR ALWAYS par_even
even_one:
    DJNZ odd
    BR ALWAYS par_odd
odd:                           # data ones so far: odd
    WAIT HIGH 0 FOREVER
    WAIT LOW 0 FOREVER         # falls 2..9: data bit
    SHIN 1 LSB
    WAIT LOW 1 FINITE
    BR TIMEOUT odd_one         # bit = 1, so state becomes even
    DJNZ odd
    BR ALWAYS par_odd
odd_one:
    DJNZ even
par_even:                      # even data ones: parity must be 1
    WAIT HIGH 0 FOREVER
    WAIT LOW 0 FOREVER         # fall 10: PARITY
    WAIT LOW 1 FINITE
    BR TIMEOUT stop_good
    BR ALWAYS stop_bad
par_odd:                       # odd data ones: parity must be 0
    WAIT HIGH 0 FOREVER
    WAIT LOW 0 FOREVER         # fall 10: PARITY
    WAIT LOW 1 FINITE
    BR TIMEOUT stop_bad
stop_good:
    WAIT HIGH 0 FOREVER
    WAIT LOW 0 FOREVER         # fall 11: STOP
    WAIT HIGH 1 FINITE
    BR TIMEOUT idle            # STOP = 0: reject, fall 11 already consumed
    PUSH
    BR ALWAYS idle
stop_bad:
    LSET 1                     # bad parity: still consume fall 11
    BR ALWAYS skip
bad_start:
    LSET 10                    # bad start: consume falls 2..11
skip:
    WAIT HIGH 0 FOREVER
    WAIT LOW 0 FOREVER
    DJNZ skip
    BR ALWAYS idle
