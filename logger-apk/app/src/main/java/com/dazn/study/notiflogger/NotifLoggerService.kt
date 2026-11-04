package com.dazn.study.notiflogger

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Logs every notification the three tracked apps post (and remove) to the ingest service.
 *
 * Each event is written to the persistent [RetryQueue] first and only deleted once ingest
 * acknowledges it, so nothing is lost while the network or ingest is down, or across
 * restarts. Data-only FCM pushes never reach a NotificationListenerService and are out of
 * scope by design.
 */
class NotifLoggerService : NotificationListenerService() {

    private lateinit var queue: RetryQueue
    private lateinit var uploader: Uploader
    private var executor: ScheduledExecutorService? = null
    private val tracked = BuildConfig.TRACKED_PACKAGES.toSet()

    override fun onCreate() {
        super.onCreate()
        queue = RetryQueue(File(filesDir, "queue"), maxItems = 100_000)
        uploader = Uploader(BuildConfig.INGEST_URL)
    }

    override fun onListenerConnected() {
        Log.i(TAG, "listener connected: instance=${BuildConfig.INSTANCE_ID} ingest=${BuildConfig.INGEST_URL}")
        val ex = Executors.newSingleThreadScheduledExecutor()
        executor = ex
        // Flush on a fixed cadence; each flush stops at the first transient failure, so this
        // doubles as the retry loop (backoff = the period).
        ex.scheduleWithFixedDelay({ flushSafely() }, 0, FLUSH_PERIOD_S, TimeUnit.SECONDS)
        // Catch anything posted while we were disconnected.
        try {
            activeNotifications?.filter { it.packageName in tracked }?.forEach { record(it, "active_on_connect", null) }
        } catch (e: SecurityException) {
            Log.w(TAG, "cannot read active notifications", e)
        }
    }

    override fun onListenerDisconnected() {
        executor?.shutdown()
        executor = null
        requestRebind(android.content.ComponentName(this, NotifLoggerService::class.java))
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName in tracked) record(sbn, "posted", null)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification, rankingMap: RankingMap, reason: Int) {
        if (sbn.packageName in tracked) record(sbn, "removed", reason)
    }

    private fun record(sbn: StatusBarNotification, action: String, removeReason: Int?) {
        val payload = PayloadBuilder.build(sbn, BuildConfig.INSTANCE_ID, action, removeReason)
        queue.enqueue(payload.toString())
        executor?.execute { flushSafely() }
    }

    private fun flushSafely() {
        try {
            val result = queue.drain { body -> uploader.post(body) }
            if (result.sent > 0 || result.dropped > 0) {
                Log.i(TAG, "flushed sent=${result.sent} dropped=${result.dropped} pending=${queue.size()}")
            }
        } catch (t: Throwable) {
            Log.w(TAG, "flush failed", t)
        }
    }

    companion object {
        private const val TAG = "NotifLogger"
        private const val FLUSH_PERIOD_S = 30L
    }
}
