package com.py2apk.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.py2apk.app.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private var selectedUri: Uri? = null
    private var selectedName: String = ""

    private val picker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            selectedUri = uri
            selectedName = queryName(uri)
            binding.fileName.text = selectedName
            binding.buildButton.isEnabled = true
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.pickButton.setOnClickListener { picker.launch(arrayOf("text/x-python", "application/zip", "text/plain", "application/octet-stream")) }
        binding.buildButton.setOnClickListener { startBuild() }
        binding.settingsButton.setOnClickListener { startActivity(Intent(Settings.ACTION_SETTINGS)) }
        binding.buildButton.isEnabled = false
    }

    private fun startBuild() {
        val uri = selectedUri ?: return
        val server = binding.serverUrl.text.toString().trim().removeSuffix("/")
        if (server.isBlank()) { toast("Enter a builder server URL"); return }
        binding.buildButton.isEnabled = false
        binding.progress.progress = 0
        binding.status.text = "Uploading project…"
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) { upload(server, uri) }
                val id = result.getString("id")
                binding.status.text = "Build queued: $id"
                poll(server, id)
            } catch (e: Exception) {
                binding.status.text = "Build failed: ${e.message ?: "unknown error"}"
                binding.buildButton.isEnabled = true
            }
        }
    }

    private suspend fun poll(server: String, id: String) {
        repeat(120) {
            val result = withContext(Dispatchers.IO) { getJson("$server/v1/builds/$id") }
            val status = result.optString("status", "unknown")
            binding.progress.progress = result.optInt("progress", 0).coerceIn(0, 100)
            binding.status.text = result.optString("message", status)
            if (status == "completed") {
                val apkUrl = result.optString("apk_url")
                if (apkUrl.isNotBlank()) downloadApk(server, apkUrl, id)
                binding.buildButton.isEnabled = true
                return
            }
            if (status == "failed") { binding.buildButton.isEnabled = true; return }
            kotlinx.coroutines.delay(2000)
        }
        binding.status.text = "Build is still running."
        binding.buildButton.isEnabled = true
    }

    private fun upload(server: String, uri: Uri): JSONObject {
        val connection = URL("$server/v1/builds").openConnection() as HttpURLConnection
        val boundary = "----Py2APK${System.currentTimeMillis()}"
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.connectTimeout = 15000
        connection.readTimeout = 30000
        connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        connection.outputStream.use { out ->
            form(out, boundary, "app_name", binding.appName.text.toString())
            form(out, boundary, "package_name", binding.packageName.text.toString())
            form(out, boundary, "version_name", binding.versionName.text.toString())
            form(out, boundary, "version_code", binding.versionCode.text.toString())
            form(out, boundary, "requirements", binding.requirements.text.toString())
            out.write("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"$selectedName\"\r\nContent-Type: application/octet-stream\r\n\r\n".toByteArray())
            contentResolver.openInputStream(uri)!!.use { input -> input.copyTo(out) }
            out.write("\r\n--$boundary--\r\n".toByteArray())
        }
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream).bufferedReader().use { it.readText() }
        if (code !in 200..299) error(body)
        return JSONObject(body)
    }

    private fun form(out: java.io.OutputStream, boundary: String, name: String, value: String) {
        out.write("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n".toByteArray())
    }

    private fun getJson(urlString: String): JSONObject {
        val connection = URL(urlString).openConnection() as HttpURLConnection
        connection.connectTimeout = 15000
        connection.readTimeout = 30000
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream).bufferedReader().use { it.readText() }
        if (code !in 200..299) error(body)
        return JSONObject(body)
    }

    private suspend fun downloadApk(server: String, apkUrl: String, id: String) {
        binding.status.text = "Downloading APK…"
        val finalUrl = if (apkUrl.startsWith("http")) apkUrl else "$server/$apkUrl".replace("//v1", "/v1")
        val temp = File(cacheDir, "Py2APK-$id.apk")
        withContext(Dispatchers.IO) {
            val connection = URL(finalUrl).openConnection() as HttpURLConnection
            connection.connectTimeout = 15000
            connection.readTimeout = 60000
            BufferedInputStream(connection.inputStream).use { input -> FileOutputStream(temp).use { output -> input.copyTo(output) } }
        }
        val shareUri = FileProvider.getUriForFile(this, "com.py2apk.app.fileprovider", temp)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "application/vnd.android.package-archive"
            putExtra(Intent.EXTRA_STREAM, shareUri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        binding.status.text = "APK ready: ${temp.name}"
        Toast.makeText(this, "APK downloaded. Choose a file app to save it.", Toast.LENGTH_LONG).show()
        startActivity(Intent.createChooser(intent, "Save or share APK"))
    }

    private fun queryName(uri: Uri): String = uri.lastPathSegment?.substringAfterLast('/') ?: "project.py"
    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
}
