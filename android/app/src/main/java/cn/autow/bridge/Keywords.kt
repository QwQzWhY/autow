package cn.autow.bridge

/**
 * Which QQ notifications are worth forwarding.
 *
 * No Android imports: "widen the defaults and still reach a phone that already
 * saved the old ones" is easy to get wrong and impossible to notice, so it is
 * tested (see `android/logic-test`).
 */
object Keywords {

    /**
     * The out-of-the-box trigger list.
     *
     * QQ prefixes its own markers to the notification text — one capture read
     * "[有全体消息]王熙竣: @全体成员 …" — so those wordings are matched too, and a
     * change in how QQ words a mention cannot silently stop everything. 有人@我
     * is how a direct mention of the user shows up.
     *
     * 公告 joined the list after the user asked for 群公告: QQ turns a posted
     * group announcement into an ordinary group message, and it does not
     * reliably carry the "@全体成员" wording.
     */
    const val DEFAULT = "@全体成员,@所有人,有全体消息,有人@我,公告"

    /**
     * What earlier builds shipped, verbatim.
     *
     * A saved setting that still equals one of these was never edited by the
     * user, so it can be upgraded in place. Without this, widening the defaults
     * only reaches fresh installs and the phone in the user's hand keeps
     * forwarding nothing new — which is exactly how "公告 went missing" would
     * look.
     */
    private val LEGACY_DEFAULTS = setOf(
        "@全体成员,@所有人",
        "@全体成员,@所有人,有全体消息,有人@我",
    )

    /**
     * The list actually in force.
     *
     * A stored list that is neither a legacy default nor blank is the user's own
     * choice and is left alone, including a deliberately emptied one — silently
     * putting keywords back into a box the user cleared would be worse than
     * forwarding nothing, which is at least visible on the app's own screen.
     */
    fun effective(stored: String?): String {
        if (stored == null) return DEFAULT
        return if (stored in LEGACY_DEFAULTS) DEFAULT else stored
    }

    /** The same list, split for matching. Both comma widths are accepted. */
    fun list(stored: String?): List<String> =
        effective(stored).split(',', '，')
            .map { it.trim() }
            .filter { it.isNotEmpty() }
}
