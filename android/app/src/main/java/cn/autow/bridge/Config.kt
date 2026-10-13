package cn.autow.bridge

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

/**
 * Settings live in SharedPreferences on the device. The GitHub token is the only
 * sensitive value; it is stored in plain text because a personal phone is the
 * threat model here. Use a fine-grained token limited to this one repository.
 */
class Config(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("autow", Context.MODE_PRIVATE)

    var repo: String
        get() = prefs.getString(KEY_REPO, "") ?: ""
        set(value) = prefs.edit().putString(KEY_REPO, value.trim()).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TOKEN, value.trim()).apply()

    var keywords: String
        get() = prefs.getString(KEY_KEYWORDS, DEFAULT_KEYWORDS) ?: DEFAULT_KEYWORDS
        set(value) = prefs.edit().putString(KEY_KEYWORDS, value.trim()).apply()

    val keywordList: List<String>
        get() = keywords.split(',', '，')
            .map { it.trim() }
            .filter { it.isNotEmpty() }

    var lastResult: String
        get() = prefs.getString(KEY_LAST, "还没有转发过消息") ?: ""
        set(value) = prefs.edit().putString(KEY_LAST, value).apply()

    /**
     * When the system last actually bound the listener.
     *
     * Separate from the permission check on purpose: after an app update the
     * setting still reads as enabled while the binding is dead, which is how
     * "nothing happens" became impossible to explain.
     */
    var listenerConnectedAt: String
        get() = prefs.getString(KEY_LISTENER_AT, "") ?: ""
        set(value) =
            prefs.edit().putString(KEY_LISTENER_AT, value).apply()

    /**
     * The last few QQ notifications seen, whether or not they matched.
     *
     * Without this, "nothing happened" is indistinguishable from "the listener
     * never fired", "QQ puts the text somewhere unexpected" and "the upload
     * failed" — the hardest part of setting this up on a phone.
     */
    fun seen(): List<JSONObject> = try {
        val array = JSONArray(prefs.getString(KEY_SEEN, "[]"))
        (0 until array.length()).map { array.getJSONObject(it) }
    } catch (e: Exception) {
        emptyList()
    }

    fun recordSeen(packageName: String, title: String, text: String, matched: Boolean) {
        val array = JSONArray()
        array.put(
            JSONObject().apply {
                put("at", nowStamp())
                put("pkg", packageName)
                put("title", title.take(60))
                put("text", text.take(140))
                put("matched", matched)
            }
        )
        seen().take(4).forEach { array.put(it) }
        prefs.edit().putString(KEY_SEEN, array.toString()).apply()
    }

    val isConfigured: Boolean
        get() = repo.contains('/') && token.isNotEmpty()

    fun remember(group: String, sender: String, text: String) {
        val short = text.replace('\n', ' ').take(40)
        lastResult = "已转发：[$group] $sender · $short\n" + nowStamp()
    }

    companion object {
        private const val KEY_REPO = "repo"
        private const val KEY_TOKEN = "token"
        private const val KEY_KEYWORDS = "keywords"
        private const val KEY_LAST = "last_result"
        private const val KEY_SEEN = "seen"
        private const val KEY_LISTENER_AT = "listener_at"

        const val DEFAULT_KEYWORDS = "@全体成员,@所有人"

        fun nowStamp(): String =
            java.text.SimpleDateFormat("MM-dd HH:mm", java.util.Locale.CHINA)
                .format(java.util.Date())

        /** Recently forwarded message hashes, so one message is not sent twice. */
        private val recent = LinkedHashSet<String>()

        @Synchronized
        fun alreadySeen(hash: String): Boolean {
            if (recent.contains(hash)) return true
            recent.add(hash)
            while (recent.size > 100) recent.remove(recent.first())
            return false
        }
    }
}
