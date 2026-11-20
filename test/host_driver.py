"""TT nibble interface driver. Each call returns one ns after the ACK edge (§6)."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


class Host:
    def __init__(self, dut):
        self.dut = dut
        self.req = 0

    async def tick(self):
        await RisingEdge(self.dut.clk)
        await Timer(1, unit="ns")

    async def reset(self):
        d = self.dut
        d.clk.value = 0
        d.rst_n.value = 0
        d.ena.value = 1
        d.ui_in.value = 0
        d.uio_in.value = 0
        d.uart_rx.value = 1
        d.spi_miso.value = 0
        d.serial_inputs.value = 0
        self.req = 0
        cocotb.start_soon(Clock(d.clk, 20, unit="ns").start(start_high=False))
        for _ in range(3):
            await self.tick()
        assert int(d.uo_out.value) == 0 and int(d.uio_oe.value) == 0
        d.rst_n.value = 1
        for _ in range(3):
            await self.tick()

    async def nibble(self, value):
        # The data is stable before toggling REQ and throughout synchronizer latency.
        d = self.dut
        d.ui_in.value = value | self.req << 4
        await Timer(1, unit="ns")
        self.req ^= 1
        d.ui_in.value = value | self.req << 4
        for _ in range(8):
            await self.tick()
            pins = int(d.uo_out.value)
            if (pins >> 4) & 1 == self.req:
                return pins & 15
        raise AssertionError(f"REQ {self.req} nibble {value:x}: ACK timeout")

    async def command(self, opcode, payload=b""):
        nibbles = [n for b in bytes([opcode]) + payload for n in (b >> 4, b & 15)]
        for n in nibbles[:-1]:
            assert await self.nibble(n) == 0
        return await self.nibble(nibbles[-1])

    async def reply_byte(self):
        high = await self.nibble(0)
        return (high << 4) | await self.nibble(0)

    async def status(self):
        assert await self.command(0) == 0
        return await self.reply_byte()

    async def load(self, image):
        assert len(image) % 2 == 0
        words = len(image) // 2
        assert await self.command(1, words.to_bytes(2, "little")) == 0
        checksum = 0
        for offset in range(0, len(image), 2):
            word = image[offset:offset + 2]
            assert await self.command(2, word) == 0
            checksum += int.from_bytes(word, "little")
        assert await self.command(3, (checksum & 0xffff).to_bytes(2, "little")) == 0
        assert (int(self.dut.uo_out.value) >> 5) & 3 == 1

    async def run(self):
        assert await self.command(4) == 0
        assert int(self.dut.uo_out.value) & 0x40

    async def byte_tx(self, value):
        for _ in range(2048):
            code = await self.command(6, bytes([value]))
            if code == 0:
                return
            assert code == 1
        raise AssertionError("TX FIFO retry limit reached")

    async def byte_rx(self):
        for _ in range(2048):
            code = await self.command(7)
            if code == 0:
                return await self.reply_byte()
            assert code == 1
        raise AssertionError("RX FIFO retry limit reached")
