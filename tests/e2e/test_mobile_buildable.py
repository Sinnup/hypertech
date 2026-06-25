"""
Regression test for HT-CB5971: generated Android projects must be buildable.

Real APK builds failed twice on LLM-generated projects:
  * missing gradle.properties (android.useAndroidX) → checkDebugAarMetadata
  * dangling @mipmap/ic_launcher with no asset      → processDebugResources

`_ensure_buildable` normalizes the file map so `./gradlew assembleDebug` works.
"""

from agents.coder_mobile.agent import _ensure_buildable

_MANIFEST_WITH_ICON = (
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
    '    <application android:icon="@mipmap/ic_launcher" android:label="@string/app_name">\n'
    '    </application>\n</manifest>\n'
)


def test_adds_gradle_properties_with_useandroidx():
    fm = _ensure_buildable({"app/build.gradle.kts": "plugins {}"})
    assert "gradle.properties" in fm
    assert "android.useAndroidX=true" in fm["gradle.properties"]


def test_preserves_existing_gradle_properties_but_adds_flag():
    fm = _ensure_buildable({"gradle.properties": "org.gradle.jvmargs=-Xmx1g\n"})
    assert "android.useAndroidX=true" in fm["gradle.properties"]
    assert "org.gradle.jvmargs" in fm["gradle.properties"]


def test_strips_icon_when_no_asset():
    fm = _ensure_buildable({"app/src/main/AndroidManifest.xml": _MANIFEST_WITH_ICON})
    assert "@mipmap/ic_launcher" not in fm["app/src/main/AndroidManifest.xml"]


def test_keeps_icon_when_asset_present():
    fm = _ensure_buildable({
        "app/src/main/AndroidManifest.xml": _MANIFEST_WITH_ICON,
        "app/src/main/res/mipmap-hdpi/ic_launcher.png": "<binary>",
    })
    assert "@mipmap/ic_launcher" in fm["app/src/main/AndroidManifest.xml"]
