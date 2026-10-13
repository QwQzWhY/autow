package cn.autow.bridge

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Plain Android views built in code — no XML, no dependencies, easy to build. */
class MainActivity : Activity() {

    private lateinit var repoField: EditText
    private lateinit var tokenField: EditText
    private lateinit var keywordField: EditText
    private lateinit var statusView: TextView
    private lateinit var seenView: TextView
    private lateinit var config: Config

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = Config(this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }

        root.addView(heading("AutoW 通知桥"))
        root.addView(
            note(
                "把 QQ 群里 @全体成员 和管理员的消息转发给你的待办系统。\n" +
                    "只读取 QQ 弹出的通知，不登录、不碰聊天记录，主号零风险。"
            )
        )

        repoField = addField(root, "GitHub 仓库", "例如 zhangsan/autow", config.repo)

        tokenField = addField(
            root,
            "访问令牌 (PAT)",
            "fine-grained token，需 Contents 读写权限",
            config.token,
        )
        tokenField.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD

        keywordField = addField(
            root,
            "转发关键词（逗号分隔）",
            "默认 @全体成员,@所有人；管理员的名字也加进来",
            config.keywords,
        )

        root.addView(
            button("保存") {
                config.repo = repoField.text.toString()
                config.token = tokenField.text.toString()
                config.keywords = keywordField.text.toString()
                if (!config.isConfigured) {
                    toast("仓库要写成 owner/repo，令牌不能为空")
                } else {
                    toast("已保存")
                }
                refreshStatus()
            }
        )

        root.addView(
            button("① 开启通知使用权") {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            }
        )

        root.addView(
            button("② 发一条测试请求") {
                config.repo = repoField.text.toString()
                config.token = tokenField.text.toString()
                config.keywords = keywordField.text.toString()
                Thread {
                    val result = GithubClient.dispatch(
                        repo = config.repo,
                        token = config.token,
                        group = "测试群",
                        sender = "AutoW",
                        text = "这是一条测试消息：请于明天中午前确认装置正常。",
                        time = Config.nowStamp(),
                    )
                    runOnUiThread {
                        toast(
                            result.fold(
                                { "成功，去 GitHub Actions 看运行记录" },
                                { "失败：${it.message}" },
                            )
                        )
                        refreshStatus()
                    }
                }.start()
            }
        )

        statusView = TextView(this).apply {
            textSize = 13f
            setTextColor(Color.parseColor("#555555"))
            setPadding(0, dp(16), 0, 0)
        }
        root.addView(statusView)

        seenView = TextView(this).apply {
            textSize = 12f
            setTextColor(Color.parseColor("#777777"))
            setPadding(0, dp(14), 0, 0)
        }
        root.addView(seenView)

        setContentView(ScrollView(this).apply { addView(root) })
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun refreshStatus() {
        statusView.text = buildString {
            append(if (enabled()) "✅ 通知使用权已开启\n" else "⚠️ 还没开启通知使用权\n")
            append(if (config.isConfigured) "✅ 仓库已配置\n" else "⚠️ 仓库未配置\n")
            append("\n最近一次：\n")
            append(config.lastResult)
        }
        seenView.text = buildSeenReport()
    }

    /** Shows what the notification listener has actually seen, if anything. */
    private fun buildSeenReport(): String {
        val seen = config.seen()
        if (seen.isEmpty()) {
            return """
                ── 还没读到任何 QQ 通知 ──
                让群里有人发一条 @全体成员 的消息试试。
                如果这里一直是空的，说明通知使用权没生效（看上面第一行）。
            """.trimIndent()
        }
        return buildString {
            appendLine("── 最近读到的 QQ 通知（最多 5 条）──")
            for (item in seen) {
                val mark = if (item.optBoolean("matched")) "✅ 已匹配关键词" else "⏭ 未匹配关键词"
                appendLine("${item.optString("at")}  $mark")
                appendLine("   标题：${item.optString("title")}")
                appendLine("   正文：${item.optString("text")}")
            }
        }
    }

    private fun enabled(): Boolean {
        val flat = Settings.Secure.getString(contentResolver, "enabled_notification_listeners")
        return flat?.contains(packageName) == true
    }

    // ------------------------------------------------------------------ views

    private fun heading(text: String) = TextView(this).apply {
        this.text = text
        textSize = 22f
        setPadding(0, 0, 0, dp(8))
    }

    private fun note(text: String) = TextView(this).apply {
        this.text = text
        textSize = 13.5f
        setTextColor(Color.parseColor("#666666"))
        setPadding(0, 0, 0, dp(20))
    }

    private fun addField(root: LinearLayout, label: String, hint: String, value: String): EditText {
        root.addView(
            TextView(this).apply {
                text = label
                textSize = 14f
                setPadding(0, dp(16), 0, dp(2))
            }
        )
        val edit = EditText(this).apply {
            this.hint = hint
            setText(value)
            textSize = 14f
        }
        root.addView(edit)
        return edit
    }

    private fun button(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        textSize = 15f
        gravity = Gravity.CENTER
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = dp(12) }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()
}
