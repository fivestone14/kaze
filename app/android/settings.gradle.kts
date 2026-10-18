// The one-module Gradle build for the Kaze shell. Plugins and libraries come from Google's and Maven Central's
// repositories; nothing else is reached at build time.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "kaze"
include(":app")
