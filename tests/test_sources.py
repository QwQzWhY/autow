"""Tests for the per-site adapters that do not need a browser."""

import unittest

from autow.sources import SOURCES, lzu_news


class TestLzuNews(unittest.TestCase):
    def test_relative_links_resolve_against_the_list_page(self):
        notice = lzu_news.to_notice(
            {"title": "关于某事的通知", "href": "info/1056/1177735.htm", "date": "2026-10-12"}
        )
        self.assertEqual(notice.url, "https://news.lzu.edu.cn/info/1056/1177735.htm")
        self.assertEqual(notice.external_id, "1177735.htm")
        self.assertEqual(notice.published_at, "2026-10-12")

    def test_offsite_links_are_kept_absolute(self):
        # The board mixes in links to other campus hosts.
        notice = lzu_news.to_notice(
            {
                "title": "关于举办国考训练营的通知",
                "href": "https://job.lzu.edu.cn/html/68/article/2026/93192.html",
                "date": "2026-10-12",
            }
        )
        self.assertEqual(notice.url, "https://job.lzu.edu.cn/html/68/article/2026/93192.html")

    def test_rows_without_a_title_or_link_are_dropped(self):
        self.assertIsNone(lzu_news.to_notice({"title": "", "href": "a.htm"}))
        self.assertIsNone(lzu_news.to_notice({"title": "标题", "href": ""}))

    def test_a_date_is_optional(self):
        notice = lzu_news.to_notice({"title": "标题", "href": "info/1/2.htm"})
        self.assertIsNone(notice.published_at)

    def test_there_is_only_one_page(self):
        self.assertEqual(lzu_news.fetch_list(None, page_no=2), [])


class TestSourceRegistry(unittest.TestCase):
    def test_every_source_exposes_the_same_interface(self):
        for source in SOURCES:
            self.assertTrue(source.NAME, source)
            self.assertTrue(callable(source.fetch_list), source)
            self.assertTrue(callable(source.fetch_detail), source)

    def test_source_names_are_unique(self):
        names = [s.NAME for s in SOURCES]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
