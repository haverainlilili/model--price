import fcntl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scraper import run


class GenerationServiceRegionTests(unittest.TestCase):
    def test_per_offering_identity_distinguishes_swapped_regions(self):
        base = {"name": "A", "variant_key": "a", "currency": "USD"}
        self.assertNotEqual(
            run._generation_offering_identity({**base, "region": "intl"}),
            run._generation_offering_identity({**base, "region": "domestic"}),
        )

    def test_missing_region_and_currency_persists_both_service_regions(self):
        self.assertEqual(
            run._generation_service_regions([{"name": "region-neutral"}]),
            ["domestic", "intl"],
        )


class FetchNewsTextTests(unittest.TestCase):
    @patch.object(run, "fetch_rendered", return_value="rendered news")
    @patch.object(run, "fetch", side_effect=AssertionError("plain fetch must not run"))
    def test_uses_browser_for_rendered_news_sources(self, _fetch, _fetch_rendered):
        text = run._fetch_news_text({
            "news_url": "https://example.com/news",
            "news_render": True,
        })

        self.assertEqual(text, "rendered news")
        _fetch_rendered.assert_called_once_with(
            "https://example.com/news", preserve_links=True,
            language="zh-CN")

    @patch.object(run, "fetch", return_value="plain news")
    def test_uses_plain_fetch_by_default(self, _fetch):
        text = run._fetch_news_text({"news_url": "https://example.com/news"})

        self.assertEqual(text, "plain news")
        _fetch.assert_called_once_with(
            "https://example.com/news", language="zh-CN")


class FetchPlanTextTests(unittest.TestCase):
    @patch.object(run, "fetch_rendered", return_value="rendered plan")
    @patch.object(run, "fetch", side_effect=AssertionError("plain fetch must not run"))
    def test_uses_browser_for_rendered_plan_sources(self, _fetch, _fetch_rendered):
        url, text = run._fetch_plan_text({
            "plan_url": "https://example.com/plans",
            "plan_render": True,
        })

        self.assertEqual(url, "https://example.com/plans")
        self.assertIn("rendered plan", text)
        _fetch_rendered.assert_called_once_with(
            "https://example.com/plans", language="zh-CN")

    @patch.object(run, "fetch", side_effect=["price page", "quota page"])
    def test_combines_multiple_official_plan_sources(self, _fetch):
        url, text = run._fetch_plan_text({
            "plan_urls": [
                "https://example.com/price",
                "https://example.com/quota",
            ],
        })

        self.assertEqual(url, "https://example.com/price")
        self.assertIn("price page", text)
        self.assertIn("quota page", text)
        self.assertEqual(
            _fetch.call_args_list,
            [
                unittest.mock.call(
                    "https://example.com/price", language="zh-CN"),
                unittest.mock.call(
                    "https://example.com/quota", language="zh-CN"),
            ],
        )


class FetchWebSearchTextTests(unittest.TestCase):
    @patch.object(run, "fetch", side_effect=["feature page", "pricing page"])
    def test_combines_multiple_official_websearch_sources(self, _fetch):
        url, text = run._fetch_websearch_text({
            "websearch_urls": [
                "https://example.com/search",
                "https://example.com/pricing",
            ],
            "region": "国际",
        })

        self.assertEqual(url, "https://example.com/search")
        self.assertIn("feature page", text)
        self.assertIn("pricing page", text)
        self.assertEqual(_fetch.call_count, 2)

    @patch.object(run, "fetch_rendered", return_value="rendered search")
    @patch.object(run, "fetch", side_effect=AssertionError("plain fetch must not run"))
    def test_uses_browser_for_rendered_websearch_source(self, _fetch, rendered):
        url, text = run._fetch_websearch_text({
            "websearch_url": "https://example.com/search",
            "websearch_render": True,
        })

        self.assertEqual(url, "https://example.com/search")
        self.assertIn("rendered search", text)
        rendered.assert_called_once_with(
            "https://example.com/search", language="zh-CN")


