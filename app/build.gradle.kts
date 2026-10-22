import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    val keystoreProperties = Properties().apply {
        val keystorePropertiesFile = rootProject.file("keystore.properties")
        if (keystorePropertiesFile.canRead()) {
            load(keystorePropertiesFile.inputStream())
        }
    }

    signingConfigs {
        create("release") {
            // keystore.properties is optional: without it the release build falls back to debug signing.
            val storePath = keystoreProperties.getProperty("storeFile", "")
            if (storePath.isNotEmpty()) {
                storeFile = rootProject.file(storePath)
                storePassword = keystoreProperties.getProperty("storePassword", "")
                keyAlias = keystoreProperties.getProperty("keyAlias", "")
                keyPassword = keystoreProperties.getProperty("keyPassword", "")
            }
        }
    }

    namespace = "com.activision.boz"

    compileSdk = 36
    ndkVersion = "25.1.8937393"

    // version.properties is gitignored; a fresh checkout does not have it, so fall back to defaults.
    val versionPropsFile = file("version.properties")
    val versionProps = Properties().apply {
        if (versionPropsFile.canRead()) load(versionPropsFile.inputStream())
    }

    defaultConfig {
        applicationId = "com.activision.boz"
        minSdk = 21
        // Low targetSdk restores legacy WRITE_EXTERNAL_STORAGE semantics for
        // the engine, which writes game data to /sdcard/Android/obb/<pkg>/.
        // At SDK 36 those writes are EACCES-blocked even with All files access.
        // App is private (never Play Store) so the trade-off is fine.
        targetSdk = 35
        versionCode = versionProps.getProperty("VERSION_CODE", "1").toInt()
        versionName = "1.0.8.1"
        versionNameSuffix = "kotlin"
        multiDexEnabled = false
    }

    buildTypes {
        getByName("release") {
            //noinspection ChromeOsAbiSupport
            ndk {
                // arm64-v8a added for the 64-bit port (see README.md)
                abiFilters += listOf("armeabi-v7a", "arm64-v8a")
            }
            //not worth enabling
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            signingConfig = if (keystoreProperties.getProperty("storeFile", "").isNotEmpty())
                signingConfigs.getByName("release")
            else
                signingConfigs.getByName("debug")
        }
    }

    sourceSets {
        getByName("main") {
            java.srcDir("src/main/java")
        }
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }

    androidResources {
        // The game data archives are huge and already compressed; keep them stored so they can be
        // opened straight from the APK.
        noCompress += listOf("dz")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21

        tasks.withType<JavaCompile>().configureEach {
            options.compilerArgs.add("-Xlint:deprecation")
        }
    }

    kotlinOptions {
        jvmTarget = "21"
    }

    dependenciesInfo {
        includeInApk = true
        includeInBundle = true
    }

    lint {
        abortOnError = false
    }

    packaging {
        jniLibs {
            // This file in the arm64-v8a folder is really a 32-bit ARM binary (wrong ABI); drop it.
            excludes += "lib/arm64-v8a/libs3eGooglePlayServices.so"
        }
        resources {
            excludes += setOf(
                "/META-INF/{AL2.0,LGPL2.1}",
                "/META-INF/LICENSE*",
                "/META-INF/NOTICE*",
            )
        }
    }
}

dependencies {
    // All native SDK sources are decompiled and inlined under src/main/java/.
    // No file-based JAR dependencies remain — see buildtools/ for the
    // decompile + Kotlin-skeleton pipeline that produced them.

    // Compose for any new Kotlin-side UI work alongside the decompiled
    // Marmalade SurfaceView.
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
}
