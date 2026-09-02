import unittest

from fruitspy.crypto import EnctypeX, PeerChatCipher, gsseckey


class CryptoTests(unittest.TestCase):
    def test_gsseckey_known_vector(self) -> None:
        self.assertEqual(gsseckey("ABCDEF", "kbeafe"), "iJbjNilG")

    def test_peerchat_cipher_round_trip(self) -> None:
        sender = PeerChatCipher("0123456789ABCDEF", "nNfhSl")
        receiver = PeerChatCipher("0123456789ABCDEF", "nNfhSl")
        plaintext = b"NICK player\r\nUSER player 0 * :Player\r\n"
        ciphertext = sender.transform(plaintext)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(receiver.transform(ciphertext), plaintext)

    def test_enctypex_round_trip_across_chunks(self) -> None:
        validate = b"12345678"
        server_challenge = bytes(range(25))
        encoder = EnctypeX("nNfhSl", validate, server_challenge)
        decoder = EnctypeX("nNfhSl", validate, server_challenge)
        first = b"server-list-prefix"
        second = b"server-entry-and-final-marker"
        encrypted_first = encoder.encrypt(first)
        encrypted_second = encoder.encrypt(second)
        self.assertEqual(decoder.decrypt(encrypted_first), first)
        self.assertEqual(decoder.decrypt(encrypted_second), second)


if __name__ == "__main__":
    unittest.main()
