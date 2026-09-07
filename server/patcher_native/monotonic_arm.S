.syntax unified
.arm
.text
.balign 4

.global fruit_monotonic_us32
.type fruit_monotonic_us32, %function
fruit_monotonic_us32:
    push    {r7, lr}
    sub     sp, sp, #8
    @ ARM EABI syscall 263: clock_gettime(CLOCK_MONOTONIC, sp).
    mov     r0, #1
    mov     r1, sp
    mov     r7, #256
    add     r7, r7, #7
    svc     #0
    ldr     r2, [sp]
    ldr     r3, [sp, #4]
    @ Divide nanoseconds by 1000 without a hardware divide instruction.
    ldr     r0, .Ldivide_magic
    smull   r12, r1, r0, r3
    asr     r12, r3, #31
    rsb     r3, r12, r1, asr #6
    ldr     r0, .Lmicroseconds_per_second
    mla     r0, r2, r0, r3
    add     sp, sp, #8
    pop     {r7, pc}
.Ldivide_magic:
    .word   0x10624dd3
.Lmicroseconds_per_second:
    .word   1000000
.size fruit_monotonic_us32, .-fruit_monotonic_us32

.balign 4
.global fruit_clamp_and_convert
.type fruit_clamp_and_convert, %function
fruit_clamp_and_convert:
    @ Treat the modular subtraction as unsigned before signed float conversion.
    ldr     r1, .Lmaximum_delta
    cmp     r0, r1
    bls     .Lconvert
    mov     r0, #16640
    add     r0, r0, #27
.Lconvert:
    b       fruit_floatsisf
.Lmaximum_delta:
    .word   250000
.size fruit_clamp_and_convert, .-fruit_clamp_and_convert
