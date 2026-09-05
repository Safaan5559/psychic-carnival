# Py2APK — Android APK app

Py2APK is now a **native Android application**, not a website.

The Android app lets you select a Python `.py` file or `.zip` project, enter Android app metadata, send the project to a Py2APK build server, monitor the build, and receive the generated APK.

## Android app

- Native Kotlin Android UI
- No HTML/CSS/JavaScript website
- Android file picker for `.py` and `.zip`
- App name, package name, version and Python requirements
- Upload progress/status
- Build polling and APK delivery
- Secure Android `FileProvider` for sharing generated APKs
- Release build configuration with R8 shrinking

## Build the Android APK

Open this repository in Android Studio and build:

```bash
./gradlew assembleRelease
```

The Android project uses Android Gradle Plugin 9.4.0, Gradle 9.6, JDK 17 and compile/target SDK 37.

## Build service

Converting arbitrary Python projects into Android APKs requires an Android build toolchain, so the phone app uses a **headless** build API. There is no web dashboard.

The service accepts a project at `POST /v1/builds`, builds it in an isolated Docker container using Buildozer/python-for-Android, and exposes status at `GET /v1/builds/{id}` and the APK at `GET /v1/builds/{id}/apk`.

Start the headless service with Docker Compose:

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

Then set the service address in the Android app's **Builder server URL** field.

## Important limitation

The APK itself is the Android client. It does **not** contain a complete Android SDK/NDK plus Python packaging toolchain because that would be far too large and unsuitable for a normal phone app. The actual APK compilation therefore runs on the dedicated headless builder service.

## Security

Uploaded projects are validated before extraction and the actual Android build runs in a short-lived Docker container with no network, CPU/RAM/PID limits, dropped Linux capabilities, and a build timeout. A production deployment should use a dedicated build machine/VM because access to the Docker socket is highly privileged.