class FetchGenerationTextTests(unittest.TestCase):
    @patch.object(run, "fetch", side_effect=["model specs", "pricing facts"])
    def test_combines_multiple_image_generation_sources(self, fetch):
        url, text, warnings, full_hash = run._fetch_imagegen_text({
            "imagegen_urls": [
                "https://example.com/image-model",
                "https://example.com/image-pricing",
            ],
            "region": "国际",
        })

        self.assertEqual(url, "https://example.com/image-model")
        self.assertIn("model specs", text)
        self.assertIn("pricing facts", text)
        self.assertEqual(warnings, [])
        self.assertEqual(fetch.call_count, 2)
        pricing_call = next(call for call in fetch.call_args_list
                            if call.args[0] == "https://example.com/image-pricing")
        self.assertEqual(pricing_call.kwargs["language"], "en-US")
        self.assertEqual(pricing_call.kwargs["max_chars"], run.GENERATION_FETCH_MAX_CHARS)
        self.assertTrue(callable(pricing_call.kwargs["final_url_validator"]))

    @patch.object(run, "fetch_rendered", return_value="rendered video facts")
    @patch.object(run, "fetch", side_effect=AssertionError("plain fetch must not run"))
    def test_uses_rendered_fetch_with_links_when_requested(self, _fetch, rendered):
        url, text, warnings, full_hash = run._fetch_videogen_text({
            "videogen_url": "https://example.cn/video",
            "videogen_render": True,
            "region": "国内",
        })

        self.assertEqual(url, "https://example.cn/video")
        self.assertIn("rendered video facts", text)
        self.assertEqual(warnings, [])
        call = rendered.call_args
        self.assertEqual(call.args[0], "https://example.cn/video")
        self.assertTrue(call.kwargs["preserve_links"])
        self.assertEqual(call.kwargs["language"], "zh-CN")
        self.assertEqual(call.kwargs["max_chars"], run.GENERATION_FETCH_MAX_CHARS)
        self.assertTrue(callable(call.kwargs["final_url_validator"]))


    @patch.object(run, "fetch", return_value=(
        "HEAD" + ("x" * 480_000) +
        "\nPricing evidence: $0.07 per second, 720p, 24fps\n" +
        ("y" * 480_000) + "TAIL"))
    def test_long_source_keeps_pricing_marker_from_middle(self, _fetch):
        _url, text, warnings, full_hash = run._fetch_videogen_text({
            "videogen_url": "https://example.com/pricing", "region": "国际",
        })
        self.assertIn("$0.07 per second, 720p, 24fps", text)
        self.assertIn("HEAD", text)
        self.assertIn("TAIL", text)
        self.assertEqual(warnings, [])

    @patch.object(run, "fetch")
    def test_complete_hash_changes_for_middle_lifecycle_fact_outside_excerpt(self, fetch):
        prefix = "x" * 300_000
        suffix = "y" * 300_000
        first = prefix + "ordinary prose" + suffix
        second = prefix + "EOL-2026-09-30-ENDED" + suffix
        fetch.side_effect = [first, second]
        cfg = {"imagegen_url": "https://official.example/pricing",
               "official_domains": ["official.example"], "region": "国际"}
        _, excerpt_one, _, hash_one = run._fetch_imagegen_text(cfg)
        _, excerpt_two, _, hash_two = run._fetch_imagegen_text(cfg)
        self.assertNotEqual(hash_one, hash_two)
        self.assertIn("EOL-2026-09-30-ENDED", excerpt_two)

    def test_dense_price_keywords_cannot_suppress_lifecycle_evidence(self):
        chunks = [("price " + "x" * 1194) for _ in range(850)]
        chunks[251] = chunks[251][:600] + " EOL-2026-09-30 " + chunks[251][616:]
        text = "".join(chunks)
        excerpt = run._generation_source_excerpt(text, 220_000)
        self.assertIn("EOL-2026-09-30", excerpt)

    def test_aggregate_lifecycle_page_keeps_each_model_row(self):
        rows = [f"deprecated MODEL-{i:04d} sunset 2026-09-{(i % 28) + 1:02d} "
                + "x" * 900 for i in range(1000)]
        excerpt = run._generation_source_excerpt("".join(rows), 220_000)
        self.assertNotIn(run.LIFECYCLE_EXCERPT_OVERFLOW, excerpt)
        self.assertIn("MODEL-0499", excerpt)
        self.assertIn("MODEL-0999", excerpt)

    def test_compact_lifecycle_rows_merge_without_dropping_matches(self):
        prefix = "x" * 300_000
        rows = [f"deprecated MODEL-{i:02d} date 2026-09-30".ljust(170, "x")
                for i in range(40)]
        first = prefix + "".join(rows) + "y" * 300_000
        rows[1] = "deprecated MODEL-01 date 2026-10-31".ljust(170, "x")
        second = prefix + "".join(rows) + "y" * 300_000
        excerpt_one = run._generation_source_excerpt(first, 220_000)
        excerpt_two = run._generation_source_excerpt(second, 220_000)
        self.assertNotEqual(excerpt_one, excerpt_two)
        self.assertIn("MODEL-01 date 2026-10-31", excerpt_two)

    def test_lifecycle_overflow_is_explicit_not_silently_sampled(self):
        text = "".join(f"deprecated MODEL-{i:05d} EOL 2026-09-30 " + "x" * 200
                       for i in range(4000))
        self.assertEqual(run._generation_source_excerpt(text, 220_000),
                         run.LIFECYCLE_EXCERPT_OVERFLOW)

    @patch.object(run, "fetch")
    def test_long_first_source_cannot_hide_later_official_pages(self, fetch):
        first = "FIRST_HEAD" + ("x" * 249_980) + "FIRST_TAIL"
        second = "SECOND_SOURCE_DEPRECATION_FACT"
        fetch.side_effect = [first, second]
        _url, text, warnings, full_hash = run._fetch_imagegen_text({
            "imagegen_urls": ["https://example.com/one", "https://example.com/two"],
            "region": "国际",
        })

        self.assertIn("FIRST_HEAD", text)
        self.assertIn("FIRST_TAIL", text)
        self.assertIn("SECOND_SOURCE_DEPRECATION_FACT", text)
        self.assertLess(len(text), 230_000)
        self.assertEqual(warnings, [])


    @patch.object(run, "fetch", side_effect=["primary pricing", run.FetchError("HTTP 403")])
    def test_secondary_source_failure_keeps_primary_and_url_warning(self, _fetch):
        url, text, warnings, full_hash = run._fetch_videogen_text({
            "videogen_urls": ["https://official.example/pricing", "https://official.example/spec"],
            "region": "国际",
        })
        self.assertEqual(url, "https://official.example/pricing")
        self.assertIn("primary pricing", text)
        self.assertEqual(warnings, ["https://official.example/spec: HTTP 403"])


class FetchLanguageSelectionTests(unittest.TestCase):
    @patch.object(run, "fetch", return_value="English pricing")
    def test_international_provider_uses_english(self, _fetch):
        run._fetch_pricing_text({
            "pricing_url": "https://example.com/pricing",
            "region": "国际",
        })

        _fetch.assert_called_once_with(
            "https://example.com/pricing", language="en-US")

    @patch.object(run, "fetch", return_value="中文价格")
    def test_domestic_provider_uses_chinese(self, _fetch):
        run._fetch_pricing_text({
            "pricing_url": "https://example.cn/pricing",
            "region": "国内",
        })

        _fetch.assert_called_once_with(
            "https://example.cn/pricing", language="zh-CN")


class OfficialPlanUrlTests(unittest.TestCase):
    def test_accepts_another_official_subdomain(self):
        self.assertEqual(
            run._official_plan_url(
                {
                    "plan_url": "https://help.aliyun.com/model-studio/plans",
                },
                "https://common-buy.aliyun.com/official-package",
            ),
            "https://common-buy.aliyun.com/official-package",
        )

    def test_rejects_affiliate_or_community_domains(self):
        self.assertIsNone(
            run._official_plan_url(
                {"plan_url": "https://support.claude.com/plans"},
                "https://affiliate.example/ref/claude",
            )
        )


class NewsFingerprintTests(unittest.TestCase):
    def test_ignores_dynamic_page_noise_and_link_order(self):
        first = (
            "当前时间 10:01\n"
            "[模型 A](/news/model-a)\n"
            "[模型 B](https://example.com/news/model-b)"
        )
        second = (
            "随机推荐内容 999\n"
            "[模型 B](https://example.com/news/model-b)\n"
            "[模型 A](/news/model-a)"
        )

        self.assertEqual(
            run._news_fingerprint("https://example.com/news", first),
            run._news_fingerprint("https://example.com/news", second),
        )

    def test_changes_when_an_announcement_link_changes(self):
        before = "[模型 A](/news/model-a)"
        after = before + "\n[模型 B](/news/model-b)"

        self.assertNotEqual(
            run._news_fingerprint("https://example.com/news", before),
            run._news_fingerprint("https://example.com/news", after),
        )


