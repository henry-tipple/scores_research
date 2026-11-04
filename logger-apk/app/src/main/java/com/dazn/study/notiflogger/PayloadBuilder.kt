package com.dazn.study.notiflogger

import android.app.Notification
import android.os.Bundle
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/** Builds the JSON body for POST /v1/notification (contract: logger-apk/README.md). */
object PayloadBuilder {

    fun build(sbn: StatusBarNotification, instanceId: String, action: String, removeReason: Int?): JSONObject {
        val n = sbn.notification
        val extras = n.extras ?: Bundle()
        return JSONObject().apply {
            put("instance", instanceId)
            put("package", sbn.packageName)
            put("posted_at", iso(sbn.postTime))
            put("notification_key", sbn.key)
            put("channel_id", n.channelId ?: JSONObject.NULL)
            put("title", text(extras, Notification.EXTRA_TITLE))
            put("text", text(extras, Notification.EXTRA_TEXT))
            put("big_text", text(extras, Notification.EXTRA_BIG_TEXT))
            put("sub_text", text(extras, Notification.EXTRA_SUB_TEXT))
            put("category", n.category ?: JSONObject.NULL)
            put("ongoing", sbn.isOngoing || (n.flags and Notification.FLAG_ONGOING_EVENT) != 0)
            put("group_key", sbn.groupKey ?: JSONObject.NULL)
            put("action", action)
            if (removeReason != null) put("remove_reason", removeReason)
            put("logged_at", iso(System.currentTimeMillis()))
            put("logger_version", BuildConfig.VERSION_NAME)
            put("extras", bundleToJson(extras))
        }
    }

    private fun text(b: Bundle, key: String): Any = b.getCharSequence(key)?.toString() ?: JSONObject.NULL

    fun iso(millis: Long): String {
        val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        f.timeZone = TimeZone.getTimeZone("UTC")
        return f.format(Date(millis))
    }

    /** Full extras bundle, recursively. Unknown parcelables are kept as their toString(). */
    @Suppress("DEPRECATION")
    fun bundleToJson(b: Bundle, depth: Int = 0): JSONObject {
        val out = JSONObject()
        for (key in b.keySet()) {
            out.put(key, toJson(b.get(key), depth))
        }
        return out
    }

    private fun toJson(v: Any?, depth: Int): Any = when {
        v == null -> JSONObject.NULL
        depth > 4 -> v.toString()
        v is Bundle -> bundleToJson(v, depth + 1)
        v is CharSequence -> v.toString()
        v is Boolean || v is Number -> v
        v is Array<*> -> JSONArray(v.map { toJson(it, depth + 1) })
        v is IntArray -> JSONArray(v.toList())
        v is LongArray -> JSONArray(v.toList())
        v is FloatArray -> JSONArray(v.map { it.toDouble() })
        v is DoubleArray -> JSONArray(v.toList())
        v is BooleanArray -> JSONArray(v.toList())
        v is Collection<*> -> JSONArray(v.map { toJson(it, depth + 1) })
        else -> v.toString()   // Bitmap, PendingIntent, Person, MessagingStyle messages, ...
    }
}
