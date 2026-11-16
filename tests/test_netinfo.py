import io
import socket
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PIL import Image

from ipwidget import netinfo
from ipwidget.netinfo import (Fetcher, NetError, Source, classify_error, clean_cc, clean_ip,
                              clean_text, offset_text, shorten_ip)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def png_bytes(size=(40, 27), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


class CleanersTest(unittest.TestCase):
    def test_clean_ip(self):
        self.assertEqual(clean_ip(" 203.0.113.7 "), "203.0.113.7")
        self.assertEqual(clean_ip("2A01:04F8:0000::0001"), "2a01:4f8::1")
        self.assertEqual(clean_ip("fe80::1%eth0"), "fe80::1")
        for bad in ("", "999.1.1.1", "<script>", None, 5, "1.2.3.4; rm", "a/b"):
            self.assertEqual(clean_ip(bad), "", bad)

    def test_clean_cc(self):
        self.assertEqual(clean_cc("nl"), "NL")
        for bad in ("", "N", "NLD", "a/", "..", "1A", "Ы", None, 12):
            self.assertEqual(clean_cc(bad), "", bad)

    def test_clean_text(self):
        self.assertEqual(clean_text("  Hello\x00\n World  "), "Hello World")
        self.assertEqual(len(clean_text("x" * 500)), 80)
        self.assertEqual(clean_text(None), "")

    def test_offset_text(self):
        self.assertEqual(offset_text("+0300"), "+03:00")
        self.assertEqual(offset_text("-05:30"), "-05:30")
        self.assertEqual(offset_text(10800), "+03:00")
        self.assertEqual(offset_text(-12600), "-03:30")
        self.assertEqual(offset_text("Europe/Moscow"), "")
        self.assertEqual(offset_text(True), "")

    def test_shorten_ip(self):
        self.assertEqual(shorten_ip("203.0.113.42"), "203.0.113.42")
        self.assertEqual(shorten_ip("2a01:4f8::1"), "2a01:4f8::1")
        long6 = "2a01:4f8:c17:1234:abcd:ef01:2345:6789"
        short = shorten_ip(long6)
        self.assertLessEqual(len(short), 18)
        self.assertTrue(short.startswith("2a01:4f8:"))
        self.assertTrue(short.endswith(":6789"))
        self.assertEqual(shorten_ip("::1"), "::1")


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify_error(socket.timeout()).kind, "timeout")
        self.assertEqual(classify_error(urllib.error.URLError(socket.timeout())).kind, "timeout")
        self.assertEqual(classify_error(urllib.error.URLError(ssl.SSLCertVerificationError())).kind, "cert")
        self.assertEqual(classify_error(urllib.error.HTTPError("u", 429, "x", {}, None)).kind, "server")
        self.assertEqual(classify_error(urllib.error.URLError("getaddrinfo failed")).kind, "network")
        self.assertEqual(classify_error(ValueError("x")).kind, "bad_response")
        self.assertEqual(classify_error(RuntimeError("x")).kind, "unknown")

    def test_https_only(self):
        with self.assertRaises(NetError):
            netinfo._open("http://ip-api.com/json", 1)


class ParsersTest(unittest.TestCase):
    def test_ipwho(self):
        info = netinfo._from_ipwho({
            "success": True, "ip": "203.0.113.42", "country": "Netherlands", "country_code": "nl",
            "city": "Rotterdam", "region": "South Holland",
            "timezone": {"id": "Europe/Amsterdam", "utc": "+01:00"},
            "connection": {"isp": "Snel.com B.V."}})
        self.assertEqual((info.ip, info.cc, info.tz, info.isp), ("203.0.113.42", "NL", "+01:00", "Snel.com B.V."))
        self.assertEqual(info.source, "ipwho.is")

    def test_ipwho_failure(self):
        with self.assertRaises(NetError):
            netinfo._from_ipwho({"success": False, "message": "limit"})

    def test_ipapi(self):
        info = netinfo._from_ipapi({"ip": "2001:db8::1", "country_code": "DE", "country_name": "Germany",
                                    "utc_offset": "+0100", "org": "Hetzner"})
        self.assertEqual((info.ip, info.cc, info.tz), ("2001:db8::1", "DE", "+01:00"))

    def test_ipinfo_strips_asn(self):
        info = netinfo._from_ipinfo({"ip": "198.51.100.5", "country": "RU", "org": "AS12389 Rostelecom"})
        self.assertEqual(info.isp, "Rostelecom")

    def test_garbage_ip_is_rejected(self):
        with self.assertRaises(NetError):
            netinfo._from_ipwho({"success": True, "ip": "<b>hi</b>", "country_code": "NL"})

    def test_garbage_country_is_dropped(self):
        info = netinfo._from_ipwho({"success": True, "ip": "198.51.100.5", "country_code": "../"})
        self.assertEqual(info.cc, "")


