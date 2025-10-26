pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }

    plugins {
        id("de.fayard.refreshVersions") version "0.60.1"
    }
}

plugins {
    id("de.fayard.refreshVersions")
}
rootProject.name = "base-java-spring-be"
