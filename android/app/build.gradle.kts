plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "cn.autow.bridge"
    compileSdk = 34

    defaultConfig {
        applicationId = "cn.autow.bridge"
        minSdk = 26
        targetSdk = 34
        // 发新版必须同时加这两个，并用带版本号的文件名发布（见 AGENTS.md）。
        // 同版本号覆盖安装会让用户分不清自己装的是哪一版。
        versionCode = 6
        versionName = "1.5"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

// 这个应用只用平台 API 和 org.json，不引入任何第三方依赖，
// 构建面越小，失败的可能越少。
