.syntax unified
.arm
.text
.balign 4

.global fruit_connected_with_accepted_nick
.type fruit_connected_with_accepted_nick, %function
fruit_connected_with_accepted_nick:
    @ peerConnectCallback(peer, success, failureReason, provider).
    @ Preserve its arguments and 8-byte stack alignment across peerGetNickA.
    push    {r0, r1, r2, r3, r4, lr}
    cmp     r1, #0
    beq     .Lpublish
    cmp     r0, #0
    beq     .Lpublish
    cmp     r3, #0
    beq     .Lpublish
    mov     r4, r3
    bl      fruit_peer_get_nick
    cmp     r0, #0
    beq     .Lpublish
    add     r1, r4, #164
    mov     r2, #63
.Lcopy:
    ldrb    r3, [r0], #1
    strb    r3, [r1], #1
    cmp     r3, #0
    beq     .Lpublish
    subs    r2, r2, #1
    bne     .Lcopy
    mov     r3, #0
    strb    r3, [r1]
.Lpublish:
    @ Only now may the original callback publish success to the game loop.
    pop     {r0, r1, r2, r3, r4, lr}
    b       fruit_original_connected
.size fruit_connected_with_accepted_nick, .-fruit_connected_with_accepted_nick

.balign 4
.global fruit_connect_with_preferred_nick
.type fruit_connect_with_preferred_nick, %function
fruit_connect_with_preferred_nick:
    @ Preserve peerConnect's register and stack arguments across the native setter.
    push    {r0, r1, r2, r3, r4, lr}
    ldr     r0, [sp, #28]
    cmp     r0, #0
    beq     .Lconnect
    ldr     r1, .Lpreferred_delta
.Lpreferred_base:
    add     r1, pc, r1
    bl      fruit_set_nick
.Lconnect:
    pop     {r0, r1, r2, r3, r4, lr}
    b       fruit_peer_connect
.Lpreferred_delta:
    .word   fruit_configured_nick - (.Lpreferred_base + 8)
.size fruit_connect_with_preferred_nick, .-fruit_connect_with_preferred_nick
