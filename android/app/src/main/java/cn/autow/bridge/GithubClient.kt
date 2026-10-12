package cn.autow.bridge

import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/**
 * Fires a `repository_dispatch` event at GitHub, which wakes the scanning
 * workflow and hands it the message text. No SDK, no third-party HTTP client.
 */
object GithubClient {

    private const val API = "https://api.github.com/repos"

    fun dispatch(
        repo: String,
        token: String,
        group: String,
        sender: String,
        text: String,
        time: String,
    ): Result<Unit> = runCatching {
        val payload = JSONObject().apply {
            put("event_type", "qq-message")
            put(
                "client_payload",
                JSONObject().apply {
                    put("group", group)
                    put("sender", sender)
                    put("text", text)
                    put("time", time)
                    put("device", android.os.Build.MODEL)
                }
            )
        }

        val connection = URL("$API/$repo/dispatches").openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.connectTimeout = 15_000
        connection.readTimeout = 20_000
        connection.setRequestProperty("Accept", "application/vnd.github+json")
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        connection.setRequestProperty("User-Agent", "AutoW-Bridge")

        connection.outputStream.use {
            it.write(payload.toString().toByteArray(StandardCharsets.UTF_8))
        }

        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            ?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
        connection.disconnect()

        check(code in 200..299) {
            "GitHub 返回 $code${if (body.isBlank()) "" else "：" + body.take(200)}"
        }
    }
}
