package cn.autow.bridge

/**
 * Shaping a run of QQ notifications into the text the cloud reads.
 *
 * Deliberately free of every Android import. This is the part where an
 * off-by-one silently eats half a notice, so it is the part with tests
 * (`android/logic-test`, run by `tests/test_bridge_logic.py`). Anything that
 * needs a Bundle or a Notification stays in QqNotificationListener.
 */
object Transcript {

    /**
     * How much of one message is worth sending.
     *
     * The old code did `joinToString("\n").take(2000)` on the whole run: the cut
     * fell wherever 2000 characters landed, so a long notice lost its tail and
     * two long messages of chatter in front of it made the notice disappear
     * completely. Whole messages are kept now; only a single message past this
     * limit is cut, and it says so.
     */
    const val MESSAGE_LIMIT = 4000

    /**
     * Backstop against a pathological run.
     *
     * Six real messages never reach this. It exists so a bug upstream cannot
     * post an unbounded body to the cloud, not because real notice text is this
     * long.
     */
    const val RUN_LIMIT = 20000

    /**
     * Appended wherever text was lost.
     *
     * A third of a notice is worse than a short one, because it still reads like
     * the whole thing. Saying it out loud is what lets the model — and the user —
     * know to go back to the group and look. Nothing on the phone can recover
     * text QQ never put in the notification; this is the honest maximum.
     */
    const val CLIPPED = "……（通知里只带了这么多，剩下的被系统截断了）"

    /** The run as one transcript, in order, without cutting into a message. */
    fun render(texts: List<String>): String {
        val joined = texts.map { it.trim() }
            .filter { it.isNotEmpty() }
            .joinToString("\n") { clip(it) }
        // Only reachable if something upstream sends far more than it should.
        return if (joined.length <= RUN_LIMIT) joined else joined.take(RUN_LIMIT) + CLIPPED
    }

    /** One message, cut to [MESSAGE_LIMIT] and marked if it had to be. */
    fun clip(text: String): String =
        if (text.length <= MESSAGE_LIMIT) text else text.take(MESSAGE_LIMIT) + CLIPPED

    /**
     * Whichever of these holds the most text.
     *
     * QQ puts the same message into several notification fields and trims each
     * one differently — a merged notification keeps every message in one field,
     * an expanded one keeps a single long message in another. Preferring a fixed
     * order threw away whatever the others happened to hold.
     */
    fun richest(candidates: List<String>): String =
        candidates.map { it.trim() }
            .filter { it.isNotEmpty() }
            .maxByOrNull { it.length }
            .orEmpty()
}
