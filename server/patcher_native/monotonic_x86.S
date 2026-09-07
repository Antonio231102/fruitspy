.code32
.text
.balign 4

.global fruit_monotonic_us32
.type fruit_monotonic_us32, @function
fruit_monotonic_us32:
    pushl   %ebx
    pushl   %esi
    subl    $8, %esp
    # i386 syscall 265: clock_gettime(CLOCK_MONOTONIC, esp).
    movl    $265, %eax
    movl    $1, %ebx
    leal    (%esp), %ecx
    int     $0x80
    imull   $1000000, (%esp), %esi
    movl    4(%esp), %eax
    cltd
    movl    $1000, %ecx
    idivl   %ecx
    addl    %esi, %eax
    addl    $8, %esp
    popl    %esi
    popl    %ebx
    ret
.size fruit_monotonic_us32, .-fruit_monotonic_us32

.balign 4
.global fruit_clamp_and_resume
.type fruit_clamp_and_resume, @function
fruit_clamp_and_resume:
    # Treat the modular subtraction as unsigned before signed float conversion.
    cmpl    $250000, %eax
    jbe     .Lconvert
    movl    $16667, %eax
.Lconvert:
    cvtsi2ssl %eax, %xmm0
    movl    0x34(%esp), %eax
    jmp     fruit_conversion_resume
.size fruit_clamp_and_resume, .-fruit_clamp_and_resume