class ProviderConfigurationTests(unittest.TestCase):
    def test_every_provider_has_crawl_source_and_official_news_homepage(self):
        for provider in run.load_providers():
            with self.subTest(provider=provider["id"]):
                self.assertTrue(provider.get("news_url"))
                self.assertTrue(provider.get("official_news_url"))

    def test_international_providers_explicitly_use_english(self):
        international = [
            provider for provider in run.load_providers()
            if provider.get("region") == "国际"
        ]

        self.assertTrue(international)
        for provider in international:
            with self.subTest(provider=provider["id"]):
                self.assertEqual(provider.get("language"), "en-US")

    def test_websearch_catalog_is_large_unique_and_separate(self):
        catalog = run.load_websearch_providers()
        ids = [provider["id"] for provider in catalog]

        self.assertGreaterEqual(len(catalog), 20)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({"model", "ai-search", "serp"}.issubset(
            {provider.get("category") for provider in catalog}))
        self.assertIn("tavily", ids)
        self.assertIn("serper", ids)
        self.assertIn("brightdata", ids)
        for provider in catalog:
            with self.subTest(provider=provider["id"]):
                self.assertTrue(
                    provider.get("websearch_url") or provider.get("websearch_urls"))

    def test_media_generation_catalogs_are_large_unique_and_officially_sourced(self):
        for name, catalog, key, minimum in (
                ("image", run.load_imagegen_providers(), "imagegen", 15),
                ("video", run.load_videogen_providers(), "videogen", 15)):
            with self.subTest(catalog=name):
                ids = [provider["id"] for provider in catalog]
                self.assertGreaterEqual(len(catalog), minimum)
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(provider.get("examples") for provider in catalog))
                self.assertTrue(all(
                    provider.get(f"{key}_url") or provider.get(f"{key}_urls")
                    for provider in catalog))
                self.assertTrue(all(
                    example.get("url", "").startswith("https://")
                    for provider in catalog for example in provider["examples"]))

    def test_official_domain_policy_rejects_third_party_and_github_org_spoof(self):
        base = {
            "id": "demo", "imagegen_url": "https://official.example/pricing",
            "official_domains": ["official.example"],
            "examples": [{"url": "https://third-party.example/gallery"}],
        }
        self.assertEqual(
            run._validate_official_generation_urls(base, "imagegen"),
            ["https://third-party.example/gallery"])
        github = {
            "id": "demo", "imagegen_url": "https://official.example/pricing",
            "official_domains": ["official.example", "raw.githubusercontent.com"],
            "official_url_prefixes": ["https://raw.githubusercontent.com/OfficialOrg/"],
            "examples": [{"url": "https://raw.githubusercontent.com/OtherOrg/repo/main/a.png"}],
        }
        self.assertEqual(len(run._validate_official_generation_urls(github, "imagegen")), 1)

    def test_deepseek_pins_version_aware_pricing_extraction(self):
        deepseek = next(
            provider for provider in run.load_providers()
            if provider["id"] == "deepseek"
        )

        self.assertEqual(deepseek["pricing_extraction_revision"], 2)

    def test_google_sources_pin_the_english_locale(self):
        google = next(
            provider for provider in run.load_providers()
            if provider["id"] == "google"
        )

        self.assertIn("hl=en", google["pricing_url"])
        self.assertIn("hl=en", google["news_url"])


class ProcessProviderTests(unittest.TestCase):
    def test_pricing_revision_forces_version_aware_refresh(self):
        page_hash = run._sha("same page")
        previous = {
            "source": "claude",
            "currency": "CNY",
            "promotions": "已接受的峰谷计价说明",
            "page_has_pricing": True,
            "models": [{"model": "deepseek-flash", "input_per_1m": 1,
                        "output_per_1m": 4, "currency": "CNY",
                        "note": "已接受的价格档位说明"}],
            "price_hash": page_hash,
            "pricing_extraction_revision": 1,
        }
        extracted = {
            "model": "deepseek-flash",
            "display_name": "DeepSeek-V4.1-Flash",
            "input_per_1m": 1,
            "output_per_1m": 4,
            "currency": "CNY",
            "note": "新抽取器的改写不应覆盖已接受说明",
        }
        page = SimpleNamespace(
            models=[SimpleNamespace(model_dump=lambda: extracted)],
            currency="CNY", promotions="新抽取器遗漏或改写的说明",
            page_has_pricing=True,
        )
        cfg = {
            "id": "deepseek", "name": "DeepSeek",
            "pricing_url": "https://official.example/pricing",
            "pricing_extraction_revision": 2,
        }
        with (
            patch.object(run.history, "load_provider", return_value=previous),
            patch.object(run.history, "save_provider") as save,
            patch.object(run.history, "append_changes") as changes,
            patch.object(run, "_fetch_pricing_text", return_value=(
                cfg["pricing_url"], "same page")),
            patch.object(run.extract, "has_api_key", return_value=True),
            patch.object(run.extract, "extract_pricing", return_value=page) as extract,
        ):
            run.process_provider(cfg)

        extract.assert_called_once()
        changes.assert_not_called()
        saved = save.call_args.args[1]
        self.assertEqual(saved["pricing_extraction_revision"], 2)
        self.assertEqual(saved["models"][0]["display_name"], "DeepSeek-V4.1-Flash")
        self.assertEqual(saved["models"][0]["note"], "已接受的价格档位说明")
        self.assertEqual(saved["promotions"], "已接受的峰谷计价说明")

    def test_revision_refresh_mismatch_preserves_facts_and_stays_pending(self):
        page_hash = run._sha("same page")
        previous = {
            "source": "claude",
            "models": [{"model": "deepseek-flash", "input_per_1m": 1,
                        "output_per_1m": 4, "currency": "CNY",
                        "note": "已接受说明"}],
            "price_hash": page_hash,
            "pricing_extraction_revision": 1,
        }
        unsafe = {
            "model": "deepseek-flash",
            "display_name": "DeepSeek-V4.1-Flash",
            "input_per_1m": 999,
            "output_per_1m": 4,
            "currency": "CNY",
            "note": "被改写说明",
        }
        page = SimpleNamespace(
            models=[SimpleNamespace(model_dump=lambda: unsafe)],
            currency="CNY", promotions=None, page_has_pricing=True,
        )
        cfg = {"id": "deepseek", "name": "DeepSeek",
               "pricing_url": "https://official.example/pricing",
               "pricing_extraction_revision": 2}
        with (
            patch.object(run.history, "load_provider", return_value=previous),
            patch.object(run.history, "save_provider") as save,
            patch.object(run.history, "append_changes") as changes,
            patch.object(run, "_fetch_pricing_text", return_value=(
                cfg["pricing_url"], "same page")),
            patch.object(run.extract, "has_api_key", return_value=True),
            patch.object(run.extract, "extract_pricing", return_value=page),
        ):
            run.process_provider(cfg)

        saved = save.call_args.args[1]
        self.assertEqual(saved["models"], previous["models"])
        self.assertEqual(saved["pricing_extraction_revision"], 1)
        self.assertIn("新版抽取", saved["last_error"])
        changes.assert_not_called()

    def test_matching_pricing_revision_still_skips_unchanged_page(self):
        previous = {
            "models": [{"model": "deepseek-flash"}],
            "price_hash": run._sha("same page"),
            "pricing_extraction_revision": 2,
        }
        cfg = {"id": "deepseek", "name": "DeepSeek",
               "pricing_url": "https://official.example/pricing",
               "pricing_extraction_revision": 2}
        with (
            patch.object(run.history, "load_provider", return_value=previous),
            patch.object(run.history, "save_provider") as save,
            patch.object(run, "_fetch_pricing_text", return_value=(
                cfg["pricing_url"], "same page")),
            patch.object(run.extract, "extract_pricing") as extract,
        ):
            run.process_provider(cfg)

        extract.assert_not_called()
        self.assertEqual(save.call_args.args[1]["status_note"], "页面无变化")

    @patch.object(run.history, "append_changes")
    @patch.object(run.history, "save_provider")
    @patch.object(run.extract, "extract_pricing")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_pricing_text",
                  return_value=("https://example.com/pricing", "new page"))
    @patch.object(run.history, "load_provider")
    def test_empty_extraction_preserves_previous_models_and_records_hash(
            self, load_provider, _fetch_pricing_text, _has_api_key,
            extract_pricing, save_provider, append_changes):
        previous_models = [{
            "model": "Example Pro",
            "input_per_1m": 3,
            "output_per_1m": 15,
        }]
        load_provider.return_value = {
            "source": "claude",
            "models": previous_models,
            "price_hash": "old-hash",
        }
        extract_pricing.return_value = SimpleNamespace(
            models=[],
            currency="USD",
            promotions=None,
            page_has_pricing=False,
        )

        run.process_provider({
            "id": "example",
            "name": "Example",
            "pricing_url": "https://example.com/pricing",
        })

        saved = save_provider.call_args.args[1]
        self.assertEqual(saved["models"], previous_models)
        self.assertEqual(saved["price_hash"], run._sha("new page"))
        self.assertIn("保留上次", saved["status_note"])
        self.assertIn("保留上次", saved["last_error"])
        append_changes.assert_not_called()


