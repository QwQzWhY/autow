package cn.autow.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.AlarmManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.SystemClock

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
        scheduleWatchdog()
        // START_STICKY asks the system to bring the service back if it is killed.
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        // If the service is going away for any reason other than the user
        // explicitly stopping it, line up a restart before it is too late.
        restartLater(2_000L)
        super.onDestroy()
    }

    /**
     * ColorOS kills the whole process when the app is swiped out of recents.
     *
     * This is the standard counter: an alarm fires a second later with a
     * PendingIntent, and the short allowlist AlarmManager grants is what makes it
     * legal to start a foreground service again from the background.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        restartLater(1_000L)
        super.onTaskRemoved(rootIntent)
    }

    /**
     * A slow repeating nudge, in case the process is killed in some way that
     * never reaches onTaskRemoved or onDestroy.
     */
    private fun scheduleWatchdog() {
        val manager = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val pending = servicePendingIntent(RESTART_REQUEST + 1)
            ?: return
        try {
            manager.setInexactRepeating(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                SystemClock.elapsedRealtime() + WATCHDOG_MS,
                WATCHDOG_MS,
                pending,
            )
        } catch (e: Exception) {
            // Non-fatal: the foreground service itself may still survive.
        }
    }

    private fun restartLater(delayMs: Long) {
        val pending = servicePendingIntent(RESTART_REQUEST) ?: return
        val manager = getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val at = System.currentTimeMillis() + delayMs
        try {
            // Exact alarms are not available to every app on Android 14; the
            // inexact fallback is only delayed, not refused.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pending)
            } else {
                manager.set(AlarmManager.RTC_WAKEUP, at, pending)
            }
        } catch (e: Exception) {
            try {
                manager.set(AlarmManager.RTC_WAKEUP, at, pending)
            } catch (ignored: Exception) {
            }
        }
    }

    private fun servicePendingIntent(requestCode: Int): PendingIntent? {
        val restart = Intent(applicationContext, KeepAliveService::class.java)
        val flags = PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_IMMUTABLE
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            PendingIntent.getForegroundService(applicationContext, requestCode, restart, flags)
        } else {
            PendingIntent.getService(applicationContext, requestCode, restart, flags)
        }
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
        private const val RESTART_REQUEST = 2001
        private const val WATCHDOG_MS = 15 * 60 * 1000L

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
