package io.resolve.mockchat

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.Executors

/**
 * Backup support chat for the Lumen demo. One screen, plain views, no libraries.
 * Talks to the Resolve JSON API: POST /chat, GET /api/chat/{session_id}, GET /api/users.
 */
class MainActivity : Activity() {

    private val io = Executors.newSingleThreadExecutor()

    private lateinit var baseUrlField: EditText
    private lateinit var userField: EditText
    private lateinit var messages: LinearLayout
    private lateinit var scroll: ScrollView
    private lateinit var input: EditText
    private lateinit var sendButton: Button
    private lateinit var banner: TextView
    private lateinit var status: TextView

    // Session lives in memory only: an app restart starts a new session.
    private var sessionId = newSessionId()
    private var lastActions = JSONArray()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getPreferences(MODE_PRIVATE)

        val root = vertical()
        root.setBackgroundColor(Color.WHITE)

        val title = TextView(this).apply {
            text = "Lumen · Support"
            textSize = 20f
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.parseColor("#3D2C8D"))
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        root.addView(title)

        baseUrlField = field("Base URL", prefs.getString("base_url", DEFAULT_BASE_URL)!!)
        baseUrlField.inputType = InputType.TYPE_TEXT_VARIATION_URI
        userField = field("customer id", prefs.getString("customer_user_id", "")!!)
        val settings = horizontal()
        settings.addView(baseUrlField, LinearLayout.LayoutParams(0, wrap(), 3f))
        settings.addView(userField, LinearLayout.LayoutParams(0, wrap(), 2f))
        root.addView(settings)

        val debug = horizontal()
        debug.addView(button("Users") { pickUser() }, LinearLayout.LayoutParams(0, wrap(), 1f))
        debug.addView(button("New session") { newSession() }, LinearLayout.LayoutParams(0, wrap(), 1f))
        debug.addView(button("Actions") { showActions() }, LinearLayout.LayoutParams(0, wrap(), 1f))
        root.addView(debug)

        status = TextView(this).apply {
            textSize = 12f
            setPadding(dp(12), dp(2), dp(12), dp(2))
        }
        root.addView(status)

        banner = TextView(this).apply {
            text = "A teammate will reply here"
            setBackgroundColor(Color.parseColor("#FFF3C4"))
            setTextColor(Color.parseColor("#5C4400"))
            setPadding(dp(12), dp(8), dp(12), dp(8))
            visibility = View.GONE
        }
        root.addView(banner)

        messages = vertical().apply { setPadding(dp(8), dp(8), dp(8), dp(8)) }
        scroll = ScrollView(this).apply { addView(messages) }
        root.addView(scroll, LinearLayout.LayoutParams(match(), 0, 1f))

        input = EditText(this).apply { hint = "Message" }
        sendButton = button("Send") { send() }
        val bottom = horizontal()
        bottom.addView(input, LinearLayout.LayoutParams(0, wrap(), 1f))
        bottom.addView(sendButton)
        root.addView(bottom)

