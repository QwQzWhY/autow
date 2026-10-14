"""Run the phone bridge's pure Kotlin logic.

The bridge is Kotlin, so most of it can only be exercised on a phone. The two
pieces that are easy to get subtly wrong — how much of a long message survives,
and whether widening a default keyword actually reaches a phone that already
saved the old default — were pulled out of Android's reach into `Transcript.kt`
and `Keywords.kt`, which import nothing from the framework. This compiles just
those two files plus `android/logic-test/BridgeLogicTest.kt` with the Kotlin
compiler and runs the result.

The compiler and the JDK are looked for where they already are on a machine that
has built the APK (the Gradle cache and the local toolchain). When they are
missing this skips loudly instead of failing: a machine without the toolchain is
not a bug, and pretending the check passed would be worse.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "android" / "app" / "src" / "main" / "java" / "cn" / "autow" / "bridge"
TEST_SOURCE = ROOT / "android" / "logic-test" / "BridgeLogicTest.kt"

# Only these two are pure; the rest need Android classes and will not compile
# outside a phone.
PURE_SOURCES = [BRIDGE / "Transcript.kt", BRIDGE / "Keywords.kt"]


def newest(jar_pattern: str) -> Path | None:
    """The highest-versioned match under the Gradle cache, or None."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    cache = Path(home) / ".gradle" / "caches" / "modules-2" / "files-2.1"
    found = sorted(cache.glob(jar_pattern))
    return found[-1] if found else None


def kotlin_compiler() -> Path | None:
    return newest(
        "org.jetbrains.kotlin/kotlin-compiler-embeddable/*/*/kotlin-compiler-embeddable-*.jar"
    )


def kotlin_stdlib() -> Path | None:
    return newest("org.jetbrains.kotlin/kotlin-stdlib/*/*/kotlin-stdlib-*.jar")


def compiler_classpath(compiler: Path, stdlib: Path) -> str:
    """Everything the Kotlin compiler itself needs to start up.

    The embeddable compiler jar carries most of what it needs but not the Kotlin
    standard library or trove, and both are already in the Gradle cache next to
    it — the earlier attempt to launch it with the jar alone died on
    NoClassDefFoundError twice, once per missing jar.
    """
    extras = [
        stdlib,
        newest("org.jetbrains.intellij.deps/trove4j/*/*/trove4j-*.jar"),
        newest("org.jetbrains/annotations/*/*/annotations-*.jar"),
        newest("org.jetbrains.kotlin/kotlin-daemon-embeddable/*/*/kotlin-daemon-embeddable-*.jar"),
        newest("org.jetbrains.kotlin/kotlin-reflect/*/*/kotlin-reflect-*.jar"),
        newest("org.jetbrains.kotlin/kotlin-script-runtime/*/*/kotlin-script-runtime-*.jar"),
    ]
    return os.pathsep.join(str(p) for p in [compiler, *extras] if p)


def java_binary() -> str | None:
    """Whichever java this machine would run the build with."""
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found = sorted((Path(local) / "autow-toolchain" / "jdk").glob("*/bin/java.exe"))
        if found:
            return str(found[0])
    return shutil.which("java")


class TestBridgeLogic(unittest.TestCase):
    def setUp(self):
        self.compiler = kotlin_compiler()
        self.stdlib = kotlin_stdlib()
        self.java = java_binary()
        missing = [
            name
            for name, value in (
                ("Kotlin 编译器（Gradle 缓存）", self.compiler),
                ("kotlin-stdlib（Gradle 缓存）", self.stdlib),
                ("java", self.java),
            )
            if not value
        ]
        if missing:
            self.skipTest("缺少 " + "、".join(missing) + "，无法编译纯 Kotlin 逻辑")
        for path in PURE_SOURCES + [TEST_SOURCE]:
            self.assertTrue(path.exists(), f"缺少 {path}")

    def test_the_pure_logic_passes_its_own_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "classes"
            out.mkdir()
            built = subprocess.run(
                [
                    self.java,
                    "-cp",
                    compiler_classpath(self.compiler, self.stdlib),
                    "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                    "-no-stdlib",
                    "-nowarn",
                    "-classpath",
                    str(self.stdlib),
                    "-d",
                    str(out),
                    *[str(p) for p in PURE_SOURCES + [TEST_SOURCE]],
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                built.returncode,
                0,
                "Kotlin 纯逻辑编译失败：\n" + (built.stdout or "") + (built.stderr or ""),
            )

            ran = subprocess.run(
                [
                    self.java,
                    # Without these a redirected JVM on Chinese Windows writes
                    # the console code page and the failure text arrives mangled.
                    "-Dfile.encoding=UTF-8",
                    "-Dsun.stdout.encoding=UTF-8",
                    "-Dsun.stderr.encoding=UTF-8",
                    "-cp",
                    os.pathsep.join([str(out), str(self.stdlib)]),
                    "cn.autow.bridge.BridgeLogicTestKt",
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            report = (ran.stdout or "") + (ran.stderr or "")
            self.assertEqual(ran.returncode, 0, "通知桥的纯逻辑检查没通过：\n" + report)
            self.assertIn("ALL PASSED", report, report)
            self.assertNotIn("FAIL", report, report)


if __name__ == "__main__":
    unittest.main()
