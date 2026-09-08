.code32
.text
.balign 4

.global fruit_connected_with_accepted_nick
.type fruit_connected_with_accepted_nick, @function
fruit_connected_with_accepted_nick:
    # cdecl callback(peer, success, failureReason, provider).
    # Preserve callee-saved registers; align the stack to 16 before calling out.
    pushl   %esi
    pushl   %edi
    subl    $20, %esp
    cmpl    $0, 36(%esp)
    je      .Lpublish
    movl    32(%esp), %eax
    testl   %eax, %eax
    je      .Lpublish
    movl    44(%esp), %edi
    testl   %edi, %edi
    je      .Lpublish
    movl    %eax, (%esp)
    call    fruit_peer_get_nick
    testl   %eax, %eax
    je      .Lpublish
    movl    %eax, %esi
    addl    $164, %edi
    movl    $63, %ecx
.Lcopy:
    movb    (%esi), %al
    incl    %esi
    movb    %al, (%edi)
    incl    %edi
    testb   %al, %al
    je      .Lpublish
    decl    %ecx
    jne     .Lcopy
    movb    $0, (%edi)
.Lpublish:
    addl    $20, %esp
    popl    %edi
    popl    %esi
    jmp     fruit_original_connected
.size fruit_connected_with_accepted_nick, .-fruit_connected_with_accepted_nick