class ProcessNewsTests(unittest.TestCase):
    @patch.object(run.history, "save_news")
    @patch.object(run.extract, "extract_news")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_news_text", return_value="changed news page")
    @patch.object(run.history, "load_news")
    def test_empty_extraction_preserves_previous_announcements_and_hash(
            self, load_news, _fetch_news_text, _has_api_key,
            extract_news, save_news):
        previous_entries = [{
            "date": "2026-09-01",
            "title": "Existing announcement",
            "url": "https://example.com/news/existing",
            "summary": "Existing summary.",
        }]
        load_news.return_value = {
            "entries": previous_entries,
            "news_hash": "old-hash",
        }
        extract_news.return_value = SimpleNamespace(entries=[])

        run.process_news({
            "id": "example",
            "name": "Example",
            "news_url": "https://example.com/news",
        })

        saved = save_news.call_args.args[1]
        self.assertEqual(saved["entries"], previous_entries)
        self.assertEqual(
            saved["news_hash"],
            run._news_fingerprint(
                "https://example.com/news", "changed news page"),
        )
        self.assertIn("保留上次", saved["status_note"])
        self.assertIn("保留上次", saved["last_error"])

    @patch.object(run.history, "save_news")
    @patch.object(run.extract, "extract_news")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_news_text", return_value="changed news page")
    @patch.object(run.history, "load_news", return_value={"entries": []})
    def test_resolves_relative_entry_url_before_saving(
            self, _load_news, _fetch_news_text, _has_api_key,
            extract_news, save_news):
        extract_news.return_value = SimpleNamespace(entries=[SimpleNamespace(
            model_dump=lambda: {
                "date": "2026-09-01",
                "title": "Demo Model 发布",
                "url": "/news/demo-model",
                "summary": "发布新模型。",
            }
        )])

        run.process_news({
            "id": "demo",
            "name": "Demo",
            "news_url": "https://docs.example.com/changelog/index.html",
        })

        saved = save_news.call_args.args[1]
        self.assertEqual(
            saved["entries"][0]["url"],
            "https://docs.example.com/news/demo-model",
        )
        self.assertEqual(
            saved["news_hash"],
            run._news_fingerprint(
                "https://docs.example.com/changelog/index.html",
                "changed news page",
            ),
        )


class ProcessPlansTests(unittest.TestCase):
    @patch.object(run.history, "save_plans")
    @patch.object(run.extract, "extract_plans")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_plan_text",
                  return_value=("https://example.com/plans", "changed plans"))
    @patch.object(run.history, "load_plans")
    def test_empty_extraction_preserves_previous_official_plans_and_hash(
            self, load_plans, _fetch_plan_text, _has_api_key,
            extract_plans, save_plans):
        previous = [{
            "name": "Plus",
            "price": "¥49 / 月",
            "quotas": [{"label": "额度", "value": "1,500 次"}],
        }]
        load_plans.return_value = {
            "source": "official",
            "plans": previous,
            "plans_hash": "old-hash",
        }
        extract_plans.return_value = SimpleNamespace(
            plans=[],
            page_has_plans=False,
        )

        run.process_plans({
            "id": "example",
            "name": "Example",
            "plan_url": "https://example.com/plans",
        })

        saved = save_plans.call_args.args[1]
        self.assertEqual(saved["plans"], previous)
        self.assertEqual(saved["plans_hash"], run._sha("changed plans"))
        self.assertIn("保留上次", saved["status_note"])

    @patch.object(run.history, "save_plans")
    @patch.object(run.extract, "extract_plans")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_plan_text",
                  return_value=("https://example.com/plans", "same plans"))
    @patch.object(run.history, "load_plans")
    def test_unchanged_page_skips_plan_extraction(
            self, load_plans, _fetch_plan_text, _has_api_key,
            extract_plans, save_plans):
        load_plans.return_value = {
            "plans": [{"name": "Plus"}],
            "plans_hash": run._sha("same plans"),
        }

        run.process_plans({
            "id": "example",
            "name": "Example",
            "plan_url": "https://example.com/plans",
        })

        extract_plans.assert_not_called()
        self.assertEqual(save_plans.call_args.args[1]["status_note"], "页面无变化")


