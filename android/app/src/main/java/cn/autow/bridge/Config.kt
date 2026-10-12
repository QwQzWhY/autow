package cn.autow.bridge

import android.content.Context
import android.content.SharedPreferences

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
