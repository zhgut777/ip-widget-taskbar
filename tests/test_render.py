import unittest

from PIL import Image

from ipwidget import i18n, render
from ipwidget.netinfo import IpInfo
from ipwidget.state import Alert, Status, View


def ok_view(**kw):
    base = dict(status=Status.OK, cc="NL", country="Нидерланды", ip="203.0.113.42")
    base.update(kw)
    return View(**base)


class PlateTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("ru")

    def test_basic_plate_is_rgba_and_reasonable(self):
        img = render.render_plate(ok_view(), 2.0)
        self.assertEqual(img.mode, "RGBA")
        self.assertGreater(img.width, 100)
        self.assertEqual(img.height, round(render.BAR_H * 2) + 2 * max(2, round(3 * 2)))

    def test_clickable_everywhere(self):
        """Вся плашка должна иметь alpha>0, иначе Windows пропустит клик сквозь неё."""
        img = render.render_plate(ok_view(), 2.0)
        pad = max(2, round(3 * 2))
        alpha = img.getchannel("A")
        for x in (pad + 20, img.width // 2, img.width - pad - 20):
            for y in (pad + 8, img.height // 2, img.height - pad - 8):
                self.assertGreater(alpha.getpixel((x, y)), 0, (x, y))

    def test_long_ipv6_never_exceeds_limit(self):
        ip = "2a01:4f8:c17:1234:abcd:ef01:2345:6789"
        for scale in (1.0, 1.5, 2.0, 3.0):
            limit = round(render.BAR_MAX_W * scale)
            pad = max(2, round(3 * scale))
            img = render.render_plate(ok_view(ip=ip, country="Соединённые Штаты Америки"), scale)
            self.assertLessEqual(img.width - 2 * pad, limit + 2, scale)

    def test_long_country_is_cut_not_ip(self):
        title, ip = render.plate_texts(ok_view(country="Государство Палестина и ещё что-то"))
        self.assertEqual(ip, "203.0.113.42")
        wide = render.render_plate(ok_view(country="X" * 200), 1.0)
        self.assertLessEqual(wide.width, render.BAR_MAX_W + 2 * 3 + 2)

    def test_states_render(self):
        for view in (View(status=Status.LOADING), View(status=Status.ERROR), ok_view(stale=True),
                     ok_view(alert=Alert.MISMATCH), ok_view(alert=Alert.CHANGED)):
            for dark in (True, False):
                for hover in (True, False):
                    self.assertGreater(render.render_plate(view, 1.0, hover, dark).width, 50)

    def test_alert_changes_pixels(self):
        plain = render.render_plate(ok_view(), 2.0).tobytes()
        self.assertNotEqual(plain, render.render_plate(ok_view(alert=Alert.MISMATCH), 2.0).tobytes())
        self.assertNotEqual(plain, render.render_plate(ok_view(), 2.0, hover=True).tobytes())

    def test_text_is_not_darkened_at_edges(self):
        """Цвет пикселей по краю букв не должен темнеть (алгоритм «маска -> цветной слой»)."""
        img = Image.new("RGBA", (120, 40), (0, 0, 0, 0))
        render.draw_text(img, (4, 30), "Hello", render.font("semibold", 24), (240, 240, 244, 255))
        raw = img.tobytes()
        colored = [raw[i:i + 4] for i in range(0, len(raw), 4) if raw[i + 3] > 0]
        self.assertTrue(colored)
        self.assertTrue(all(p[0] >= 238 for p in colored))

    def test_premultiplied_bytes_length(self):
        img = render.render_plate(ok_view(), 1.0)
        self.assertEqual(len(render.premultiplied_bgra(img)), img.width * img.height * 4)

    def test_wrap_and_fit(self):
        fnt = render.font("regular", 14)
        lines = render.wrap_text("слово " * 60, fnt, 200, max_lines=2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(render.text_width(l, fnt) <= 201 for l in lines))
        self.assertEqual(render.fit_text("abc", fnt, 1000), "abc")
        self.assertEqual(render.fit_text("abcdef" * 20, fnt, 5), "…")


class TipTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "ru")

    def info(self, **kw):
        base = dict(ip="203.0.113.42", cc="NL", country_en="Netherlands", city="Rotterdam",
                    region="South Holland", tz="+01:00", isp="Snel.com B.V.", source="ipwho.is",
                    fetched_at=1_700_000_000.0)
        base.update(kw)
        return IpInfo(**base)

    def test_rows_with_both_families(self):
        data = render.build_tip(self.info(ip="2001:db8::5", ip_other="203.0.113.42"), ok_view())
        labels = [r[0] for r in data.rows]
        self.assertEqual(labels[:2], ["IPv4", "IPv6"])
        data = render.build_tip(self.info(ip="2001:db8::5", ip_other="203.0.113.42"), ok_view(), show_both=False)
        self.assertEqual(data.rows[0][0], "IP")

    def test_subtitle_without_duplicates(self):
        self.assertEqual(render.build_tip(self.info(city="Moscow", region="Moscow"), ok_view()).subtitle, "Moscow")
        self.assertEqual(render.build_tip(self.info(city="", region="Bavaria"), ok_view()).subtitle, "Bavaria")

    def test_notes(self):
        kinds = lambda d: [n.kind for n in d.notes]
        self.assertEqual(kinds(render.build_tip(self.info(), ok_view())), ["info"])
        self.assertEqual(kinds(render.build_tip(self.info(), ok_view(stale=True), "no network")), ["warn"])
        data = render.build_tip(self.info(), ok_view(alert=Alert.MISMATCH), expected_name="Russia")
        self.assertEqual(kinds(data), ["info", "bad"])
        self.assertIn("Russia", data.notes[-1].text)

    def test_no_data_tip(self):
        data = render.build_tip(None, View(status=Status.ERROR), "no network")
        self.assertEqual(data.title, "No data")
        self.assertEqual(data.notes[0].kind, "bad")

    def test_tip_renders(self):
        for dark in (True, False):
            for info in (self.info(), self.info(ip="2a01:4f8:c17:1234:abcd:ef01:2345:6789", ip_other="198.51.100.7",
                                                 isp="Очень длинное название провайдера " * 4)):
                img = render.render_tip(render.build_tip(info, ok_view(stale=True, alert=Alert.CHANGED), "timeout"),
                                        2.0, dark)
                self.assertEqual(img.width, round(render.TIP_W * 2))
                self.assertGreater(img.height, 150)
        self.assertGreater(render.render_tip(render.build_tip(None, View(status=Status.ERROR), "x"), 1.0).height, 60)


if __name__ == "__main__":
    unittest.main()