class ProcessWebSearchTests(unittest.TestCase):
    @patch.object(run.history, "save_websearch")
    @patch.object(run.extract, "extract_websearch")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_websearch_text",
                  return_value=("https://example.com/search", "same search"))
    @patch.object(run.history, "load_websearch")
    def test_retries_previous_failed_extraction_even_when_hash_is_unchanged(
            self, load_websearch, _fetch, _has_key,
            extract_websearch, _save_websearch):
        load_websearch.return_value = {
            "offerings": [{"name": "seed"}],
            "websearch_hash": run._sha("same search"),
            "last_error": "previous extraction was empty",
        }
        extract_websearch.return_value = SimpleNamespace(
            has_search=True,
            offerings=[SimpleNamespace(model_dump=lambda: {"name": "Search API"})],
        )

        run.process_websearch({
            "id": "example",
            "name": "Example",
            "websearch_url": "https://example.com/search",
        })

        extract_websearch.assert_called_once()

    @patch.object(run.history, "save_websearch")
    @patch.object(run.extract, "extract_websearch")
    @patch.object(run.extract, "has_api_key", return_value=True)
    @patch.object(run, "_fetch_websearch_text",
                  return_value=("https://example.com/search", "changed search"))
    @patch.object(run.history, "load_websearch", return_value={})
    def test_saves_comparable_official_search_price(
            self, _load, _fetch, _has_key, extract_websearch, save_websearch):
        extracted = {
            "name": "Search API",
            "pricing": "$5 / 1k requests",
            "price_per_1k_usd": 5,
            "price_basis": "PAYG",
            "free_quota": "1000/month",
            "output_type": "structured results",
            "cites_sources": True,
            "default_on": False,
            "note": None,
        }
        extract_websearch.return_value = SimpleNamespace(
            has_search=True,
            offerings=[SimpleNamespace(model_dump=lambda: extracted)],
        )

        run.process_websearch({
            "id": "example",
            "name": "Example",
            "websearch_urls": [
                "https://example.com/search",
                "https://example.com/pricing",
            ],
        })

        saved = save_websearch.call_args.args[1]
        self.assertEqual(saved["source_url"], "https://example.com/search")
        self.assertEqual(len(saved["source_urls"]), 2)
        self.assertEqual(saved["offerings"][0]["price_per_1k_usd"], 5)


class SingleWriterTests(unittest.TestCase):
    def test_main_skips_before_loading_when_another_writer_holds_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            lock_path = data_dir / ".scraper.lock"
            with lock_path.open("a+") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(run.history, "DATA", data_dir),                         patch.object(run, "load_providers") as load:
                    self.assertEqual(run.main(["--build-only"]), 75)
                load.assert_not_called()


class MediaExtractionBudgetTests(unittest.TestCase):
    def test_budget_accepts_zero_and_falls_back_on_invalid_value(self):
        with patch.dict(run.os.environ, {"MEDIA_EXTRACT_BUDGET": "0"}):
            self.assertEqual(run._media_extract_budget(), 0)
        with patch.dict(run.os.environ, {"MEDIA_EXTRACT_BUDGET": "invalid"}):
            self.assertEqual(run._media_extract_budget(), 5)

    def test_catalog_rotation_changes_start_and_preserves_every_provider(self):
        catalog = [{"id": str(i)} for i in range(4)]
        first = run._rotate_generation_catalog(catalog, "imagegen", hour_number=0)
        second = run._rotate_generation_catalog(catalog, "imagegen", hour_number=1)
        video = run._rotate_generation_catalog(catalog, "videogen", hour_number=0)

        self.assertEqual([item["id"] for item in first], ["0", "1", "2", "3"])
        self.assertEqual([item["id"] for item in second], ["1", "2", "3", "0"])
        self.assertEqual([item["id"] for item in video], ["2", "3", "0", "1"])
        self.assertEqual({item["id"] for item in video}, {"0", "1", "2", "3"})


