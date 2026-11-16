import tempfile
import unittest
from pathlib import Path

from ipwidget import config, i18n, layout, state
from ipwidget.netinfo import IpInfo


class ConfigTest(unittest.TestCase):
    def test_defaults_for_garbage(self):
        for raw in (None, [], "x", 5, {"interval_min": "десять", "language": "fr", "placement": 3,
                                      "alert_on_change": "yes", "expected_cc": "RUS"}):
            cfg = config.Config.from_dict(raw)
            self.assertEqual(cfg, config.Config(), raw)

    def test_valid_values_kept(self):
        cfg = config.Config.from_dict({"interval_min": 30, "language": "en", "placement": "right",
                                       "alert_on_change": False, "expected_cc": " nl "})
        self.assertEqual((cfg.interval_min, cfg.language, cfg.placement, cfg.alert_on_change, cfg.expected_cc),
                         (30, "en", "right", False, "NL"))

    def test_interval_must_be_known(self):
        for bad in (-5, 7, True, 3.5, 99999):
            self.assertEqual(config.Config.from_dict({"interval_min": bad}).interval_min, 10, bad)
        self.assertEqual(config.Config.from_dict({"interval_min": 0}).interval_min, 0)

    def test_v1_config_is_accepted(self):
        v1 = {"mode": "bar", "x": 2328, "y": 1644, "topmost": True, "autostart": False, "interval_min": 5}
        cfg = config.Config.from_dict(v1)
        self.assertEqual(cfg.interval_min, 5)

    def test_roundtrip_and_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "config.json"
            cfg = config.Config(interval_min=1, language="ru", expected_cc="DE")
            config.save_config(cfg, path)
            self.assertEqual(config.load_config(path), cfg)
            path.write_text("{ не json", encoding="utf-8")
            self.assertEqual(config.load_config(path), config.Config())
            self.assertEqual(config.load_config(Path(tmp) / "missing.json"), config.Config())


class StateTest(unittest.TestCase):
    def test_retry_delay_grows_then_caps(self):
        self.assertEqual([state.retry_delay(n) for n in (0, 1, 2, 3, 4, 5, 6, 50)],
                         [15, 15, 30, 60, 120, 300, 300, 300])

    def info(self, ip="198.51.100.5", cc="NL", other=""):
        return IpInfo(ip=ip, cc=cc, ip_other=other)

    def test_changed(self):
        self.assertFalse(state.changed(None, self.info()))
        self.assertFalse(state.changed(self.info(), self.info()))
        self.assertTrue(state.changed(self.info(), self.info(ip="198.51.100.9")))
        self.assertTrue(state.changed(self.info(), self.info(cc="DE")))

    def test_family_flip_is_not_a_change(self):
        v4 = self.info(ip="198.51.100.5", other="2001:db8::5")
        v6 = self.info(ip="2001:db8::5", other="198.51.100.5")
        self.assertFalse(state.changed(v4, v6))
        v6_new = self.info(ip="2001:db8::6", other="198.51.100.5")
        self.assertFalse(state.changed(v4, v6_new))         # IPv4 тот же — считаем сеть прежней
        v6_other_v4 = self.info(ip="2001:db8::5", other="198.51.100.77")
        self.assertTrue(state.changed(v4, v6_other_v4))

    def test_alert_priority(self):
        nl = self.info(cc="NL")
        self.assertEqual(state.alert_for(None, "RU", True, True), state.Alert.NONE)
        self.assertEqual(state.alert_for(nl, "RU", True, True), state.Alert.MISMATCH)
        self.assertEqual(state.alert_for(nl, "NL", True, True), state.Alert.CHANGED)
        self.assertEqual(state.alert_for(nl, "NL", False, True), state.Alert.NONE)
        self.assertEqual(state.alert_for(nl, "", True, False), state.Alert.NONE)


class LayoutTest(unittest.TestCase):
    def test_merge(self):
        self.assertEqual(layout.merge_intervals([(10, 20), (18, 30), (40, 50)]), [(10, 30), (40, 50)])
        self.assertEqual(layout.merge_intervals([(10, 20), (22, 30)], gap=5), [(10, 30)])
        self.assertEqual(layout.merge_intervals([]), [])

    def test_gaps(self):
        self.assertEqual(layout.free_gaps(0, 100, [(10, 20), (50, 60)]), [(0, 10), (20, 50), (60, 100)])
        self.assertEqual(layout.free_gaps(0, 100, [(-50, 20), (90, 400)]), [(20, 90)])
        self.assertEqual(layout.free_gaps(0, 100, []), [(0, 100)])

    def test_slot_left_and_right(self):
        occupied = [(300, 700), (900, 1000)]       # значки по центру, трей справа
        self.assertEqual(layout.find_slot(0, 1000, occupied, 120, 10, "left"), 10)
        self.assertEqual(layout.find_slot(0, 1000, occupied, 120, 10, "right"), 900 - 10 - 120)

    def test_slot_none_when_crowded(self):
        self.assertIsNone(layout.find_slot(0, 400, [(100, 300)], 150, 10))
        self.assertEqual(layout.find_slot(0, 400, [(100, 300)], 60, 10), 10)


class I18nTest(unittest.TestCase):
    def test_both_languages_have_same_keys(self):
        self.assertEqual(set(i18n.STRINGS["ru"]), set(i18n.STRINGS["en"]))

    def test_country_names(self):
        i18n.set_language("ru")
        self.assertEqual(i18n.country_name("NL"), "Нидерланды")
        i18n.set_language("en")
        self.assertEqual(i18n.country_name("nl"), "Netherlands")
        self.assertEqual(i18n.country_name("ZZ", "Atlantis"), "Atlantis")
        self.assertEqual(i18n.country_name("", ""), "Unknown")
        i18n.set_language("ru")

    def test_format_arguments(self):
        i18n.set_language("en")
        self.assertIn("12:30", i18n.t("tip_updated", time="12:30"))
        i18n.set_language("ru")

    def test_unknown_key_falls_back_to_key(self):
        self.assertEqual(i18n.t("no_such_key"), "no_such_key")


if __name__ == "__main__":
    unittest.main()
