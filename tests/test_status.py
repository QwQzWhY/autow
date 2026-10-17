"""看板的勾选/删除存在 status.json 里，扫描开跑时并进数据库。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from autow import status
from autow.models import RawNotice
from autow.store import Store


class TestStatusFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.docs = Path(tmp.name)
        os.environ["AUTOW_DOCS_DIR"] = str(self.docs)
        self.addCleanup(os.environ.pop, "AUTOW_DOCS_DIR", None)
        self.addCleanup(lambda: None)

    def write(self, items) -> None:
        (self.docs / "status.json").write_text(
            json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8"
        )

    def test_it_reads_the_map(self):
        self.write({"qq:abc": "deleted", "lzu-ai:1.html": "done"})
        self.assertEqual(
            status.load(), {"qq:abc": "deleted", "lzu-ai:1.html": "done"}
        )

    def test_a_missing_file_reads_as_empty(self):
        self.assertEqual(status.load(), {})

    def test_junk_values_are_ignored(self):
        # 文件是公开可编辑的，写坏了也只丢那一条，不能让整轮扫描失败。
        self.write({"a": "删掉它", "b": "done"})
        self.assertEqual(status.load(), {"b": "done"})

    def test_a_broken_file_reads_as_empty(self):
        (self.docs / "status.json").write_text("{不是 JSON", encoding="utf-8")
        self.assertEqual(status.load(), {})

    def test_it_applies_to_the_database(self):
        db = self.docs / "autow.db"
        store = Store(db)
        self.addCleanup(store.close)
        notice_id, _ = store.upsert_notice(
            "lzu-ai", RawNotice(url="https://ai.lzu.edu.cn/x1", title="通知", title_full="通知")
        )
        store.add_todo(
            notice_id=notice_id,
            title="交材料",
            detail="",
            due_at=None,
            priority="normal",
            dedupe_key="lzu-ai:x1",
        )
        self.write({"lzu-ai:x1": "deleted"})

        self.assertEqual(status.apply_to(store), 1)
        self.assertEqual(store.open_todos(), [])


if __name__ == "__main__":
    unittest.main()
