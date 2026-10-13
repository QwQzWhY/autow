package cn.autow.bridge

import android.app.Notification
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
 * user's behalf, so there is no account risk. The trade-off is that a message
 * is only visible if QQ actually raised a notification for it, and long
 * messages arrive truncated by QQ itself.
 */
class QqNotificationListener : NotificationListenerService() {

    private val worker = Executors.newSingleThreadExecutor()

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
        // Record before filtering, so a wrong guess about where QQ puts the text
        // shows up as "captured but not matched" instead of silence.
        config.recordSeen(sbn.packageName, group, body, matched)
        if (!matched) return
        if (!config.isConfigured) return

        val sender = body.substringBefore(':', "").substringBefore('：', "").trim().ifBlank { group }
        val hash = "$group|$sender|$body".hashCode().toString()
        if (Config.alreadySeen(hash)) return

        val stamp = SimpleDateFormat("yyyy-MM-dd'T'HH:mm", Locale.CHINA).format(Date())
        val appContext = applicationContext

        worker.execute {
            val result = GithubClient.dispatch(
                repo = config.repo,
                token = config.token,
                group = group,
                sender = sender,
                text = body,
                time = stamp,
            )
            result
                .onSuccess { config.remember(group, sender, body) }
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
    }
}
