package cn.autow.bridge

import android.app.Notification
import android.content.ComponentName
import android.os.Build
import android.os.Bundle
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
 * Two things make QQ notifications awkward, and both are handled by keeping a
 * short rolling history per group instead of looking at one notification:
 *
 *  - A mention is often its own message, and the notice can come either before
 *    it or after it. Watching only forwards would miss the first order, only
 *    backwards the second, so a match sends the whole recent run.
 *  - One notification can carry several messages at once (EXTRA_TEXT_LINES),
 *    and taking only the first field threw the rest away.
 *
 * The history lives in memory only; nothing is written to disk.
 */
class QqNotificationListener : NotificationListenerService() {

    private val worker = Executors.newSingleThreadExecutor()

    private data class Msg(val who: String, val text: String, val at: Long)

    /** A mention, and how long its surrounding run stays interesting. */
    private data class Episode(val id: String, val until: Long, val sent: Int)

    private val history = HashMap<String, MutableList<Msg>>()
    private val episodes = HashMap<String, Episode>()

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

        val extras = notification.extras ?: return
        val group = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val sub = extras.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString().orEmpty()
        val body = richestText(extras)
        if (body.isBlank()) return

        val config = Config(applicationContext)
        val haystack = listOf(group, sub, body).joinToString(" ")
        val matched = config.keywordList.any { haystack.contains(it) }

        val now = System.currentTimeMillis()
        val who = body.substringBefore(':', "").substringBefore('：', "").trim().ifBlank { group }
        remember(group, Msg(who, body, now), now)
        config.recordSeen(sbn.packageName, group, body, matched)

        val id = nextEpisode(group, now, matched) ?: return
        if (!config.isConfigured) return

        // Whatever we have seen around the mention is what gets sent, so the
        // notice is included whether it came before or after.
        val run = history[group].orEmpty()
        val text = run.joinToString("\n") { it.text }.take(MAX_CONTEXT_CHARS)
        if (Config.alreadySeen("$id|$text")) return

        val stamp = SimpleDateFormat("yyyy-MM-dd'T'HH:mm", Locale.CHINA).format(Date())
        val clock = SimpleDateFormat("HH:mm", Locale.CHINA)
        val messages = run.map {
            mapOf("who" to it.who, "text" to it.text, "at" to clock.format(Date(it.at)))
        }

        val appContext = applicationContext
        worker.execute {
            val result = GithubClient.dispatch(
                repo = config.repo,
                token = config.token,
                group = group,
                sender = who,
                text = text,
                time = stamp,
                id = id,
                messages = messages,
            )
            result
                .onSuccess { config.remember(group, who, text) }
                .onFailure { config.lastResult = "转发失败：${it.message}\n${Config.nowStamp()}" }
            appContext.getSharedPreferences("autow", MODE_PRIVATE)
                .edit()
                .putString("last_result", config.lastResult)
                .apply()
        }
    }

    /**
     * Which run this notification belongs to, or null if it is not interesting.
     *
     * A match opens one; anything else from the same group while it is open joins
     * it, under the same id, so the cloud refreshes one todo rather than stacking
     * several. The window is anchored to the mention and never extended — an
     * extended one kept a chatty group armed all evening.
     */
    private fun nextEpisode(group: String, now: Long, matched: Boolean): String? {
        if (matched) {
            val id = "g" + now.toString(36)
            episodes[group] = Episode(id, now + EPISODE_WINDOW_MS, 0)
            return id
        }
        val open = episodes[group] ?: return null
        if (open.until < now || open.sent >= MAX_FOLLOW_UPS) {
            episodes.remove(group)
            return null
        }
        episodes[group] = open.copy(sent = open.sent + 1)
        return open.id
    }

    /**
     * The text of a notification, preferring the field that carries the most.
     *
     * A merged notification lists every message in EXTRA_TEXT_LINES while
     * EXTRA_TEXT holds only the newest, so reading EXTRA_TEXT first silently
     * drops messages.
     */
    private fun richestText(extras: Bundle): String {
        val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            ?.map { it.toString().trim() }
            ?.filter { it.isNotEmpty() }
            .orEmpty()
        if (lines.isNotEmpty()) return lines.joinToString("\n")

        val big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()?.trim().orEmpty()
        if (big.isNotEmpty()) return big

        return extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
    }

    private fun remember(group: String, msg: Msg, now: Long) {
        val list = history.getOrPut(group) { mutableListOf() }
        list.add(msg)
        // Bound both ends: by age, because older messages are a different notice,
        // and by count, so a busy group cannot grow this without limit.
        list.removeAll { now - it.at > HISTORY_WINDOW_MS }
        while (list.size > HISTORY_MAX) list.removeAt(0)

        history.entries.removeAll { (key, value) ->
            key != group && now - (value.lastOrNull()?.at ?: 0L) > HISTORY_WINDOW_MS
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

        /** How much of the run around a mention is worth sending. */
        private const val HISTORY_WINDOW_MS = 5 * 60 * 1000L
        private const val HISTORY_MAX = 6

        /** How long a mention keeps its run open. Anchored, never extended. */
        private const val EPISODE_WINDOW_MS = 3 * 60 * 1000L
        private const val MAX_FOLLOW_UPS = 5

        /** Stop growing the joined text; the model only reads the first few pages. */
        private const val MAX_CONTEXT_CHARS = 2000
    }
}
