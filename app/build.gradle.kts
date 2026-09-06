import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
}

android {
    namespace = "com.py2apk.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.py2apk.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0.0"
    }

    buildFeatures { viewBinding = true }

    signingConfigs {
        create("release") {
            val keystorePath = providers.environmentVariable("PY2APK_KEYSTORE_PATH").orNull
            val keystorePassword = providers.environmentVariable("PY2APK_KEYSTORE_PASSWORD").orNull
            val keyAlias = providers.environmentVariable("PY2APK_KEY_ALIAS").orNull
            val keyPassword = providers.environmentVariable("PY2APK_KEY_PASSWORD").orNull

            if (!keystorePath.isNullOrBlank() && !keystorePassword.isNullOrBlank() &&
                !keyAlias.isNullOrBlank() && !keyPassword.isNullOrBlank()) {
                storeFile = file(keystorePath)
                storePassword = keystorePassword
                this.keyAlias = keyAlias
                this.keyPassword = keyPassword
            }
        }
    }

    buildTypes {
        release {
            val signingReady = signingConfigs.getByName("release").storeFile != null
            if (signingReady) {
                signingConfig = signingConfigs.getByName("release")
            }

            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_17 } }
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("com.google.android.material:material:1.13.0")
}
