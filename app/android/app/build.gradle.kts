// The app module: one Activity, a WebView and the two values from local.properties compiled in as BuildConfig
// fields. A missing value fails the build by name rather than shipping a shell that opens to a sign-in error.
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

fun localValue(key: String): String {
    val file = rootProject.file("local.properties")
    if (!file.exists()) throw GradleException(
        "kaze: local.properties is missing.\n  cp local.properties.example local.properties   and fill it in."
    )
    val properties = Properties().apply { file.inputStream().use { load(it) } }
    val value = properties.getProperty(key)?.trim()
    if (value.isNullOrEmpty()) throw GradleException(
        "kaze: $key is not set in local.properties.\n  See local.properties.example for what it wants."
    )
    return value
}

val keystoreFile = rootProject.file("keystore.properties")
val hasKeystore = keystoreFile.exists()
val keystoreProperties = Properties().apply {
    if (hasKeystore) keystoreFile.inputStream().use { load(it) }
}

android {
    namespace = "com.kaze.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.kaze.app"
        minSdk = 26
        targetSdk = 34
        // Bump both on every rebuild that goes to the phone: Android refuses an APK whose versionCode is not
        // higher than the one installed.
        versionCode = 2
        versionName = "1.1"
        buildConfigField("String", "KAZE_URL", "\"${localValue("KAZE_URL").trimEnd('/')}\"")
        buildConfigField("String", "KAZE_TOKEN", "\"${localValue("KAZE_TOKEN")}\"")
    }

    signingConfigs {
        if (hasKeystore) {
            create("release") {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (hasKeystore) signingConfig = signingConfigs.getByName("release")
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

gradle.taskGraph.whenReady {
    if (!hasKeystore && allTasks.any { it.name.contains("Release", ignoreCase = true) }) {
        throw GradleException(
            "kaze: no keystore.properties, so a release build would be unsigned and Android would refuse it.\n" +
            "  Make one key, once:\n" +
            "    keytool -genkeypair -v -keystore kaze-release.jks -alias kaze -keyalg RSA -keysize 4096 " +
            "-validity 10000\n" +
            "  Then write keystore.properties (git-ignored) next to build.gradle.kts:\n" +
            "    storeFile=kaze-release.jks\n    storePassword=...\n    keyAlias=kaze\n    keyPassword=...\n" +
            "  To get going without one: gradle assembleDebug"
        )
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
