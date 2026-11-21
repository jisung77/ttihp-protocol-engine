`timescale 1ns/1ps
module pin_bounds_tb;
    reg clk=0, rst_n=0, ena=1;
    reg [7:0] ui=0;
    wire [7:0] response, pc;
    wire [8:0] observed;
    pe_engine_formal monitor(.clk(clk),.rst_n(rst_n),.ena(ena),.ui_in(ui),.uio_in(8'b0),
                            .uo_out(response),.pc(pc),.declared_length(observed));
    always #10 clk = ~clk;
    integer ticks=0, accepted_nibbles=0, checks=0, changed_data=0, running_cycles=0;
    reg [3:0] last_ack_data;
    bit saw_ack=0;
    always @(posedge clk) ticks=ticks+1;
    always @(negedge clk) begin
      if (rst_n && ena && response[6]) begin
        running_cycles=running_cycles+1;
        if (observed===0 || pc>=observed) $fatal(1,"independent bounds violated pc=%d length=%d",pc,observed);
      end
    end
    initial begin repeat(100000) @(posedge clk); $fatal(1,"test timed out"); end

    // The next nibble is placed immediately after observing the previous ACK.
    // REQ toggles one clock later. In general the new nibble and acknowledged nibble differ.
    task automatic nibble(input [3:0] value, input integer code);
        integer n;
        begin
          if (saw_ack && last_ack_data!=value) changed_data=changed_data+1;
          ui[3:0]=value;
          @(negedge clk);
          ui[4]=~ui[4];
          n=0;
          do begin @(negedge clk); n=n+1; if(n>8)$fatal(1,"no ACK at %d",ticks); end
          while(response[4]!==ui[4]);
          if(code>=0 && response[3:0]!==code[3:0])
            $fatal(1,"unexpected reply at %d, expected %d got %x",ticks,code,response);
          accepted_nibbles=accepted_nibbles+1;
          last_ack_data=value;
          saw_ack=1;
        end
    endtask
    task automatic byte_out(input [7:0] value, input integer code);
      begin nibble(value[7:4],0); nibble(value[3:0],code); end
    endtask
    task automatic command_word(input [7:0] op, input [15:0] arg, input integer code);
      begin byte_out(op,0); byte_out(arg[7:0],0); byte_out(arg[15:8],code); end
    endtask
    task automatic check_length(input integer expected, input [320:0] label);
      begin
        // Deliberately change bit 0 before the observer samples the ACK transition.
        ui[3:0]=~ui[3:0];
        @(negedge clk); #1;
        if(observed!==expected[8:0])
          $fatal(1,"%0s: observer got %0d, expected %0d at t=%0d",label,observed,expected,ticks);
        checks=checks+1;
        $display("WITNESS %0s tick=%0d length=%0d pc=%0d pins=%02h",label,ticks,observed,pc,response);
      end
    endtask
    task automatic restart(input integer by_deselect, input [2:0] mode);
      begin
        if(by_deselect) ena=0; else rst_n=0;
        ui={mode,5'b0};saw_ack=0;
        repeat(3) @(negedge clk);
        if(observed!==0 || response!==0 || pc!==0)$fatal(1,"uncleared observer or pins");
        ena=1;rst_n=1;
        repeat(4) @(negedge clk);#1;
      end
    endtask

    integer i;
    reg [15:0] word_value,checksum;
    initial begin
      restart(0,0);
      command_word(1,0,3); check_length(0,"reject zero");
      command_word(1,257,3); check_length(0,"reject 257");
      command_word(1,17,0); check_length(17,"valid begin 17");
      command_word(1,16'h100,0); check_length(256,"replace partial by 256");
      command_word(2,16'h1234,0); command_word(3,16'h1234,3); check_length(256,"short END preserves observer");
      // Each aborted frame is followed by a fresh legal BEGIN using single strap 7.
      byte_out(1,0); nibble(4'hB,0);
      restart(0,7); command_word(1,165,0); check_length(165,"reset discarded half frame");
      byte_out(3,0); byte_out(8'hFE,0); nibble(4'h1,0);
      restart(1,7); command_word(1,1,0); check_length(1,"deselect discarded partial END");
      command_word(2,16'h5000,0); command_word(3,16'h5000,0); byte_out(4,0);
      check_length(1,"running length one pc zero");
      if (!response[6] || pc!==0)$fatal(1,"not running at zero");
      command_word(1,256,2);check_length(1,"rejected running BEGIN retains one");
      byte_out(0,0); nibble(0,-1);nibble(0,-1);
      byte_out(7,1);byte_out(8'hE5,3);
      byte_out(5,0);check_length(1,"ready after STOP and variable frames");
      command_word(1,2,0);command_word(2,16'h6800,0);command_word(2,16'h5001,0);command_word(3,16'hB801,0);
      byte_out(4,0);repeat(8)@(negedge clk); byte_out(7,0);nibble(0,0);nibble(0,0);
      byte_out(5,0);
      command_word(1,256,0);check_length(256,"success RX dummy alignment");
      checksum=0;
      for(i=0;i<256;i=i+1)begin
          word_value=(i==0 || i==255)?16'h50ff:0;
          command_word(2,word_value,0);checksum=checksum+word_value;
      end
      command_word(3,checksum,0);byte_out(4,0);
      repeat(7)@(negedge clk);
      check_length(256,"actual running at PC 255");
      if(!response[6] || pc!==8'hff)$fatal(1,"PC max witness missing");
      command_word(1,1,2);check_length(256,"running max rejects new length");
      if(changed_data<100||running_cycles<40)$fatal(1,"trace not diverse");
      $display("PASS ticks=%0d accepted=%0d changed_after_ack=%0d checks=%0d running_cycles=%0d",ticks,accepted_nibbles,changed_data,checks,running_cycles);
      $finish;
    end
endmodule
