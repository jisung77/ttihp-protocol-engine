`default_nettype none
`timescale 1ns / 1ps
module tb;
    reg clk, rst_n, ena;
    reg [7:0] ui_in, uio_in;
    wire [7:0] uo_out, uio_out, uio_oe;
    reg serial_inputs, uart_rx, spi_miso;
    wire uart_tx = uio_out[0];
    wire spi_sclk = uio_out[0], spi_mosi = uio_out[1], spi_cs_n = uio_out[3];
    wire [7:0] pins = serial_inputs ? {uio_in[7:3],spi_miso,uart_rx,uio_in[0]} : uio_in;

    tt_um_jisung_protocol_engine user_project (
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(pins),
        .uio_out(uio_out), .uio_oe(uio_oe), .ena(ena), .clk(clk), .rst_n(rst_n)
    );

    initial if (!$test$plusargs("NO_DUMP")) begin
        $dumpfile("tb.fst");
        $dumpvars(0, tb);
    end
endmodule
`default_nettype wire
