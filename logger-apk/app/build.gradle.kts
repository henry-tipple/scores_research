plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// One APK per instance: INSTANCE_ID and INGEST_URL are baked in at build time.
val instanceId: String = (project.findProperty("instanceId") as String?) ?: "unset"
val ingestUrl: String = (project.findProperty("ingestUrl") as String?) ?: "http://10.0.2.2:8000"
require(Regex("[a-z0-9][a-z0-9-]*").matches(instanceId)) { "instanceId must look like uk-c4-fotmob, got '$instanceId'" }
require(ingestUrl.startsWith("http://") || ingestUrl.startsWith("https://")) { "ingestUrl must be http(s)" }

android {
    namespace = "com.dazn.study.notiflogger"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.dazn.study.notiflogger"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField("String", "INSTANCE_ID", "\"$instanceId\"")
        buildConfigField("String", "INGEST_URL", "\"$ingestUrl\"")
        buildConfigField(
            "String[]", "TRACKED_PACKAGES",
            "{\"com.scores365\", \"com.mobilefootie.wc2010\", \"com.livescore\"}"
        )
    }

    buildFeatures { buildConfig = true }

    buildTypes {
        // Internal tool, sideloaded with adb: the debug signing key is fine.
        debug { isMinifyEnabled = false }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    testOptions { unitTests.isReturnDefaultValues = true }
}

// Name the output after the instance: notif-logger-uk-c4-fotmob-debug.apk
base { archivesName.set("notif-logger-$instanceId") }

dependencies {
    // Deliberately no AndroidX/WorkManager: the retry queue is plain files + an executor,
    // so the APK has no dependencies beyond the Kotlin stdlib.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
