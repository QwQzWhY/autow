package cn.autow.bridge

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Brings the listener back after a restart, without the user remembering to. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            KeepAliveService.start(context)
        }
    }
}
