from pathlib import Path
import unittest

class TransportMessageEncodingTests(unittest.TestCase):
    def test_host_transport_message_is_valid_chinese(self):
        expected = "\u672c\u673a\u5bc6\u7801\u8fde\u63a5\u914d\u7f6e\u5df2\u53d8\u5316\uff0c\u8bf7\u4fee\u590d\u672c\u673a\u6388\u6743"
        src = Path("src/vram_radar/ssh_transport.py").read_text(encoding="utf-8")
        loc = Path("src/vram_radar/web/localization.js").read_text(encoding="utf-8")
        self.assertIn(expected, src)
        self.assertIn(expected, loc)
        self.assertNotIn("\ufffd", src)
        self.assertIn("The local password connection configuration changed; repair its local authorization", loc)

if __name__ == "__main__":
    unittest.main()
