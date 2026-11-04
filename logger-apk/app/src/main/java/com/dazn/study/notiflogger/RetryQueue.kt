package com.dazn.study.notiflogger

import java.io.File
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong

/**
 * Persistent FIFO of JSON bodies: one file per item, written atomically (tmp + rename),
 * deleted only after the server accepted it. Survives process death and reboots.
 *
 * Pure JVM (java.io only) so it is unit-tested without Android.
 */
class RetryQueue(private val dir: File, private val maxItems: Int = 100_000) {

    enum class Outcome { SENT, RETRY, REJECT }

    data class DrainResult(val sent: Int, val dropped: Int, val remaining: Int)

    private val seq = AtomicLong(System.currentTimeMillis() * 1000)
    private val rejected = File(dir, "rejected")

    init {
        dir.mkdirs()
        rejected.mkdirs()
    }

    @Synchronized
    fun enqueue(body: String) {
        val name = String.format("%020d-%s.json", seq.incrementAndGet(), UUID.randomUUID())
        val tmp = File(dir, "$name.tmp")
        tmp.writeText(body)
        check(tmp.renameTo(File(dir, name))) { "rename failed for $name" }
        trim()
    }

    fun size(): Int = items().size

    /**
     * Sends items oldest first. Stops at the first RETRY (network down: keep order, try
     * again later). REJECT (permanent 4xx) moves the item to `rejected/` so one bad
     * payload can never block the queue.
     */
    @Synchronized
    fun drain(send: (String) -> Outcome): DrainResult {
        var sent = 0
        var dropped = 0
        for (f in items()) {
            when (send(f.readText())) {
                Outcome.SENT -> { f.delete(); sent++ }
                Outcome.REJECT -> { f.renameTo(File(rejected, f.name)); dropped++ }
                Outcome.RETRY -> return DrainResult(sent, dropped, size())
            }
        }
        return DrainResult(sent, dropped, 0)
    }

    private fun items(): List<File> =
        (dir.listFiles { f -> f.isFile && f.name.endsWith(".json") } ?: emptyArray()).sortedBy { it.name }

    /** Bound disk use: beyond maxItems the oldest items go to rejected/ (never silently lost). */
    private fun trim() {
        val all = items()
        if (all.size <= maxItems) return
        all.take(all.size - maxItems).forEach { it.renameTo(File(rejected, it.name)) }
    }
}
