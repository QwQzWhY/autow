package cn.autow.bridge

import android.app.Notification
import android.content.ComponentName
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * Reads QQ notifications and forwards the interesting ones.
 *
 * This deliberately does not touch the QQ protocol: it only watches the
 * notifications the official QQ app already shows. Nothing is logged in on the
 * user's behalf, so there is no account risk.
 *
 * The awkward part is that a mention is often sent on its own — "@全体成员" as one
 * message and the actual notice as the next one. Forwarding only the first gives
 * the model nothing to read. So a match *arms* the group for a few minutes, and
 * anything that follows is sent under the same id with the text joined up. The
 * cloud keys on that id, so the two messages end up as one todo.
 */
class QqNotificationListener : NotificationListenerService() {

    private val worker = Executors.newSingleThreadExecutor()

    /** Groups that a recent mention has armed, keyed by the group title. */
    private val armed = HashMap<String, Armed>()

    private data class Armed(val id: String, val text: String, val until: Long)

    override fun onListenerConnected() {
        super.onListenerConnected()
        // Proves the service is actually bound, which the permission string alone
        // does not: after an app update the binding can go stale while the
        // setting still reads as enabled.
        Config(applicationContext).listenerConnectedAt = Config.nowStamp()
    }

    override fun onListenerDisconnected() {
        super.onListenerDisconnected()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            try {
                requestRebind(ComponentName(this, QqNotificationListener::class.java))
            } catch (e: Exception) {
            }
        }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val notification = sbn?.notification ?: return
        if (sbn.packageName !in QQ_PACKAGES) return

        val config = Config(applicationContext)

        val extras = notification.extras ?: return
        val group = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val sub = extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString().orEmpty()
        val body = listOfNotNull(
            extras.getCharSequence(Notification.EXTRA_TEXT)?.toString(),
            extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString(),
            extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)?.joinToString("\n"),
        ).firstOrNull { !it.isNullOrBlank() }.orEmpty()

        if (body.isBlank()) return

        // QQ puts the group in the title and "sender: text" in the body; on some
        // versions the sender becomes the title instead. Search all of it.
        val haystack = listOf(group, sub, body).joinToString(" ")
        val matched = config.keywordList.any { haystack.contains(it) }

        val now = System.currentTimeMillis()
        armed.entries.removeAll { it.value.until < now }
        val pending = armed[group]

        config.recordSeen(sbn.packageName, group, body, matched)
        if (!matched && pending == null) return
        if (!config.isConfigured) return

        val id: String
        val text: String
        if (matched) {
            // Send the mention straight away so nothing is lost if no follow-up
            // ever arrives; the follow-up will re-send under the same id.
            id = "g" + now.toString(36)
            text = body
            armed[group] = Armed(id, body, now + CONTEXT_WINDOW_MS)
        } else {
            id = pending!!.id
            text = (pending.text + "\n" + body).take(MAX_CONTEXT_CHARS)
            armed[group] = Armed(id, text, now + CONTEXT_WINDOW_MS)
        }

        val sender = body.substringBefore(':', "").substringBefore('：', "").trim().ifBlank { group }
        if (Config.alreadySeen("$id|$text")) return

        val stamp = SimpleDateFormat("yyyy-MM-dd'T'HH:mm", Locale.CHINA).format(Date())
        val appContext = applicationContext

        worker.execute {
            val result = GithubClient.dispatch(
                repo = config.repo,
                token = config.token,
                group = group,
                sender = sender,
                text = text,
                time = stamp,
                id = id,
            )
            result
                .onSuccess { config.remember(group, sender, text) }
                .onFailure { config.lastResult = "转发失败：${it.message}\n${Config.nowStamp()}" }
            appContext.getSharedPreferences("autow", MODE_PRIVATE)
                .edit()
                .putString("last_result", config.lastResult)
                .apply()
        }
    }

    override fun onDestroy() {
        worker.shutdown()
        super.onDestroy()
    }

    companion object {
        /** QQ and the lite/TIM builds ship different package names. */
        private val QQ_PACKAGES = setOf(
            "com.tencent.mobileqq",
            "com.tencent.tim",
            "com.tencent.mobileqqi",
        )

        /** How long after a mention the next messages still count as its context. */
        private const val CONTEXT_WINDOW_MS = 3 * 60 * 1000L

        /** Stop growing the joined text; the model only reads the first few pages. */
        private const val MAX_CONTEXT_CHARS = 2000
    }
}
