```python
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

import os
import itertools
from PIL import Image


@cocotb.test()
async def test_project(dut):

    # -------------------------------------------------------------------------
    # Clock
    # -------------------------------------------------------------------------
    # VGA timing is based on a 25 MHz simulation clock in the original test.
    CLOCK_PERIOD = 40  # ns = 25 MHz

    # -------------------------------------------------------------------------
    # VGA 640x480 timing
    # -------------------------------------------------------------------------
    H_DISPLAY = 640
    H_FRONT = 16
    H_SYNC = 96
    H_BACK = 48

    V_DISPLAY = 480
    V_FRONT = 10
    V_SYNC = 2
    V_BACK = 33

    CAPTURE_FRAMES = 3

    # -------------------------------------------------------------------------
    # Derived timing constants
    # -------------------------------------------------------------------------
    H_SYNC_START = H_DISPLAY + H_FRONT
    H_SYNC_END = H_SYNC_START + H_SYNC
    H_TOTAL = H_SYNC_END + H_BACK

    V_SYNC_START = V_DISPLAY + V_FRONT
    V_SYNC_END = V_SYNC_START + V_SYNC
    V_TOTAL = V_SYNC_END + V_BACK

    # -------------------------------------------------------------------------
    # Palette
    #
    # uo_out:
    #
    #   bit 7 = hsync
    #   bit 6 = B0
    #   bit 5 = G0
    #   bit 4 = R0
    #   bit 3 = vsync
    #   bit 2 = B1
    #   bit 1 = G1
    #   bit 0 = R1
    #
    # This matches the original testbench.
    # -------------------------------------------------------------------------
    palette = [bytes(3)] * 256

    for r1, r0, g1, g0, b1, b0 in itertools.product(range(2), repeat=6):

        red = 170 * r1 + 85 * r0
        green = 170 * g1 + 85 * g0
        blue = 170 * b1 + 85 * b0

        color_index = (
            (b0 << 6)
            | (g0 << 5)
            | (r0 << 4)
            | (b1 << 2)
            | (g1 << 1)
            | r1
        )

        for sync_bits in (0x00, 0x08, 0x80, 0x88):
            palette[color_index | sync_bits] = bytes(
                (red, green, blue)
            )

    # -------------------------------------------------------------------------
    # Clock
    # -------------------------------------------------------------------------
    clock = Clock(dut.clk, CLOCK_PERIOD, unit="ns")
    cocotb.start_soon(clock.start())

    # -------------------------------------------------------------------------
    # Reset
    # -------------------------------------------------------------------------
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)

    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 2)

    # -------------------------------------------------------------------------
    # Check one complete VGA line
    # -------------------------------------------------------------------------
    async def check_line(expected_vsync):

        for pixel in range(H_TOTAL):

            hsync = int(dut.uo_out.value[7])
            vsync = int(dut.uo_out.value[3])

            expected_hsync = (
                0
                if H_SYNC_START <= pixel < H_SYNC_END
                else 1
            )

            assert hsync == expected_hsync, (
                f"Unexpected hsync pattern at pixel {pixel}: "
                f"expected {expected_hsync}, got {hsync}"
            )

            assert vsync == expected_vsync, (
                f"Unexpected vsync pattern at pixel {pixel}: "
                f"expected {expected_vsync}, got {vsync}"
            )

            await ClockCycles(dut.clk, 1)

    # -------------------------------------------------------------------------
    # Capture one visible VGA line
    # -------------------------------------------------------------------------
    async def capture_line(framebuffer, offset):

        for pixel in range(H_TOTAL):

            hsync = int(dut.uo_out.value[7])
            vsync = int(dut.uo_out.value[3])

            expected_hsync = (
                0
                if H_SYNC_START <= pixel < H_SYNC_END
                else 1
            )

            assert hsync == expected_hsync, (
                f"Unexpected hsync pattern at pixel {pixel}: "
                f"expected {expected_hsync}, got {hsync}"
            )

            # During the visible 480 lines, vsync must remain high.
            assert vsync == 1, (
                f"Unexpected vsync during visible line "
                f"at pixel {pixel}"
            )

            # Only capture the 640 visible pixels.
            if pixel < H_DISPLAY:

                color = palette[int(dut.uo_out.value)]

                framebuffer[
                    offset + 3 * pixel:
                    offset + 3 * pixel + 3
                ] = color

            await ClockCycles(dut.clk, 1)

    # -------------------------------------------------------------------------
    # Skip an entire frame
    # -------------------------------------------------------------------------
    async def skip_frame(frame_num):

        dut._log.info(
            f"Skipping frame {frame_num}"
        )

        await ClockCycles(
            dut.clk,
            H_TOTAL * V_TOTAL
        )

    # -------------------------------------------------------------------------
    # Capture a complete VGA frame
    # -------------------------------------------------------------------------
    async def capture_frame(frame_num, check_sync=True):

        framebuffer = bytearray(
            V_DISPLAY * H_DISPLAY * 3
        )

        # -------------------------------------------------------------
        # Visible display area
        # -------------------------------------------------------------
        for line in range(V_DISPLAY):

            dut._log.info(
                f"Frame {frame_num}, line {line} (display)"
            )

            await capture_line(
                framebuffer,
                3 * line * H_DISPLAY
            )

        # -------------------------------------------------------------
        # Vertical front porch
        # -------------------------------------------------------------
        if check_sync:

            for line in range(V_FRONT):

                dut._log.info(
                    f"Frame {frame_num}, "
                    f"line {V_DISPLAY + line} (front porch)"
                )

                await check_line(1)

            # ---------------------------------------------------------
            # Vertical sync pulse
            # ---------------------------------------------------------
            for line in range(V_SYNC):

                dut._log.info(
                    f"Frame {frame_num}, "
                    f"line {V_SYNC_START + line} (sync pulse)"
                )

                await check_line(0)

            # ---------------------------------------------------------
            # Vertical back porch
            # ---------------------------------------------------------
            for line in range(V_BACK):

                dut._log.info(
                    f"Frame {frame_num}, "
                    f"line {V_SYNC_END + line} (back porch)"
                )

                await check_line(1)

        else:

            dut._log.info(
                f"Frame {frame_num}, "
                f"skipping non-display lines"
            )

            await ClockCycles(
                dut.clk,
                H_TOTAL * (V_TOTAL - V_DISPLAY)
            )

        # -------------------------------------------------------------
        # Convert framebuffer to PIL image
        # -------------------------------------------------------------
        frame = Image.frombytes(
            "RGB",
            (H_DISPLAY, V_DISPLAY),
            bytes(framebuffer)
        )

        return frame

    # -------------------------------------------------------------------------
    # Output directory
    # -------------------------------------------------------------------------
    os.makedirs("output", exist_ok=True)

    # -------------------------------------------------------------------------
    # Capture frames
    # -------------------------------------------------------------------------
    for frame_number in range(CAPTURE_FRAMES):

        frame = await capture_frame(
            frame_number,
            check_sync=True
        )

        filename = (
            f"output/frame{frame_number}.png"
        )

        frame.save(filename)

        dut._log.info(
            f"Saved {filename}"
        )
```
