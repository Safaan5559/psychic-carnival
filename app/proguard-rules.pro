# Py2APK currently relies on AndroidX and platform APIs only.
# Keep FileProvider metadata and generated view binding classes.
-keep class androidx.core.content.FileProvider { *; }
-keep class com.py2apk.app.databinding.** { *; }
