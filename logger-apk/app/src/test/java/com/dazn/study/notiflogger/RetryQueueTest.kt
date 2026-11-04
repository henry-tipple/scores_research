package com.dazn.study.notiflogger

import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class RetryQueueTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test fun keepsOrderAndStopsOnRetry() {
        val q = RetryQueue(tmp.newFolder("q"))
        listOf("a", "b", "c").forEach(q::enqueue)
        val seen = mutableListOf<String>()
        val r = q.drain { body -> seen += body; if (body == "b") RetryQueue.Outcome.RETRY else RetryQueue.Outcome.SENT }
        assertEquals(listOf("a", "b"), seen)
        assertEquals(1, r.sent)
        assertEquals(2, q.size())
    }

    @Test fun survivesRestart() {
        val dir = tmp.newFolder("q")
        RetryQueue(dir).enqueue("x")
        assertEquals(1, RetryQueue(dir).size())
    }

    @Test fun rejectMovesAside() {
        val dir = tmp.newFolder("q")
        val q = RetryQueue(dir)
        q.enqueue("bad"); q.enqueue("good")
        val r = q.drain { if (it == "bad") RetryQueue.Outcome.REJECT else RetryQueue.Outcome.SENT }
        assertEquals(1, r.sent); assertEquals(1, r.dropped); assertEquals(0, q.size())
        assertEquals(1, File(dir, "rejected").listFiles()!!.size)
    }

    @Test fun boundedSize() {
        val q = RetryQueue(tmp.newFolder("q"), maxItems = 3)
        repeat(5) { q.enqueue("$it") }
        assertEquals(3, q.size())
    }

    @Test fun httpClassification() {
        assertEquals(RetryQueue.Outcome.SENT, Uploader.classify(201))
        assertEquals(RetryQueue.Outcome.SENT, Uploader.classify(200))
        assertEquals(RetryQueue.Outcome.RETRY, Uploader.classify(503))
        assertEquals(RetryQueue.Outcome.RETRY, Uploader.classify(429))
        assertEquals(RetryQueue.Outcome.REJECT, Uploader.classify(422))
    }
}