        setContentView(root)
        showStatus("session $sessionId")
    }

    override fun onPause() {
        super.onPause()
        savePrefs()
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdownNow()
    }

    // --- actions ---------------------------------------------------------------------------------

    private fun send() {
        val text = input.text.toString().trim()
        val user = userField.text.toString().trim()
        if (text.isEmpty()) return
        if (user.isEmpty()) {
            showError("Set a customer id first (or tap Users)")
            return
        }
        savePrefs()
        val base = baseUrl()
        val body = JSONObject()
            .put("customer_user_id", user)
            .put("session_id", sessionId)
            .put("message", text)
        val session = sessionId

        input.setText("")
        addBubble(text, fromUser = true)
        sendButton.isEnabled = false
        showStatus("waiting for the agent…")

        background({ JSONObject(http(base, "POST", "/chat", body)) }) { res ->
            sendButton.isEnabled = true
            if (session != sessionId) return@background // a new session started meanwhile
            lastActions = res.optJSONArray("actions") ?: JSONArray()
            val reply = res.optString("reply", "")
            if (reply.isNotEmpty()) addBubble(reply, fromUser = false)
            if (res.optBoolean("handed_off", false)) banner.visibility = View.VISIBLE
            val ticket = if (res.isNull("ticket_id")) "" else " · ticket ${res.optString("ticket_id")}"
            showStatus("session $sessionId · ${lastActions.length()} action(s)$ticket")
        }
    }

    private fun newSession() {
        sessionId = newSessionId()
        lastActions = JSONArray()
        messages.removeAllViews()
        banner.visibility = View.GONE
        showStatus("session $sessionId")
    }

    private fun pickUser() {
        val base = baseUrl()
        background({ JSONArray(http(base, "GET", "/api/users", null)) }) { users ->
            if (users.length() == 0) {
                showError("No users on the server")
                return@background
            }
            val ids = Array(users.length()) { users.getJSONObject(it).optString("customer_user_id") }
            val labels = Array(users.length()) {
                val u = users.getJSONObject(it)
                val product = if (u.isNull("product")) "" else " · ${u.optString("product")}"
                "${u.optString("customer_user_id")} · ${u.optString("badge")}$product"
            }
            AlertDialog.Builder(this)
                .setTitle("Pick a user")
                .setItems(labels) { _, i ->
                    userField.setText(ids[i])
                    savePrefs()
                    newSession() // a session belongs to one user
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    private fun showActions() {
        val base = baseUrl()
        val session = sessionId
        val local = lastActions
        background({
            val out = StringBuilder()
            out.append("Last reply (${local.length()} action(s)):\n")
            if (local.length() == 0) out.append("  none\n")
            for (i in 0 until local.length()) out.append(formatAction(local.getJSONObject(i)))

            out.append("\nServer events for $session:\n")
            try {
                val chat = JSONObject(http(base, "GET", "/api/chat/" + URLEncoder.encode(session, "UTF-8"), null))
                out.append("  outcome: ${chat.optString("outcome")}")
                if (!chat.isNull("ticket_id")) out.append(" · ticket ${chat.optString("ticket_id")}")
                out.append("\n")
                val events = chat.optJSONArray("events") ?: JSONArray()
                if (events.length() == 0) out.append("  none\n")
                for (i in 0 until events.length()) out.append(formatAction(events.getJSONObject(i)))
            } catch (e: Exception) {
                out.append("  ${e.message}\n")
            }
            out.toString()
        }) { text ->
            val view = TextView(this).apply {
                this.text = text
                textSize = 13f
                setTextIsSelectable(true)
                setPadding(dp(16), dp(8), dp(16), dp(8))
            }
            AlertDialog.Builder(this)
                .setTitle("Actions")
                .setView(ScrollView(this).apply { addView(view) })
                .setPositiveButton("OK", null)
                .show()
        }
    }

    private fun formatAction(a: JSONObject): String {
        val verdict = if (a.optBoolean("allowed", false)) "allowed"
        else "BLOCKED: ${if (a.isNull("blocked_reason")) "(no reason)" else a.optString("blocked_reason")}"
        val args = a.optJSONObject("args")?.toString() ?: "{}"
        val result = if (a.isNull("result")) "" else "\n    result: ${a.opt("result")?.toString()?.take(200)}"
        return "• ${a.optString("tool")} $args\n    $verdict$result\n"
    }

    // --- plumbing --------------------------------------------------------------------------------

    /** Runs [work] off the main thread; [done] on the main thread; any exception → visible error. */
    private fun <T> background(work: () -> T, done: (T) -> Unit) {
        io.execute {
            val result = try {
                Result.success(work())
            } catch (e: Exception) {
                Result.failure(e)
            }
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                result.fold(done) { e ->
                    sendButton.isEnabled = true
                    showError(e.message ?: e.javaClass.simpleName)
                }
            }
        }
    }

    /** Blocking HTTP call; returns the body or throws IOException with the status and body. */
    private fun http(base: String, method: String, path: String, body: JSONObject?): String {
        val conn = URL(base + path).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 10_000
            conn.readTimeout = 120_000 // the agent loop can take a while
            conn.setRequestProperty("Accept", "application/json")
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            if (code !in 200..299) throw IOException("HTTP $code ${text.take(300)}")
            return text
        } catch (e: IOException) {
            throw IOException("$method $path: ${e.message}", e)
        } finally {
            conn.disconnect()
        }
    }

    private fun addBubble(text: String, fromUser: Boolean) {
        val bubble = TextView(this).apply {
            this.text = text
            textSize = 15f
            setTextIsSelectable(true)
            maxWidth = (resources.displayMetrics.widthPixels * 0.78).toInt()
            setPadding(dp(12), dp(8), dp(12), dp(8))
            setTextColor(if (fromUser) Color.WHITE else Color.BLACK)
            background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(Color.parseColor(if (fromUser) "#5B47C7" else "#ECECF1"))
            }
        }
        val params = LinearLayout.LayoutParams(wrap(), wrap()).apply {
            gravity = if (fromUser) Gravity.END else Gravity.START
            topMargin = dp(4)
            bottomMargin = dp(4)
        }
        messages.addView(bubble, params)
        scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun baseUrl() = baseUrlField.text.toString().trim().trimEnd('/')

    private fun savePrefs() {
        getPreferences(MODE_PRIVATE).edit()
            .putString("base_url", baseUrl())
            .putString("customer_user_id", userField.text.toString().trim())
            .apply()
    }

    private fun showStatus(text: String) {
        status.setTextColor(Color.DKGRAY)
        status.text = text
    }

    private fun showError(text: String) {
        status.setTextColor(Color.RED)
        status.text = text
        Toast.makeText(this, text, Toast.LENGTH_LONG).show()
    }

    private fun field(hint: String, value: String) = EditText(this).apply {
        this.hint = hint
        setText(value)
        textSize = 13f
        isSingleLine = true
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        textSize = 12f
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun vertical() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun horizontal() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        setPadding(dp(4), 0, dp(4), 0)
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun wrap() = LinearLayout.LayoutParams.WRAP_CONTENT
    private fun match() = LinearLayout.LayoutParams.MATCH_PARENT

    companion object {
        const val DEFAULT_BASE_URL = "http://10.0.2.2:8010" // emulator → laptop localhost:8010

        fun newSessionId() = "apk-" + UUID.randomUUID().toString().take(8)
    }
}