def make_source(name, result):
    def parse(_data):
        if isinstance(result, Exception):
            raise result
        return result
    return Source(name, f"https://{name}/", parse)


def info_for(name, ip="198.51.100.5", cc="NL"):
    return netinfo.IpInfo(ip=ip, cc=cc, source=name)


class FetcherTest(unittest.TestCase):
    def make(self, sources, calls=None, text=None):
        clock = Clock()

        def getter(url, timeout):
            if calls is not None:
                calls.append(url)
            return {}

        fetcher = Fetcher(sources, clock=clock, getter=getter, text_getter=text or (lambda u, t: ""))
        return fetcher, clock

    def test_falls_back_to_next_source(self):
        a = make_source("a", NetError("timeout"))
        b = make_source("b", info_for("b"))
        fetcher, _ = self.make((a, b))
        self.assertEqual(fetcher.fetch(False).source, "b")

    def test_last_good_goes_first_and_failed_is_skipped(self):
        calls = []
        a = make_source("a", NetError("timeout"))
        b = make_source("b", info_for("b"))
        fetcher, clock = self.make((a, b), calls)
        fetcher.fetch(False)
        calls.clear()
        fetcher.fetch(False)
        self.assertEqual(calls, ["https://b/"])           # a в «остывании», b — первый
        clock.now += netinfo.SOURCE_COOLDOWN + 1
        self.assertEqual([s.name for s in fetcher.order()], ["b", "a"])

    def test_all_fail_raises_aggregate(self):
        fetcher, _ = self.make((make_source("a", NetError("timeout")), make_source("b", NetError("timeout"))))
        with self.assertRaises(NetError) as ctx:
            fetcher.fetch(False)
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_all_in_cooldown_still_tried(self):
        a = make_source("a", NetError("network"))
        fetcher, _ = self.make((a,))
        with self.assertRaises(NetError):
            fetcher.fetch(False)
        with self.assertRaises(NetError):                  # не «нет источников», а реальная попытка
            fetcher.fetch(False)

    def test_other_family_is_fetched_and_cached(self):
        texts = []

        def text(url, timeout):
            texts.append(url)
            return "203.0.113.9"

        fetcher, clock = self.make((make_source("a", info_for("a", ip="2001:db8::5")),), text=text)
        info = fetcher.fetch()
        self.assertEqual(info.ip_other, "203.0.113.9")
        self.assertEqual(info.ipv4, "203.0.113.9")
        self.assertEqual(info.ipv6, "2001:db8::5")
        fetcher.fetch()
        self.assertEqual(len(texts), 1)                    # второй адрес закэширован
        clock.now += netinfo.OTHER_FAMILY_TTL + 1
        fetcher.fetch()
        self.assertEqual(len(texts), 2)

    def test_other_family_failure_is_harmless(self):
        def text(url, timeout):
            raise NetError("timeout")

        fetcher, _ = self.make((make_source("a", info_for("a")),), text=text)
        self.assertEqual(fetcher.fetch().ip_other, "")

    def test_other_family_same_family_is_ignored(self):
        fetcher, _ = self.make((make_source("a", info_for("a", ip="198.51.100.5")),),
                               text=lambda u, t: "198.51.100.77")
        self.assertEqual(fetcher.fetch().ip_other, "")


class FlagCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(netinfo, "CACHE_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_downloads_valid_png(self):
        with mock.patch.object(netinfo, "_open", return_value=png_bytes()) as op:
            netinfo.ensure_flag("nl")
        self.assertIn("/nl.png", op.call_args[0][0])
        self.assertIsNotNone(netinfo.load_flag("NL"))

    def test_rejects_html_instead_of_png(self):
        with mock.patch.object(netinfo, "_open", return_value=b"<html>error</html>"):
            with self.assertRaises(NetError):
                netinfo.ensure_flag("NL")
        self.assertFalse((Path(self.tmp.name) / "nl.png").exists())

    def test_rejects_non_png_image(self):
        with mock.patch.object(netinfo, "_open", return_value=png_bytes(fmt="GIF")):
            with self.assertRaises(NetError):
                netinfo.ensure_flag("NL")

    def test_corrupt_cache_is_replaced(self):
        path = Path(self.tmp.name) / "nl.png"
        path.write_bytes(b"\x89PNG broken")
        self.assertIsNone(netinfo.load_flag("NL"))          # нечитаемый файл удалён
        self.assertFalse(path.exists())
        path.write_bytes(b"\x89PNG broken")
        with mock.patch.object(netinfo, "_open", return_value=png_bytes()):
            netinfo.ensure_flag("NL")
        self.assertIsNotNone(netinfo.load_flag("NL"))

    def test_bad_country_code_does_nothing(self):
        with mock.patch.object(netinfo, "_open") as op:
            netinfo.ensure_flag("../x")
            netinfo.ensure_flag("")
        op.assert_not_called()


if __name__ == "__main__":
    unittest.main()
