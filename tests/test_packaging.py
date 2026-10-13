"""Published artifacts must carry a version the user can see.

The phone bridge was rebuilt four times in one evening, each build overwriting
the same download file while versionCode stayed at 1. The user had no way to
tell which build was installed and no way back to an older one.
"""

import hashlib
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADLE = ROOT / "android" / "app" / "build.gradle.kts"
DOCS = ROOT / "docs"


def gradle_text() -> str:
    return GRADLE.read_text(encoding="utf-8")


def version_name() -> str:
    match = re.search(r'versionName\s*=\s*"([^"]+)"', gradle_text())
    assert match, "build.gradle.kts 里没有 versionName"
    return match.group(1)


def version_code() -> int:
    match = re.search(r"versionCode\s*=\s*(\d+)", gradle_text())
    assert match, "build.gradle.kts 里没有 versionCode"
    return int(match.group(1))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestPackaging(unittest.TestCase):
    def test_the_version_has_moved_off_the_placeholder(self):
        # Guards against "rebuilt it and forgot to bump anything".
        self.assertGreater(version_code(), 1)
        self.assertNotEqual(version_name(), "1.0")

    def test_the_versioned_apk_is_published(self):
        path = DOCS / f"autow-bridge-{version_name()}.apk"
        self.assertTrue(path.exists(), f"缺少 {path.name}，新版必须用带版本号的文件名发布")

    def test_the_stable_download_is_the_same_build(self):
        versioned = DOCS / f"autow-bridge-{version_name()}.apk"
        latest = DOCS / "autow-bridge.apk"
        self.assertTrue(latest.exists(), "缺少 autow-bridge.apk")
        self.assertEqual(
            digest(latest), digest(versioned), "两份文件名不同但不是同一个文件"
        )

    def test_the_download_page_links_the_current_version(self):
        page = (DOCS / "qq.html").read_text(encoding="utf-8")
        self.assertIn(
            f"autow-bridge-{version_name()}.apk",
            page,
            "qq.html 还在指向旧版本，改了版本号就要同步改下载链接",
        )

    def test_the_versioned_apk_is_actually_published_by_git(self):
        # .gitignore has a blanket `*.apk`. The first version of this change only
        # un-ignored the stable filename, so the versioned copy was silently left
        # out of the commit and never reached the download page.
        path = DOCS / f"autow-bridge-{version_name()}.apk"
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "check-ignore", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(
            result.returncode, 0, f"{path.name} 被 .gitignore 忽略了，不会随仓库发布"
        )


if __name__ == "__main__":
    unittest.main()
