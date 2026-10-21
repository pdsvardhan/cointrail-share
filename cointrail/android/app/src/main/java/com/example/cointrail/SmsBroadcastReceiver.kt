package com.example.cointrail

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class SmsBroadcastReceiver : BroadcastReceiver() {
    companion object {
        private const val TAG = "COINTRAIL-SMS"
        private const val INGEST_URL = "https://ingest.example.com/sms"
        private val executor = Executors.newSingleThreadExecutor()
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (messages.isEmpty()) return

        var sender: String? = null
        val bodyBuilder = StringBuilder()
        var timestamp = 0L
        for (m in messages) {
            if (sender == null) sender = m.originatingAddress
            m.messageBody?.let { bodyBuilder.append(it) }
            if (timestamp == 0L) timestamp = m.timestampMillis
        }

        val body = bodyBuilder.toString()
        if (body.isBlank()) return

        val apiKey = BuildConfig.INGEST_API_KEY
        if (apiKey.isBlank()) {
            Log.w(TAG, "INGEST_API_KEY missing at build time, skipping POST")
            return
        }

        val payload = JSONObject().apply {
            put("sender", sender ?: "")
            put("body", body)
            put("timestamp_ms", timestamp)
            put("source", "android")
        }

        executor.submit {
            try {
                val conn = URL(INGEST_URL).openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("X-API-Key", apiKey)
                conn.connectTimeout = 10_000
                conn.readTimeout = 10_000
                conn.doOutput = true
                conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
                val code = conn.responseCode
                Log.i(TAG, "POST /sms -> $code from=$sender len=${body.length}")
                conn.disconnect()
            } catch (e: Exception) {
                Log.w(TAG, "POST failed: ${e.javaClass.simpleName}: ${e.message}")
            }
        }
    }
}
