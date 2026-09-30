import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "scripts" / "collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

CFG = {
    "aliases": [
        "Χρήστος Κωνσταντινίδης", "Χρηστος Κωνσταντινιδης",
        "Christos Konstantinidis", "Hristos Konstantinidis",
        "Khristos Konstantinidis", "Christos Constantinidis", "Hristo Konstantinidis",
    ],
    "surname_aliases": ["Konstantinidis", "Constantinidis", "Κωνσταντινίδης"],
    "identity_context": ["Geopolitico", "gazeteci", "journalist", "δημοσιογράφος", "yayın yönetmeni", "podcast"],
    "authored_markers": ["By Christos Konstantinidis", "Γράφει ο Χρήστος Κωνσταντινίδης"],
}


class MonitorRegressionTests(unittest.TestCase):
    def test_milliyet_real_sample(self):
        text = "Geopolitico.gr yayın yönetmeni Christos Konstantinidis Türkiye-Yunanistan ilişkilerini ele aldığı podcastte konuştu."
        kind, hits = collector.classify_mention(text, CFG)
        self.assertEqual(kind, "personal_reference")
        self.assertIn("Christos Konstantinidis", hits)

    def test_cnn_turk_real_transliteration(self):
        text = "Haber portalının genel yayın yönetmeni Hristos Konstantinidis yayımladığı bir podcast'te değerlendirdi."
        kind, _ = collector.classify_mention(text, CFG)
        self.assertEqual(kind, "personal_reference")

    def test_hristo_variant_documented_in_turkish_republication(self):
        text = "Hristo Konstantinidis Türk Medyasına Seslendi; Geopolitico podcasti tartışıldı."
        kind, _ = collector.classify_mention(text, CFG)
        self.assertEqual(kind, "personal_reference")

    def test_contextual_surname_from_mezopotamya_title(self):
        kind, _ = collector.classify_mention("Gazeteci Konstantinidis: AB destekli proje Türkiye-Yunanistan ilişkilerini sınayacak", CFG)
        self.assertEqual(kind, "personal_reference")

    def test_surname_alone_is_not_promoted(self):
        kind, _ = collector.classify_mention("Konstantinidis scored in the match.", CFG)
        self.assertIsNone(kind)

    def test_authored_article_is_not_personal_reference(self):
        kind, _ = collector.classify_mention("By Christos Konstantinidis | Middle East Forum", CFG)
        self.assertEqual(kind, "authored_byline")

    def test_same_surname_without_context_not_promoted(self):
        kind, _ = collector.classify_mention("Nikos Konstantinidis won the local match.", CFG)
        self.assertIsNone(kind)

    def test_exact_name_without_identity_context_is_candidate(self):
        kind, _ = collector.classify_mention("Christos Konstantinidis attended the event.", CFG)
        self.assertEqual(kind, "name_candidate")

    def test_greece_can_match_outside_title(self):
        rules = {"high": {"Yunanistan": 30, "Doğu Akdeniz": 30}, "medium": {"savunma": 10}}
        score, hits = collector.score_text("Başlık farklı. Haberin gövdesinde Yunanistan ve Doğu Akdeniz savunma konusu var.", rules)
        self.assertGreaterEqual(score, 70)
        self.assertIn("Yunanistan", hits)

    def test_cnn_turk_gsi_case_passes_from_title_and_snippet(self):
        title = "Türkiye korkusu geri adım mı attırdı? Elektrik hattı neden krize dönüştü?"
        snippet = "Güney Kıbrıs ile Yunanistan arasındaki Great Sea Interconnector projesinde Fransa, Nexans ve Meridiam gündemde. Ankara kıta sahanlığı iddiasını sürdürüyor."
        required_any = ["Yunanistan", "Atina", "Güney Kıbrıs", "Doğu Akdeniz", "kıta sahanlığı", "elektrik hattı", "elektrik bağlantısı", "kablo", "Great Sea Interconnector", "GSI", "Nexans", "Meridiam", "Fransa", "araştırma gemisi", "NAVTEX"]
        text = f"{title} {snippet}"
        self.assertTrue(any(collector.keyword_matches(text, kw) for kw in required_any))

        rules = {
            "high": {
                "Yunanistan": 30, "Güney Kıbrıs": 30, "kıta sahanlığı": 30,
                "elektrik hattı": 25, "Nexans": 20, "Meridiam": 20,
            },
            "medium": {
                "Fransa": 10, "kriz": 15, "Great Sea Interconnector": 20,
                "GSI": 15, "kablo": 10, "elektrik bağlantısı": 12,
            },
        }
        score, hits = collector.score_text(text, rules)
        weighted = min(100, round(score * 1.10))
        self.assertGreaterEqual(weighted, 35)
        self.assertIn("elektrik hattı", hits)
        self.assertIn("Yunanistan", hits)

    def test_acronym_boundary_still_prevents_false_positive(self):
        self.assertFalse(collector.keyword_matches("crocodilettpstory", "TTP"))
        self.assertTrue(collector.keyword_matches("TTP saldırısı", "TTP"))


if __name__ == "__main__":
    unittest.main()
