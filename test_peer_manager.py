import tempfile
import unittest

from peer_manager import PeerManager, PeerState


class TestBanPersistence(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.mgr = PeerManager("127.0.0.1", 9000, self.data_dir.name)
        self.mgr.add("127.0.0.1", 9001)

    def tearDown(self):
        self.data_dir.cleanup()

    def _ban(self):
        for _ in range(self.mgr.BAN_AFTER):
            self.mgr.mark_failed("127.0.0.1", 9001)

    def test_repeated_failures_ban_a_peer(self):
        self._ban()

        self.assertTrue(self.mgr.is_banned("127.0.0.1", 9001))

    def test_mark_connected_does_not_resurrect_a_banned_peer(self):
        # This is the exact bug: mark_connected() used to unconditionally
        # overwrite state with CONNECTED and reset fail_count to 0,
        # regardless of what the peer's state was beforehand -- silently
        # undoing a ban the moment the peer reconnects.
        self._ban()

        self.mgr.mark_connected("127.0.0.1", 9001)

        peer = next(p for p in self.mgr.all_peers() if p.port == 9001)
        self.assertEqual(peer.state, PeerState.BANNED)
        self.assertEqual(peer.fail_count, self.mgr.BAN_AFTER)

    def test_mark_connected_still_works_normally_for_a_non_banned_peer(self):
        # The fix must not break the ordinary case: an unbanned peer
        # still transitions to CONNECTED and has its fail_count reset.
        self.mgr.mark_failed("127.0.0.1", 9001)  # one failure, not a ban

        self.mgr.mark_connected("127.0.0.1", 9001)

        peer = next(p for p in self.mgr.all_peers() if p.port == 9001)
        self.assertEqual(peer.state, PeerState.CONNECTED)
        self.assertEqual(peer.fail_count, 0)

    def test_is_banned_is_false_for_a_known_but_unbanned_peer(self):
        self.assertFalse(self.mgr.is_banned("127.0.0.1", 9001))

    def test_is_banned_is_false_for_an_unknown_peer(self):
        self.assertFalse(self.mgr.is_banned("127.0.0.1", 12345))


if __name__ == "__main__":
    unittest.main()
