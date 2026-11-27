#!/usr/bin/env python3
"""Convert the Android host to native Compose while retaining existing Android services."""
from __future__ import annotations
import base64, gzip, re, shutil, sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("usage: migrate.py <source> <compose-b64>")
root = Path(sys.argv[1]).resolve()
payload_path = Path(sys.argv[2])
if payload_path.is_dir():
    payload = "".join(
        (payload_path / f"compose.part{i}.b64").read_text(encoding="ascii").strip()
        for i in range(1, 6)
    )
else:
    payload = payload_path.read_text(encoding="ascii").strip()
try:
    compose = gzip.decompress(base64.b64decode(payload, validate=True)).decode("utf-8")
except Exception as exc:
    raise SystemExit(f"Compose payload decode failed: {exc}")

# Repair the Compose collection-card nesting and import scroll APIs used by dialogs.
scroll_import = "import androidx.compose.foundation.text.KeyboardOptions"
if scroll_import not in compose:
    raise SystemExit("Expected Compose keyboard import was not found")
compose = compose.replace(
    scroll_import,
    scroll_import + "\nimport androidx.compose.foundation.rememberScrollState\nimport androidx.compose.foundation.verticalScroll",
    1,
)
old_row = r'''            CardSurface(p, Modifier.fillMaxWidth()) { Row(Modifier.padding(13.dp), verticalAlignment = Alignment.CenterVertically) { Column(Modifier.weight(1f)) { Text(primary, color = p.ink, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis); if (secondary.isNotBlank()) Text(secondary, color = p.ink3, fontSize = 11.sp, maxLines = 2, overflow = TextOverflow.Ellipsis) }; if (route == "tasks") { Checkbox(checked = str(rec, "status") == "done", onCheckedChange = { done -> onSave(mapping, JSONObject(rec.toString()).put("status", if (done) "done" else "todo").put("completedAt", if (done) System.currentTimeMillis() else JSONObject.NULL)) } } } }'''
new_row = """            CardSurface(p, Modifier.fillMaxWidth()) {
                Row(Modifier.padding(13.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(primary, color = p.ink, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (secondary.isNotBlank()) {
                            Text(secondary, color = p.ink3, fontSize = 11.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                    if (route == "tasks") {
                        Checkbox(
                            checked = str(rec, "status") == "done",
                            onCheckedChange = { done ->
                                val updated = JSONObject(rec.toString())
                                    .put("status", if (done) "done" else "todo")
                                    .put("completedAt", if (done) System.currentTimeMillis() else JSONObject.NULL)
                                onSave(mapping, updated)
                            }
                        )
                    }
                }
            }"""
if compose.count(old_row) != 1:
    raise SystemExit(f"Expected exactly one malformed task-card block, found {compose.count(old_row)}")
compose = compose.replace(old_row, new_row, 1)

# Close the SettingsModule LazyColumn and function scopes before the next top-level screen.
settings_boundary = "\\n}\\n\\n@Composable\\nprivate fun CalendarModule("
if compose.count(settings_boundary) != 1:
    raise SystemExit(f"Expected one SettingsModule/CalendarModule boundary, found {compose.count(settings_boundary)}")
compose = compose.replace(
    settings_boundary,
    "\\n}\\n}\\n}\\n\\n@Composable\\nprivate fun CalendarModule(",
    1,
)

android = root / "android"
app = android / "app"
java = app / "src/main/java/com/dayflow/app"
ui = java / "ui"
ui.mkdir(parents=True, exist_ok=True)
(ui / "NativeDayflowApp.kt").write_text(compose, encoding="utf-8")

main = '''package com.dayflow.app
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.mutableStateOf
import androidx.core.view.WindowCompat
import com.dayflow.app.alarm.ActivityActions
import com.dayflow.app.alarm.AlarmScheduler
import com.dayflow.app.alarm.NotificationHelper
import com.dayflow.app.ui.NativeDayflowApp

class MainActivity : ComponentActivity() {
    private val requestedScreen = mutableStateOf<String?>(null)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        NotificationHelper.createChannels(this)
        AlarmScheduler.prune(this)
        handleLaunch(intent)
        setContent { NativeDayflowApp(initialScreen = requestedScreen.value) }
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleLaunch(intent)
    }
    private fun handleLaunch(intent: Intent?) {
        val key = intent?.getStringExtra("alarmKey")
        val blockId = intent?.getStringExtra("openBlockId")
        val screen = intent?.getStringExtra("openScreen")
        if (!key.isNullOrEmpty()) Thread {
            runCatching { ActivityActions.apply(this, "DISMISS", key, blockId, "app") }
        }.start()
        requestedScreen.value = when {
            !screen.isNullOrBlank() -> screen
            !blockId.isNullOrBlank() -> "today"
            else -> requestedScreen.value
        }
    }
}
'''
(java / "MainActivity.kt").write_text(main, encoding="utf-8")

receivers = java / "alarm/Receivers.kt"
text = receivers.read_text(encoding="utf-8")
text = text.replace("import com.dayflow.app.plugin.DayflowNativePlugin\n", "")
text = re.sub(r"^\s*DayflowNativePlugin\.emitDataChanged\(\)\s*\n", "", text, flags=re.M)
if "DayflowNativePlugin" in text:
    raise SystemExit("A Capacitor event reference remains in Receivers.kt")
receivers.write_text(text, encoding="utf-8")