class ProcessGenerationTests(unittest.TestCase):
    def test_changed_full_source_with_identical_excerpt_is_not_accepted(self):
        excerpt = "same bounded excerpt"
        previous = {
            "source": "official", "has_image_generation": True,
            "product_status": "active", "offerings": [{"name": "Existing"}],
            "imagegen_hash": run._sha("old complete source"),
            "imagegen_excerpt_hash": run._sha(excerpt),
        }
        cfg = {"id": "example", "name": "Example",
               "imagegen_url": "https://official.example/pricing"}
        with patch.object(run.history, "load_imagegen", return_value=previous),                 patch.object(run.history, "save_imagegen") as save,                 patch.object(run, "_fetch_imagegen_text", return_value=(
                    cfg["imagegen_url"], excerpt, [], run._sha("new complete source"))),                 patch.object(run.extract, "extract_imagegen") as extract:
            self.assertTrue(run.process_imagegen(cfg))
        extract.assert_not_called()
        saved = save.call_args.args[1]
        self.assertEqual(saved["imagegen_hash"], previous["imagegen_hash"])
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertIn("安全摘录无变化", saved["last_error"])

    def test_catalog_targets_refresh_without_relabeling_old_fact_provenance(self):
        previous = {"source": "official", "source_url": "https://old.example/",
                    "source_urls": ["https://old.example/"],
                    "offerings": [{"name": "Existing"}]}
        configured = ["https://official.example/pricing", "https://official.example/spec"]
        with patch.object(run.history, "load_imagegen", return_value=previous),                 patch.object(run.history, "save_imagegen") as save,                 patch.object(run, "_fetch_imagegen_text",
                             side_effect=run.FetchError("temporary")):
            self.assertFalse(run.process_imagegen({
                "id": "example", "name": "Example", "imagegen_urls": configured,
                "examples": [{"title": "Current", "url": "https://official.example/demo"}],
            }))
        saved = save.call_args.args[1]
        self.assertEqual(saved["source_url"], "https://old.example/")
        self.assertEqual(saved["source_urls"], ["https://old.example/"])
        self.assertEqual(saved["configured_source_urls"], configured)
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertEqual(saved["official_examples"][0]["title"], "Current")

    def test_successful_image_extraction_replaces_seed_and_preserves_examples(self):
        offering = {
            "name": "Current Image", "api_available": True,
            "modes": ["text-to-image"], "pricing": "$0.04/image",
            "currency": "USD", "price_per_image": .04,
            "comparison_group": "usd-standard-1mp",
            "price_basis": "1K default",
        }
        page = SimpleNamespace(
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: offering)],
        )
        seed_metadata = {
            "source": "official_seed", "seed_verified": True,
            "seed_cutoff": "snapshot", "verified_at": "2026-09-08",
            "verification_hash": "abc", "seeded_at": "2026-09-08T00:00:00Z",
        }
        with patch.object(run.history, "load_imagegen", return_value=seed_metadata), \
                patch.object(run.history, "save_imagegen") as save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://example.com/image", "changed image page")), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", return_value=page):
            invoked = run.process_imagegen({
                "id": "example", "name": "Example",
                "imagegen_url": "https://example.com/image",
                "examples": [{"title": "Official", "url": "https://example.com/gallery"}],
            })

        self.assertTrue(invoked)
        saved = save.call_args.args[1]
        self.assertEqual(saved["source"], "official")
        self.assertTrue(saved["has_image_generation"])
        self.assertEqual(saved["offerings"], [offering])
        self.assertEqual(saved["imagegen_hash"], run._sha("changed image page"))
        self.assertEqual(saved["official_examples"][0]["title"], "Official")
        for field in ("seed_verified", "seed_cutoff", "verified_at",
                      "verification_hash", "seeded_at"):
            self.assertNotIn(field, saved)

    def test_unchanged_generation_page_costs_no_extraction_budget(self):
        previous = {
            "offerings": [{"name": "Existing"}],
            "videogen_hash": run._sha("same video page"),
            "last_error": None,
        }
        with patch.object(run.history, "load_videogen", return_value=previous), \
                patch.object(run.history, "save_videogen") as save, \
                patch.object(run, "_fetch_videogen_text", return_value=(
                    "https://example.com/video", "same video page")), \
                patch.object(run.extract, "has_api_key") as has_key, \
                patch.object(run.extract, "extract_videogen") as extract_video:
            invoked = run.process_videogen({
                "id": "example", "name": "Example",
                "videogen_url": "https://example.com/video",
            })

        self.assertFalse(invoked)
        has_key.assert_not_called()
        extract_video.assert_not_called()
        self.assertEqual(save.call_args.args[1]["status_note"], "页面无变化")

    def test_empty_video_extraction_preserves_previous_offerings_and_retries(self):
        previous_offerings = [{"name": "Seed", "api_available": True}]
        previous = {
            "offerings": previous_offerings,
            "videogen_hash": "old",
        }
        page = SimpleNamespace(has_video_generation=True, offerings=[])
        with patch.object(run.history, "load_videogen", return_value=previous), \
                patch.object(run.history, "save_videogen") as save, \
                patch.object(run, "_fetch_videogen_text", return_value=(
                    "https://example.com/video", "new page")), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_videogen", return_value=page):
            invoked = run.process_videogen({
                "id": "example", "name": "Example",
                "videogen_url": "https://example.com/video",
            })

        self.assertTrue(invoked)
        saved = save.call_args.args[1]
        self.assertEqual(saved["offerings"], previous_offerings)
        self.assertEqual(saved["videogen_hash"], run._sha("new page"))
        self.assertIn("保留上次", saved["last_error"])

    def test_extraction_exception_is_persisted_and_counts_toward_budget(self):
        previous = {"offerings": [{"name": "Official seed"}]}
        with patch.object(run.history, "load_imagegen", return_value=previous), \
                patch.object(run.history, "save_imagegen") as save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://example.com/image", "new page")), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", side_effect=RuntimeError("bad JSON")):
            invoked = run.process_imagegen({
                "id": "example", "name": "Example",
                "imagegen_url": "https://example.com/image",
            })

        self.assertTrue(invoked)
        saved = save.call_args.args[1]
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertIn("抽取失败", saved["last_error"])
        self.assertIn("bad JSON", saved["last_error"])

    def test_missing_key_does_not_clear_retry_error_before_key_returns(self):
        previous = {
            "offerings": [{"name": "Seed", "api_available": True}],
            "imagegen_hash": run._sha("same page"),
            "last_error": "previous empty extraction",
        }
        cfg = {
            "id": "example", "name": "Example",
            "imagegen_url": "https://example.com/image",
        }
        with patch.object(run.history, "load_imagegen", return_value=previous), \
                patch.object(run.history, "save_imagegen") as first_save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://example.com/image", "same page")), \
                patch.object(run.extract, "has_api_key", return_value=False), \
                patch.object(run.extract, "extract_imagegen") as first_extract:
            self.assertFalse(run.process_imagegen(cfg))

        first_extract.assert_not_called()
        retry_record = first_save.call_args.args[1]
        self.assertEqual(retry_record["last_error"], "previous empty extraction")

        page = SimpleNamespace(
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: {
                "name": "Seed", "api_available": True,
            })],
        )
        with patch.object(run.history, "load_imagegen", return_value=retry_record), \
                patch.object(run.history, "save_imagegen") as second_save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://example.com/image", "same page")), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", return_value=page) as second_extract:
            self.assertTrue(run.process_imagegen(cfg))

        # A one-row result is a suspicious shrink from the previous one only if
        # the count is lower; here equal-size recovery succeeds immediately.
        second_extract.assert_called_once()
        self.assertEqual(second_save.call_args.args[1]["source"], "official")
        self.assertIsNone(second_save.call_args.args[1]["last_error"])

    def test_partial_extraction_requires_same_shrink_twice_before_replacing(self):
        old_offerings = [
            {"name": "A"}, {"name": "B"}, {"name": "C"},
        ]
        previous = {"source": "official_seed", "offerings": old_offerings}
        candidate = {"name": "Only A", "api_available": True}
        page = SimpleNamespace(
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: candidate)],
        )
        cfg = {
            "id": "example", "name": "Example",
            "imagegen_url": "https://example.com/image",
        }
        common_fetch = ("https://example.com/image", "changed page")
        with patch.object(run.history, "load_imagegen", return_value=previous), \
                patch.object(run.history, "save_imagegen") as first_save, \
                patch.object(run, "_fetch_imagegen_text", return_value=common_fetch), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))

        pending = first_save.call_args.args[1]
        self.assertEqual(pending["offerings"], old_offerings)
        self.assertIn("等待下轮同语义结果确认", pending["last_error"])
        self.assertTrue(pending["imagegen_pending_shrink_hash"])

        with patch.object(run.history, "load_imagegen", return_value=pending), \
                patch.object(run.history, "save_imagegen") as second_save, \
                patch.object(run, "_fetch_imagegen_text", return_value=common_fetch), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))

        accepted = second_save.call_args.args[1]
        self.assertEqual(accepted["offerings"], [candidate])
        self.assertEqual(accepted["source"], "official")
        self.assertNotIn("imagegen_pending_shrink_hash", accepted)
        self.assertIsNone(accepted["last_error"])

    def test_invalid_shell_preserves_previous_flag_and_offerings(self):
        previous = {
            "has_video_generation": True,
            "offerings": [{"name": "Existing"}],
        }
        page = SimpleNamespace(
            page_has_relevant_content=False,
            has_video_generation=False,
            offerings=[],
        )
        with patch.object(run.history, "load_videogen", return_value=previous), \
                patch.object(run.history, "save_videogen") as save, \
                patch.object(run, "_fetch_videogen_text", return_value=(
                    "https://example.com/video", "captcha")), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen({
                "id": "example", "name": "Example",
                "videogen_url": "https://example.com/video",
            }))

        saved = save.call_args.args[1]
        self.assertTrue(saved["has_video_generation"])
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertIn("页面无效", saved["last_error"])

    def test_healthy_shutdown_requires_two_matching_empty_results(self):
        previous = {
            "has_video_generation": True,
            "offerings": [{"name": "Existing"}],
        }
        page = SimpleNamespace(
            page_has_relevant_content=True,
            has_video_generation=False,
            offerings=[],
        )
        cfg = {
            "id": "example", "name": "Example",
            "videogen_url": "https://example.com/video",
        }
        fetched = ("https://example.com/video", "official shutdown notice")
        with patch.object(run.history, "load_videogen", return_value=previous), \
                patch.object(run.history, "save_videogen") as first_save, \
                patch.object(run, "_fetch_videogen_text", return_value=fetched), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen(cfg))
        pending = first_save.call_args.args[1]
        self.assertTrue(pending["has_video_generation"])
        self.assertEqual(pending["offerings"], previous["offerings"])

        with patch.object(run.history, "load_videogen", return_value=pending), \
                patch.object(run.history, "save_videogen") as second_save, \
                patch.object(run, "_fetch_videogen_text", return_value=fetched), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen(cfg))
        accepted = second_save.call_args.args[1]
        self.assertFalse(accepted["has_video_generation"])
        self.assertEqual(accepted["offerings"], [])
        self.assertIsNone(accepted["last_error"])

    def test_semantic_confirmation_ignores_order_and_note_wording(self):
        first = [
            {"name": "A", "currency": "USD", "price_per_image": .1,
             "resolution": "1024x1024", "note": "wording one"},
            {"name": "B", "currency": "USD", "price_per_image": .2,
             "resolution": "1024x1024", "note": "wording two"},
        ]
        second = [
            {"name": "B", "currency": "USD", "price_per_image": .2,
             "resolution": "1024x1024", "note": "different prose"},
            {"name": "A", "currency": "USD", "price_per_image": .1,
             "resolution": "1024x1024", "note": None},
        ]
        self.assertEqual(
            run._generation_candidate_signature(first, True),
            run._generation_candidate_signature(second, True))

    def test_same_count_identity_loss_is_suspicious(self):
        previous = {"offerings": [{"name": "A"}, {"name": "B"}]}
        page = SimpleNamespace(
            has_image_generation=True,
            offerings=[
                SimpleNamespace(model_dump=lambda: {"name": "A"}),
                SimpleNamespace(model_dump=lambda: {"name": "C"}),
            ],
        )
        with patch.object(run.history, "load_imagegen", return_value=previous), \
                patch.object(run.history, "save_imagegen") as save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://example.com/image", "changed")), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen({
                "id": "example", "name": "Example",
                "imagegen_url": "https://example.com/image",
            }))
        saved = save.call_args.args[1]
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertIn("同语义结果", saved["last_error"])

    def test_discontinued_tombstone_cannot_resurrect_on_partial_sources(self):
        previous = {
            "source": "official", "product_status": "discontinued",
            "product_status_date": "2026-08-01", "has_video_generation": False,
            "offerings": [],
        }
        active = {"name": "Resurrected", "lifecycle_status": "active"}
        page = SimpleNamespace(
            page_has_relevant_content=True, product_status="active",
            product_status_date=None, product_status_note=None,
            has_video_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: active)],
        )
        warning = "https://official.example/eol: HTTP 403"
        cfg = {"id": "example", "name": "Example",
               "videogen_urls": ["https://official.example/pricing",
                                  "https://official.example/eol"]}
        with patch.object(run.history, "load_videogen", return_value=previous),                 patch.object(run.history, "save_videogen") as save,                 patch.object(run, "_fetch_videogen_text", return_value=(
                    cfg["videogen_urls"][0], "partial", [warning])),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen(cfg))
        saved = save.call_args.args[1]
        self.assertFalse(saved["has_video_generation"])
        self.assertEqual(saved["product_status"], "discontinued")
        self.assertEqual(saved["offerings"], [])
        self.assertIn("完整事实", saved["last_error"])

    def test_populated_generation_facts_cannot_disappear_in_one_pass(self):
        previous_offering = {
            "name": "Same", "variant_key": "same", "api_available": True,
            "currency": "USD", "region": "intl", "lifecycle_status": "active",
            "modes": ["text-to-image", "image-edit"], "free_quota": "10/day",
            "note": "rate limit 5/min",
        }
        candidate = {**previous_offering, "modes": ["text-to-image"],
                     "free_quota": None, "note": None}
        previous = {"source": "official", "has_image_generation": True,
                    "product_status": "active", "service_regions": ["intl"],
                    "offerings": [previous_offering]}
        page = SimpleNamespace(
            page_has_relevant_content=True, product_status="active",
            product_status_date=None, product_status_note=None,
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: candidate)],
        )
        cfg = {"id": "example", "name": "Example",
               "imagegen_url": "https://official.example/pricing"}
        with patch.object(run.history, "load_imagegen", return_value=previous),                 patch.object(run.history, "save_imagegen") as save,                 patch.object(run, "_fetch_imagegen_text", return_value=(
                    cfg["imagegen_url"], "sparse complete fetch")),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))
        pending = save.call_args.args[1]
        self.assertEqual(pending["offerings"], [previous_offering])
        self.assertIn("事实变少", pending["last_error"])

    def test_service_region_transition_requires_two_matching_passes(self):
        previous_offering = {
            "name": "Same", "variant_key": "same", "api_available": True,
            "currency": "USD", "region": "intl", "lifecycle_status": "active",
        }
        previous = {"source": "official", "has_image_generation": True,
                    "product_status": "active", "service_regions": ["intl"],
                    "offerings": [previous_offering]}
        domestic = {**previous_offering, "region": "domestic"}
        page = SimpleNamespace(
            page_has_relevant_content=True, product_status="active",
            product_status_date=None, product_status_note=None,
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: domestic)],
        )
        cfg = {"id": "example", "name": "Example",
               "imagegen_url": "https://official.example/pricing"}
        fetched = (cfg["imagegen_url"], "same facts, new endpoint region")
        with patch.object(run.history, "load_imagegen", return_value=previous),                 patch.object(run.history, "save_imagegen") as first_save,                 patch.object(run, "_fetch_imagegen_text", return_value=fetched),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))
        pending = first_save.call_args.args[1]
        self.assertEqual(pending["service_regions"], ["intl"])
        self.assertIn("等待下轮同语义结果确认", pending["last_error"])

        with patch.object(run.history, "load_imagegen", return_value=pending),                 patch.object(run.history, "save_imagegen") as second_save,                 patch.object(run, "_fetch_imagegen_text", return_value=fetched),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))
        accepted = second_save.call_args.args[1]
        self.assertEqual(accepted["service_regions"], ["domestic"])
        self.assertEqual(accepted["offerings"], [domestic])

    def test_product_status_note_cannot_disappear_in_one_pass(self):
        previous = {
            "source": "official", "product_status": "discontinued",
            "product_status_date": "2026-08-01", "product_status_note": "Migrate to V2",
            "has_video_generation": False, "service_regions": ["intl"],
            "offerings": [],
        }
        page = SimpleNamespace(
            page_has_relevant_content=True, product_status="discontinued",
            product_status_date="2026-08-01", product_status_note=None,
            has_video_generation=False, offerings=[],
        )
        cfg = {"id": "example", "name": "Example",
               "videogen_url": "https://official.example/eol"}
        with patch.object(run.history, "load_videogen", return_value=previous),                 patch.object(run.history, "save_videogen") as save,                 patch.object(run, "_fetch_videogen_text", return_value=(
                    cfg["videogen_url"], "same tombstone without note")),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen(cfg))
        pending = save.call_args.args[1]
        self.assertEqual(pending["product_status_note"], "Migrate to V2")
        self.assertIn("说明变为空", pending["last_error"])

    def test_product_level_transition_without_offerings_requires_two_passes(self):
        previous = {
            "source": "official", "product_status": "discontinued",
            "product_status_date": "2026-08-01", "has_video_generation": False,
            "offerings": [],
        }
        active = {"name": "Returned", "lifecycle_status": "active"}
        page = SimpleNamespace(
            page_has_relevant_content=True, product_status="active",
            product_status_date=None, product_status_note=None,
            has_video_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: active)],
        )
        cfg = {"id": "example", "name": "Example",
               "videogen_url": "https://official.example/pricing"}
        fetched = (cfg["videogen_url"], "active again")
        with patch.object(run.history, "load_videogen", return_value=previous),                 patch.object(run.history, "save_videogen") as first_save,                 patch.object(run, "_fetch_videogen_text", return_value=fetched),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen(cfg))
        pending = first_save.call_args.args[1]
        self.assertEqual(pending["product_status"], "discontinued")
        self.assertIn("产品状态", pending["last_error"])

        with patch.object(run.history, "load_videogen", return_value=pending),                 patch.object(run.history, "save_videogen") as second_save,                 patch.object(run, "_fetch_videogen_text", return_value=fetched),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen(cfg))
        accepted = second_save.call_args.args[1]
        self.assertEqual(accepted["product_status"], "active")
        self.assertTrue(accepted["has_video_generation"])
        self.assertEqual(accepted["offerings"], [active])

    def test_same_identity_lifecycle_change_requires_matching_second_pass(self):
        old_offering = {"name": "A", "lifecycle_status": "active", "sunset_at": None}
        new_offering = {"name": "A", "lifecycle_status": "discontinued",
                        "sunset_at": "2026-09-01"}
        previous = {"offerings": [old_offering]}
        page = SimpleNamespace(
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: new_offering)],
        )
        cfg = {"id": "example", "name": "Example",
               "imagegen_url": "https://example.com/image"}
        fetched = ("https://example.com/image", "changed lifecycle")
        with patch.object(run.history, "load_imagegen", return_value=previous),                 patch.object(run.history, "save_imagegen") as first_save,                 patch.object(run, "_fetch_imagegen_text", return_value=fetched),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))
        pending = first_save.call_args.args[1]
        self.assertEqual(pending["offerings"], [old_offering])
        self.assertIn("生命周期", pending["last_error"])

        with patch.object(run.history, "load_imagegen", return_value=pending),                 patch.object(run.history, "save_imagegen") as second_save,                 patch.object(run, "_fetch_imagegen_text", return_value=fetched),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen(cfg))
        self.assertEqual(second_save.call_args.args[1]["offerings"], [new_offering])

    def test_confirmation_signature_includes_lifecycle_facts(self):
        active = [{"name": "A", "lifecycle_status": "active", "sunset_at": None}]
        stopped = [{"name": "A", "lifecycle_status": "discontinued",
                    "sunset_at": "2026-09-01"}]
        self.assertNotEqual(
            run._generation_candidate_signature(active, True),
            run._generation_candidate_signature(stopped, True))

    def test_partial_source_failure_preserves_same_identity_spec_fields(self):
        old = {"name": "A", "modes": ["text-to-video"], "duration": "5s",
               "frame_rate": "24fps", "free_quota": "none"}
        partial = {"name": "A", "modes": [], "duration": None,
                   "frame_rate": None, "free_quota": None}
        previous = {"offerings": [old]}
        page = SimpleNamespace(
            has_video_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: partial)],
        )
        warning = "https://official.example/spec: HTTP 403"
        with patch.object(run.history, "load_videogen", return_value=previous),                 patch.object(run.history, "save_videogen") as save,                 patch.object(run, "_fetch_videogen_text", return_value=(
                    "https://official.example/pricing", "primary only", [warning])),                 patch.object(run.extract, "has_api_key", return_value=True),                 patch.object(run.extract, "extract_videogen", return_value=page):
            self.assertTrue(run.process_videogen({
                "id": "example", "name": "Example",
                "videogen_url": "https://official.example/pricing",
            }))
        saved = save.call_args.args[1]
        self.assertEqual(saved["offerings"], [old])
        self.assertEqual(saved["source_warnings"], [warning])
        self.assertIn("完整事实", saved["last_error"])

    def test_partial_source_failure_cannot_confirm_coverage_loss(self):
        previous = {"offerings": [{"name": "A"}, {"name": "B"}]}
        page = SimpleNamespace(
            has_image_generation=True,
            offerings=[SimpleNamespace(model_dump=lambda: {"name": "A"})],
        )
        warning = "https://official.example/spec: HTTP 403"
        with patch.object(run.history, "load_imagegen", return_value=previous), \
                patch.object(run.history, "save_imagegen") as save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://official.example/pricing", "primary only", [warning])), \
                patch.object(run.extract, "has_api_key", return_value=True), \
                patch.object(run.extract, "extract_imagegen", return_value=page):
            self.assertTrue(run.process_imagegen({
                "id": "example", "name": "Example",
                "imagegen_url": "https://official.example/pricing",
            }))
        saved = save.call_args.args[1]
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertEqual(saved["source_warnings"], [warning])
        self.assertIn("来源恢复后", saved["last_error"])
        self.assertNotIn("imagegen_pending_shrink_hash", saved)

    def test_missing_api_key_keeps_seed_and_does_not_consume_budget(self):
        previous = {"source": "official_seed", "offerings": [{"name": "Seed"}]}
        with patch.object(run.history, "load_imagegen", return_value=previous), \
                patch.object(run.history, "save_imagegen") as save, \
                patch.object(run, "_fetch_imagegen_text", return_value=(
                    "https://example.com/image", "new page")), \
                patch.object(run.extract, "has_api_key", return_value=False), \
                patch.object(run.extract, "extract_imagegen") as extract_image:
            invoked = run.process_imagegen({
                "id": "example", "name": "Example",
                "imagegen_url": "https://example.com/image",
            })

        self.assertFalse(invoked)
        extract_image.assert_not_called()
        saved = save.call_args.args[1]
        self.assertEqual(saved["offerings"], previous["offerings"])
        self.assertEqual(saved["status_note"], "等待 OPENAI_API_KEY")
        self.assertNotIn("imagegen_hash", saved)



if __name__ == "__main__":
    unittest.main()
