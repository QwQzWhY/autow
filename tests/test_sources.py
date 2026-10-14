"""Tests for the per-site adapters that do not need a browser."""

import unittest

from autow.sources import SOURCES, lzu_jwc, lzu_news, lzu_xgb


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

    def test_the_two_new_campus_sources_are_registered(self):
        names = {source.NAME for source in SOURCES}
        self.assertIn("lzu-jwc", names, "教务处的通知没人抓")
        self.assertIn("lzu-xgb", names, "学工部的通知没人抓")

    def test_every_source_has_a_unique_name(self):
        names = [source.NAME for source in SOURCES]
        self.assertEqual(len(names), len(set(names)))


class TestXgb(unittest.TestCase):
    """学工部的列表来自 JSON，不在 HTML 里。"""

    ROW = {
        "articleId": 338466,
        "articleTitle": "兰州大学第九届感恩中国近现代科学家奖助学金笔试通知",
        "articleUrl": "/tongzhigonggao/zizhufuwu/2026/1013/338466.html",
        "publishDate": "2026-10-13",
    }

    def test_a_feed_row_becomes_a_notice(self):
        notice = lzu_xgb.to_notice(self.ROW, "资助通知")
        self.assertEqual(notice.title, self.ROW["articleTitle"])
        self.assertEqual(
            notice.url, "http://xgb.lzu.edu.cn/tongzhigonggao/zizhufuwu/2026/1013/338466.html"
        )
        self.assertEqual(notice.published_at, "2026-10-13")
        self.assertEqual(notice.category, "资助通知")

    def test_the_id_comes_from_the_url(self):
        self.assertEqual(lzu_xgb.to_notice(self.ROW, "资助通知").external_id, "338466.html")

    def test_a_row_without_a_title_or_link_is_dropped(self):
        self.assertIsNone(lzu_xgb.to_notice({**self.ROW, "articleTitle": "  "}, "资助通知"))
        self.assertIsNone(lzu_xgb.to_notice({**self.ROW, "articleUrl": ""}, "资助通知"))

    def test_a_legacy_field_name_still_works(self):
        # 列表模板用 aurl 取值，字段名两边都出现过。
        row = {**self.ROW, "articleUrl": "", "aurl": self.ROW["articleUrl"]}
        self.assertIsNotNone(lzu_xgb.to_notice(row, "思政通知"))

    def test_the_category_falls_back_to_the_channel(self):
        notice = lzu_xgb.to_notice({**self.ROW, "channelName": "学生资助"}, "")
        self.assertEqual(notice.category, "学生资助")

    def test_the_columns_we_watch_are_the_ones_with_student_content(self):
        self.assertIn("资助通知", lzu_xgb.COLUMNS)
        self.assertIn("国防教育", lzu_xgb.COLUMNS)
        # 栏目的 id 是从栏目页上的 /xgbnew/lm/<id>.js 抄来的，不能是猜的。
        for column_id in lzu_xgb.COLUMNS.values():
            self.assertIsInstance(column_id, int)
            self.assertIn(str(column_id), lzu_xgb.feed_url(column_id))


class TestJwc(unittest.TestCase):
    """教务处首页把各栏目各列几条，但不是所有 ul.list 里的链接都是通知。"""

    ROW = {
        "title": "关于召开本科教学工作例会的通知",
        "href": "/tongzhigonggao/2026/0922/337606.html",
        "date": "2026-09-22",
        "category": "综合公告",
    }

    def test_a_list_row_becomes_a_notice(self):
        notice = lzu_jwc.to_notice(self.ROW)
        self.assertEqual(notice.title, self.ROW["title"])
        self.assertEqual(notice.url, "https://jwc.lzu.edu.cn/tongzhigonggao/2026/0922/337606.html")
        self.assertEqual(notice.published_at, "2026-09-22")
        self.assertEqual(notice.category, "综合公告")

    def test_a_navigation_link_is_not_a_notice(self):
        # 导航和栏目入口挂在同一个 list 类下面，不能当成通知。
        for href in (
            "/tongzhigonggao/zonghegonggao/index.html",
            "/jigoushezhi/index.html",
            "https://shpg.lzu.edu.cn",
            "/tongzhigonggao/index.html",
        ):
            with self.subTest(href):
                self.assertIsNone(lzu_jwc.to_notice({**self.ROW, "href": href}))

    def test_a_row_without_a_title_is_dropped(self):
        self.assertIsNone(lzu_jwc.to_notice({**self.ROW, "title": " "}))

    def test_a_row_without_a_date_still_works(self):
        notice = lzu_jwc.to_notice({**self.ROW, "date": ""})
        self.assertIsNotNone(notice)
        self.assertIsNone(notice.published_at)

    def test_source_names_are_unique(self):
        names = [s.NAME for s in SOURCES]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