root_gradle = android / "build.gradle"
root_text = root_gradle.read_text(encoding="utf-8")
if "compose-compiler-gradle-plugin" not in root_text:
    root_text = root_text.replace(
        'classpath "org.jetbrains.kotlin:kotlin-gradle-plugin:$kotlin_version"',
        'classpath "org.jetbrains.kotlin:kotlin-gradle-plugin:$kotlin_version"\n        classpath "org.jetbrains.kotlin.plugin.compose:org.jetbrains.kotlin.plugin.compose.gradle.plugin:$kotlin_version"'
    )
root_text = root_text.replace("        google()\n        mavenCentral()", "        google()\n        gradlePluginPortal()\n        mavenCentral()")
root_gradle.write_text(root_text, encoding="utf-8")

app_gradle = app / "build.gradle"
app_text = app_gradle.read_text(encoding="utf-8")
app_text = app_text.replace("apply plugin: 'kotlin-android'", "apply plugin: 'kotlin-android'\napply plugin: 'org.jetbrains.kotlin.plugin.compose'")
app_text = app_text.replace("    kotlinOptions {\n        jvmTarget = '21'\n    }", "    compileOptions {\n        sourceCompatibility JavaVersion.VERSION_21\n        targetCompatibility JavaVersion.VERSION_21\n    }\n\n    kotlinOptions {\n        jvmTarget = '21'\n    }")
app_text = app_text.replace("        buildConfig true", "        buildConfig true\n        compose true")
app_text = re.sub(r"\nrepositories\s*\{\s*flatDir\s*\{.*?\n\}\s*\n", "\n", app_text, flags=re.S)
app_text = app_text.replace("    implementation project(':capacitor-android')\n", "")
app_text = app_text.replace("    implementation project(':capacitor-cordova-android-plugins')\n", "")
app_text = app_text.replace("\napply from: 'capacitor.build.gradle'\n", "\n")
deps = '''
    implementation platform("androidx.compose:compose-bom:2025.05.01")
    implementation "androidx.activity:activity-compose:1.10.1"
    implementation "androidx.compose.ui:ui"
    implementation "androidx.compose.ui:ui-tooling-preview"
    implementation "androidx.compose.material3:material3"
    implementation "androidx.compose.material:material-icons-extended"
    implementation "androidx.lifecycle:lifecycle-runtime-ktx:2.9.0"
    debugImplementation "androidx.compose.ui:ui-tooling"
    debugImplementation "androidx.compose.ui:ui-test-manifest"
'''
if "androidx.compose:compose-bom" not in app_text:
    app_text = app_text.replace("dependencies {", "dependencies {" + deps, 1)
if any(x in app_text for x in ("capacitor-android", "capacitor-cordova", "capacitor.build.gradle")):
    raise SystemExit("Capacitor dependency remains in app/build.gradle")
app_gradle.write_text(app_text, encoding="utf-8")
(android / "settings.gradle").write_text("include ':app'\n", encoding="utf-8")

shutil.rmtree(java / "plugin", ignore_errors=True)
for rel in (
    "capacitor-cordova-android-plugins", "app/capacitor.build.gradle", "capacitor.settings.gradle",
    "app/src/main/assets/public", "app/src/main/assets/capacitor.config.json",
    "app/src/main/assets/capacitor.plugins.json", "app/src/main/res/layout/activity_main.xml"
):
    p = android / rel
    if p.is_dir(): shutil.rmtree(p)
    elif p.exists(): p.unlink()
assets = app / "src/main/assets"
if assets.exists() and not any(assets.iterdir()): assets.rmdir()

manifest = app / "src/main/AndroidManifest.xml"
m = manifest.read_text(encoding="utf-8")
m = m.replace("<!-- WebView (local assets only; no network services are contacted by the core app) -->",
              "<!-- Network access is used only for a user-configured external AI provider. -->")
m = re.sub(r'\s+android:configChanges="[^"]*"', "", m, count=1)
manifest.write_text(m, encoding="utf-8")

test = app / "src/test/java/com/dayflow/app/NativeLogicTest.kt"
if test.exists():
    t = test.read_text(encoding="utf-8")
    old = 'PrayerEngine.KEYS.associateWith { 0 }, 10, true, true, "", null, 0.9, true)'
    new = 'PrayerEngine.KEYS.associateWith { 0 }, 10, true, true, "", null, null, 0.9, true)'
    if old in t: test.write_text(t.replace(old, new), encoding="utf-8")
    elif new not in t: raise SystemExit("Unexpected PrayerEngine.Cfg test fixture")

for p in (app / "src/main").rglob("*.kt"):
    if p.name == "NativeDayflowApp.kt": continue
    s = p.read_text(encoding="utf-8")
    if any(x in s for x in ("BridgeActivity", "com.getcapacitor", "DayflowNativePlugin")):
        raise SystemExit(f"Capacitor code remains: {p.relative_to(root)}")
for p in (app_gradle, android / "settings.gradle"):
    if "capacitor" in p.read_text(encoding="utf-8").lower():
        raise SystemExit(f"Capacitor Gradle setting remains: {p.relative_to(root)}")
print("NATIVE_COMPOSE_MIGRATION_APPLIED")
print(f"Compose UI source: {len(compose.encode('utf-8'))} bytes")
print("Preserved native SQLite, alarm engine, receivers, audio service, notifications, backup and AppWidgets")
