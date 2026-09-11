import json
import unittest
from pathlib import Path

from scraper import build_site, run
from pydantic import ValidationError

from scraper.models import (
    ImageGenerationOffering, ImageGenerationPage, VideoGenerationOffering,
    VideoGenerationPage,
)
from scripts import make_generation as seed


ROOT = Path(__file__).resolve().parents[1]


def records(kind):
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "data" / kind).glob("*.json")
    }


class GenerationSeedDataTests(unittest.TestCase):
    def test_seed_files_exactly_match_both_catalogs(self):
        for kind, catalog in (
                ("imagegen", run.load_imagegen_providers()),
                ("videogen", run.load_videogen_providers())):
            expected = {provider["id"] for provider in catalog}
            actual = set(records(kind))
            with self.subTest(kind=kind):
                self.assertEqual(actual, expected)
                for record in records(kind).values():
                    if record.get("source") == "official_seed":
                        self.assertTrue(record.get("seed_verified"))
                    else:
                        self.assertNotIn("seed_cutoff", record)

    def test_seed_metadata_is_reviewed_and_not_a_fake_fetch(self):
        catalogs = {
            "imagegen": {cfg["id"]: cfg for cfg in run.load_imagegen_providers()},
            "videogen": {cfg["id"]: cfg for cfg in run.load_videogen_providers()},
        }
        facts = {"imagegen": seed.IMAGE, "videogen": seed.VIDEO}
        for kind in ("imagegen", "videogen"):
            for pid, record in records(kind).items():
                if record.get("source") != "official_seed":
                    continue
                with self.subTest(kind=kind, provider=pid):
                    self.assertEqual(record["offerings"], facts[kind][pid])
                    expected = seed.verification_hash(
                        kind, catalogs[kind][pid], record["offerings"])
                    self.assertEqual(record["verification_hash"], expected)
                    self.assertEqual(record["verification_hash"],
                                     seed.VERIFIED_MANIFEST[kind][pid])
                    self.assertEqual(record["verified_at"], seed.VERIFIED_AT)
                    self.assertIsNone(record["fetched_at"])
                    self.assertIsNone(record["page_has_relevant_content"])
                    self.assertTrue(record.get("seeded_at"))

    def test_all_seed_offerings_validate_against_pydantic_schemas(self):
        for kind, model, flag in (
                ("imagegen", ImageGenerationPage, "has_image_generation"),
                ("videogen", VideoGenerationPage, "has_video_generation")):
            for pid, record in records(kind).items():
                with self.subTest(kind=kind, provider=pid):
                    model.model_validate({
                        "page_has_relevant_content": (
                            True if record.get("source") == "official_seed"
                            else record["page_has_relevant_content"]),
                        "product_status": record["product_status"],
                        "product_status_date": record["product_status_date"],
                        "product_status_note": record["product_status_note"],
                        flag: record[flag],
                        "offerings": record["offerings"],
                    })

    def test_video_fixed_example_derives_strict_group_from_objective_facts(self):
        offering = VideoGenerationOffering.model_validate({
            "name": "doubao-seedance-2.5",
            "api_available": True,
            "currency": "CNY",
            "price_per_second": 1.51,
            "comparison_price_type": "official-fixed-example",
            "price_basis": "官网 720p、16:9、5 秒固定场景元/秒示例",
            "resolution": "720p",
            "native_audio": True,
        })

        self.assertEqual(offering.comparison_group, "cny-720p-audio")
        self.assertEqual(offering.comparison_resolution, "720p")

    def test_temporary_video_chart_price_requires_complete_date_window(self):
        base = {
            "name": "promotional video",
            "api_available": True,
            "currency": "CNY",
            "price_per_second": 1.51,
            "comparison_price_type": "official-fixed-example",
            "price_basis": "720p 16:9 5秒原生有声",
            "resolution": "720p",
            "native_audio": True,
        }
        with self.assertRaisesRegex(ValidationError, "start and end dates"):
            VideoGenerationOffering.model_validate({
                **base, "note": "阶段性72折优惠",
            })

        accepted = VideoGenerationOffering.model_validate({
            **base,
            "note": "2026-08-14 14:00至2026-09-17 14:00（UTC+8）按刊例价72折",
        })
        self.assertEqual(accepted.comparison_group, "cny-720p-audio")

    def test_charted_seedance_promotions_keep_official_expiry_timestamps(self):
        record = records("videogen")["seedance"]
        rows = [row for row in record["offerings"]
                if row["name"] in ("doubao-seedance-2.0-fast",
                                   "doubao-seedance-2.0-mini")]
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("2026-08-07 14:00", row["note"])
            self.assertIn("2026-10-07 14:00", row["note"])
            self.assertIn("UTC+8", row["note"])
        seed25 = next(row for row in record["offerings"]
                      if row["name"] == "doubao-seedance-2.5"
                      and row["resolution"] == "1080p")
        self.assertIn("2026-08-14 14:00", seed25["note"])
        self.assertIn("2026-09-17 14:00", seed25["note"])
        self.assertIn("UTC+8", seed25["note"])

    def test_seedance_catalog_renders_dynamic_official_sources(self):
        cfg = next(item for item in run.load_videogen_providers()
                   if item["id"] == "seedance")

        self.assertTrue(cfg["videogen_render"])
        self.assertIn("https://docs.volcengine.com/docs/82379/1544106?lang=zh",
                      cfg["videogen_urls"])
        self.assertIn("https://seed.bytedance.com/en/seedance2_5",
                      cfg["videogen_urls"])

    def test_unknown_comparison_groups_fail_schema_validation(self):
        with self.assertRaises(ValidationError):
            ImageGenerationPage.model_validate({
                "page_has_relevant_content": True,
                "product_status": "active",
                "has_image_generation": True,
                "offerings": [{
                    "name": "bad", "comparison_group": "usd-anything",
                }],
            })
        with self.assertRaises(ValidationError):
            VideoGenerationPage.model_validate({
                "page_has_relevant_content": True,
                "product_status": "active",
                "has_video_generation": True,
                "offerings": [{
                    "name": "bad", "comparison_group": "usd-768p-audio",
                }],
            })

    def test_impossible_lifecycle_dates_are_rejected(self):
        with self.assertRaises(ValidationError):
            ImageGenerationPage.model_validate({
                "page_has_relevant_content": True, "product_status": "discontinued",
                "product_status_date": "2026-99-99", "has_image_generation": False,
            })
        with self.assertRaises(ValidationError):
            VideoGenerationPage.model_validate({
                "page_has_relevant_content": True, "product_status": "active",
                "has_video_generation": True, "offerings": [{
                    "name": "bad date", "sunset_at": "2026-02-30",
                }],
            })

    def test_non_finite_and_incomplete_comparison_prices_are_rejected(self):
        base_page = {
            "page_has_relevant_content": True, "product_status": "active",
            "has_image_generation": True,
        }
        with self.assertRaises(ValidationError):
            ImageGenerationPage.model_validate({**base_page, "offerings": [{
                "name": "infinite", "price_per_image": float("inf"),
            }]})
        with self.assertRaises(ValidationError):
            ImageGenerationPage.model_validate({**base_page, "offerings": [{
                "name": "boolean", "price_per_image": True,
            }]})
        with self.assertRaises(ValidationError):
            ImageGenerationPage.model_validate({**base_page, "offerings": [{
                "name": "missing basis", "api_available": True, "currency": "USD",
                "price_per_image": .1, "comparison_group": "usd-standard-1mp",
                "comparison_width": 1024, "comparison_height": 1024,
                "quality_tier": "standard", "price_basis": None,
            }]})
        with self.assertRaises(ValidationError):
            VideoGenerationPage.model_validate({
                "page_has_relevant_content": True, "product_status": "active",
                "has_video_generation": True, "offerings": [{
                    "name": "infinite", "price_per_second": float("inf"),
                }],
            })
        with self.assertRaises(ValidationError):
            VideoGenerationPage.model_validate({
                "page_has_relevant_content": True, "product_status": "active",
                "has_video_generation": True, "offerings": [{
                    "name": "boolean", "price_per_second": False,
                }],
            })

    def test_generation_models_forbid_unknown_extraction_keys(self):
        with self.assertRaises(ValidationError):
            ImageGenerationOffering.model_validate({
                "name": "Typo", "price_per_img": 0.04,
            })
        with self.assertRaises(ValidationError):
            VideoGenerationPage.model_validate({
                "page_has_relevant_content": True,
                "product_status": "active",
                "has_video_generation": True,
                "offerings": [],
                "has_vidoe_generation": True,
            })

    def test_image_chart_entries_have_exact_group_currency_and_numeric_price(self):
        grouped = 0
        for pid, record in records("imagegen").items():
            for offering in record["offerings"]:
                group = offering.get("comparison_group")
                if not group:
                    continue
                grouped += 1
                with self.subTest(provider=pid, model=offering["name"]):
                    self.assertIn(group, build_site.IMAGE_PRICE_GROUPS)
                    self.assertEqual(group.split("-", 1)[0].upper(), offering["currency"])
                    self.assertIsInstance(offering["price_per_image"], (int, float))
                    self.assertGreaterEqual(offering["price_per_image"], 0)
                    self.assertTrue(offering.get("price_basis"))
        rendered = build_site._generation_price_entries(
            "imagegen", run.load_imagegen_providers(), records("imagegen"))
        active_grouped = sum(
            build_site._generation_lifecycle(offering)["active"]
            for record in records("imagegen").values()
            for offering in record["offerings"] if offering.get("comparison_group"))
        self.assertEqual(active_grouped, sum(len(items) for items in rendered.values()))

    def test_video_chart_entries_match_resolution_audio_and_currency(self):
        grouped = 0
        for pid, record in records("videogen").items():
            for offering in record["offerings"]:
                group = offering.get("comparison_group")
                if not group:
                    continue
                grouped += 1
                with self.subTest(provider=pid, model=offering["name"]):
                    self.assertIn(group, build_site.VIDEO_PRICE_GROUPS)
                    self.assertEqual(group.split("-", 1)[0].upper(), offering["currency"])
                    self.assertIsInstance(offering["price_per_second"], (int, float))
                    resolution_number = group.split("-")[1].removesuffix("p")
                    self.assertIn(resolution_number, offering["resolution"].lower())
                    self.assertEqual(group.endswith("-audio"), offering["native_audio"])
                    self.assertTrue(offering.get("price_basis"))
        rendered = build_site._generation_price_entries(
            "videogen", run.load_videogen_providers(), records("videogen"))
        active_grouped = sum(
            build_site._generation_lifecycle(offering)["active"]
            for record in records("videogen").values()
            for offering in record["offerings"] if offering.get("comparison_group"))
        self.assertEqual(active_grouped, sum(len(items) for items in rendered.values()))

    def test_reviewed_seed_snapshot_retains_key_policy_facts(self):
        image = seed.IMAGE
        for pid in ("openai", "google", "bfl", "adobe", "leonardo", "midjourney"):
            with self.subTest(provider=pid):
                self.assertFalse(any(item.get("comparison_group") for item in image[pid]))
        self.assertFalse(image["midjourney"][0]["api_available"])
        self.assertEqual(image["xai"][0]["price_per_image"], .06)
        self.assertIn("medium", image["xai"][0]["price_basis"])

        video = seed.VIDEO
        for pid in ("adobe", "minimax", "seedance", "pika", "stability"):
            with self.subTest(provider=pid):
                self.assertFalse(any(item.get("comparison_group") for item in video[pid]))
        self.assertFalse(video["stability"][0]["api_available"])
        self.assertEqual(video["stability"][0]["lifecycle_status"], "self-host-only")
        google_1080 = {item["name"]: item for item in video["google"]
                       if item.get("comparison_resolution") == "1080p"}
        self.assertEqual(set(google_1080), {
            "Veo 3.1 Fast 1080p", "Veo 3.1 Lite 1080p",
            "Veo 3.1 Standard 1080p",
        })

    def test_reviewed_seed_snapshot_has_expected_coverage_and_caveats(self):
        image_grouped = sum(bool(item.get("comparison_group"))
                            for offerings in seed.IMAGE.values() for item in offerings)
        video_grouped = sum(bool(item.get("comparison_group"))
                            for offerings in seed.VIDEO.values() for item in offerings)
        self.assertGreaterEqual(image_grouped, 15)
        self.assertGreaterEqual(video_grouped, 25)
        sora_text = json.dumps(seed.VIDEO["openai"], ensure_ascii=False)
        nova_text = json.dumps(seed.VIDEO["aws-nova"], ensure_ascii=False)
        pika_text = json.dumps(seed.VIDEO["pika"], ensure_ascii=False)
        self.assertIn("2026-09-24", sora_text)
        self.assertIn("2026-09-30", nova_text)
        self.assertIn("$10/月", pika_text)

    def test_every_provider_has_an_official_sample_and_media_is_optional_http(self):
        for kind, catalog in (
                ("imagegen", run.load_imagegen_providers()),
                ("videogen", run.load_videogen_providers())):
            direct_media = 0
            for provider in catalog:
                with self.subTest(kind=kind, provider=provider["id"]):
                    self.assertTrue(provider["examples"])
                    for example in provider["examples"]:
                        self.assertTrue(example["url"].startswith("https://"))
                        if example.get("media_url"):
                            direct_media += 1
                            self.assertTrue(example["media_url"].startswith("https://"))
            self.assertGreaterEqual(direct_media, 3)

    def test_seed_records_retain_all_configured_source_and_sample_urls(self):
        for kind, catalog in (
                ("imagegen", run.load_imagegen_providers()),
                ("videogen", run.load_videogen_providers())):
            data = records(kind)
            for provider in catalog:
                expected_sources = provider.get(f"{kind}_urls") or [provider[f"{kind}_url"]]
                record = data[provider["id"]]
                with self.subTest(kind=kind, provider=provider["id"]):
                    if record.get("source") == "official_seed":
                        self.assertEqual(record["source_urls"], expected_sources)
                    else:
                        self.assertEqual(
                            record.get("configured_source_urls", expected_sources),
                            expected_sources)
                    self.assertEqual(record["official_examples"], provider["examples"])


if __name__ == "__main__":
    unittest.main()
