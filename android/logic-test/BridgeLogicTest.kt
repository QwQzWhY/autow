package cn.autow.bridge

/**
 * Standalone checks for the two pieces of the bridge that are pure Kotlin.
 *
 * This project has no test framework on purpose — the app itself takes no
 * third-party dependency, so there is no JUnit to hang a test off. A plain
 * `main` that prints what failed and exits non-zero works without one, and
 * `tests/test_bridge_logic.py` compiles this file together with Transcript.kt
 * and Keywords.kt and runs it.
 *
 * Everything else in the bridge needs a Bundle, a Notification or a phone, and
 * is verified by compiling the APK and by the app's own 「最近读到的 QQ 通知」
 * screen.
 */

private var failures = 0

private fun check(name: String, ok: Boolean, detail: String = "") {
    if (ok) {
        println("ok   $name")
    } else {
        failures++
        println("FAIL $name${if (detail.isEmpty()) "" else " —— $detail"}")
    }
}

private fun expect(name: String, expected: String, actual: String) {
    check(name, expected == actual, "期望「${brief(expected)}」，实际「${brief(actual)}」")
}

private fun brief(text: String): String =
    if (text.length <= 60) text else "${text.take(30)}…（共 ${text.length} 字）"

fun main() {
    // ---------------------------------------------------------- 超长消息
    // The regression this guards: the run used to be cut with take(2000) on the
    // joined string, so anything past 2000 characters was lost wherever the cut
    // happened to land.
    val notice = "请于10月25日前提交实验报告到实验中心，" + "说明文字".repeat(500) + "末尾还有一句：签名。"
    check("测试用例本身比旧上限长", notice.length > 2000, "只有 ${notice.length} 字")

    val rendered = Transcript.render(listOf(notice))
    expect("单独的正文一字不少", notice, rendered)

    // Two long messages of chatter before the notice used to push the notice out
    // of the first 2000 characters entirely.
    val chatterA = "甲".repeat(1500)
    val chatterB = "乙".repeat(1500)
    val withChatter = Transcript.render(listOf(chatterA, chatterB, notice))
    check("闲聊再长也不挤掉后面的正文", withChatter.contains(notice))
    check("闲聊也照原样保留", withChatter.startsWith(chatterA) && withChatter.contains(chatterB))

    val huge = "丙".repeat(Transcript.MESSAGE_LIMIT + 1)
    val clipped = Transcript.render(listOf(huge))
    check("超长消息只保留到上限", clipped.startsWith("丙".repeat(Transcript.MESSAGE_LIMIT)))
    check("截断处说清楚是被系统截断的", clipped.endsWith(Transcript.CLIPPED))
    check(
        "截断不会多留一个字",
        clipped == "丙".repeat(Transcript.MESSAGE_LIMIT) + Transcript.CLIPPED,
    )

    val exact = "丁".repeat(Transcript.MESSAGE_LIMIT)
    expect("正好到上限的不加截断标记", exact, Transcript.clip(exact))
    expect("短消息原样返回", "短消息", Transcript.clip("短消息"))

    // A notice spread over several messages has to survive as several lines, in
    // order, with nothing dropped.
    expect(
        "多行按原顺序拼成一段",
        "第一行\n第二行\n第三行",
        Transcript.render(listOf("第一行", "第二行", "第三行")),
    )
    expect("空行不会拼进来", "只有一行", Transcript.render(listOf("", "只有一行", "   ")))
    expect("全空时得到空串", "", Transcript.render(listOf("", "  ")))
    expect(
        "每行两端的空白会被去掉",
        "甲\n乙",
        Transcript.render(listOf("  甲  ", "\n乙\n")),
    )

    // ------------------------------------------------- 取最完整的那个字段
    expect("取最长的候选", "最长的那一段", Transcript.richest(listOf("短", "最长的那一段", "中等长度")))
    expect("忽略空候选", "有内容", Transcript.richest(listOf("", "  ", "有内容")))
    expect("全空时得到空串", "", Transcript.richest(listOf("", "   ")))
    expect(
        "候选两端的空白会被去掉",
        "有内容",
        Transcript.richest(listOf("  有内容  ")),
    )

    // ---------------------------------------------------------- 默认关键词
    check("默认关键词里有公告", Keywords.DEFAULT.contains("公告"), Keywords.DEFAULT)
    expect("没存过就用默认", Keywords.DEFAULT, Keywords.effective(null))
    expect(
        "第一版默认值会被升级",
        Keywords.DEFAULT,
        Keywords.effective("@全体成员,@所有人"),
    )
    expect(
        "1.7 的默认值会被升级",
        Keywords.DEFAULT,
        Keywords.effective("@全体成员,@所有人,有全体消息,有人@我"),
    )
    expect(
        "自己改过的关键词不动",
        "作业,实验报告",
        Keywords.effective("作业,实验报告"),
    )
    expect("自己清空的也照原样", "", Keywords.effective(""))
    check("默认列表里有公告", Keywords.list(null).contains("公告"), Keywords.list(null).toString())
    check(
        "默认关键词正好五项",
        Keywords.list(null).size == 5,
        Keywords.list(null).toString(),
    )
    expect(
        "中文逗号也能分隔",
        "[作业, 实验报告]",
        Keywords.list("作业，实验报告").toString(),
    )
    check("空项被丢掉", Keywords.list("作业,,实验报告").size == 2, Keywords.list("作业,,实验报告").toString())

    if (failures == 0) {
        // ASCII on purpose: this line is checked by tests/test_bridge_logic.py,
        // and a redirected Java process on a Chinese Windows writes the console
        // code page, which mangles the Chinese names above but not this.
        println("ALL PASSED")
    } else {
        println("$failures failed")
        kotlin.system.exitProcess(1)
    }
}
