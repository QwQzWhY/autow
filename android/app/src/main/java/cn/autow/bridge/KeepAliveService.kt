package cn.autow.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder

/**
 * Keeps the process alive so the notification listener is not killed.
 *
 * A NotificationListenerService is bound by the system, so in theory the app
 * comes back on its own. In practice several ROMs kill the process and then
 * never rebind it, and the listener goes silent until the app is opened again —
 * which looked exactly like "nothing happened" during setup.
 *
 * A foreground service is the supported way to say "the user asked this process
 * to keep doing something"; the ongoing notification is the price of that.
 */
class KeepAliveService : Service() {

    override fun onCreate() {
        super.onCreate()
        running = true
        createChannel()
        startForeground(NOTIFICATION_ID, buildNotification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())
        // START_STICKY asks the system to bring the service back if it is killed.
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            // IMPORTANCE_MIN: present but silent, it is not something to react to.
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "后台监听", NotificationManager.IMPORTANCE_MIN)
            )
        }
    }

    private fun buildNotification(): Notification =
        Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("AutoW 正在监听 QQ 通知")
            .setContentText("关掉这条通知会停止转发群消息")
            .setSmallIcon(R.drawable.ic_autow)
            .setOngoing(true)
            .build()

    companion object {
        private const val CHANNEL_ID = "autow-keepalive"
        private const val NOTIFICATION_ID = 1001

        @Volatile
        var running = false
            private set

        fun start(context: Context) {
            val intent = Intent(context, KeepAliveService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (e: Exception) {
                // Android 12+ blocks background foreground-service starts in some
                // situations. Nothing to do but carry on; opening the app retries.
            }
        }
    }
}
