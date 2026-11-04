package com.dazn.study.notiflogger

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** POSTs one JSON body to {INGEST_URL}/v1/notification. */
class Uploader(baseUrl: String, private val timeoutMs: Int = 10_000) {

    private val endpoint = URL(baseUrl.trimEnd('/') + "/v1/notification")

    fun post(body: String): RetryQueue.Outcome {
        val conn = try {
            endpoint.openConnection() as HttpURLConnection
        } catch (e: IOException) {
            return RetryQueue.Outcome.RETRY
        }
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            classify(conn.responseCode)
        } catch (e: IOException) {
            RetryQueue.Outcome.RETRY
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        /** 2xx (incl. 200 = duplicate) is done; 408/429/5xx retry; other 4xx are permanent. */
        fun classify(code: Int): RetryQueue.Outcome = when {
            code in 200..299 -> RetryQueue.Outcome.SENT
            code == 408 || code == 429 || code >= 500 -> RetryQueue.Outcome.RETRY
            code in 400..499 -> RetryQueue.Outcome.REJECT
            else -> RetryQueue.Outcome.RETRY
        }
    }
}
