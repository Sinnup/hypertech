"""
Mobile Coder agent — generates a complete Android/Kotlin/Jetpack Compose project.

POC scenario: produces all source files for a minimal runnable Android app,
saves them to poc/{ticket_id}/android/, and instructs the user how to build
the APK locally via Gradle.

Demo scenario: Android TPV (card-payment terminal) — button press simulates
card insertion → processing animation → "Payment Approved" result.
"""

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, strip_fences, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = """You are an expert Android developer using Kotlin and Jetpack Compose.
Your job is to generate a complete, minimal, immediately buildable Android project.

Rules:
- Use Jetpack Compose for all UI — no XML layouts.
- Target Android API 24+ (minSdk 24, compileSdk 34, targetSdk 34).
- Use Material Design 3 components (androidx.compose.material3).
- Every file must be complete — no placeholders, no "..." ellipses.
- Keep code concise; this is a POC, not production.

Respond with a JSON object where each key is a file path (relative to the
project root) and each value is the full file content as a string.
Include exactly these files:
  - settings.gradle.kts
  - build.gradle.kts  (root)
  - app/build.gradle.kts
  - app/src/main/AndroidManifest.xml
  - app/src/main/java/com/hypertech/poc/MainActivity.kt
  - app/src/main/res/values/strings.xml
  - app/src/main/res/values/themes.xml
  - gradle/wrapper/gradle-wrapper.properties
  - gradlew  (POSIX shell launcher — stub that prints build instructions)

Return raw JSON only, no markdown fences."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "App description: {prompt}\n\n"
        "Tech stack: {tech_stack}\n\n"
        "Design brief summary: {design_summary}\n\n"
        "Additional requirements: {extra}"
    )),
])

# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------

@register(
    "coder_mobile",
    description="Generates Android Kotlin/Jetpack Compose project files for POC/demo",
    tier=ModelTier.BALANCED,
    tags=["code-generation", "android", "mobile"],
)
@observe(name="coder-mobile-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    design_brief = state.get("design_brief") or {}
    hld = state.get("hld_output") or {}
    synthesis = state.get("design_synthesis") or {}

    slack.status(ticket_id, "📱 Mobile Coder started — generating Android project...")

    # Build rich context from upstream agents if available
    tech_stack = _extract_tech_stack(hld, synthesis)
    design_summary = _extract_design_summary(design_brief, synthesis)
    extra = _extract_extra_requirements(state)

    client = get_client()
    if client:
        client.update_current_span(input={"prompt": prompt, "tech_stack": tech_stack})

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.2)
    chain = _PROMPT | llm

    slack.status(ticket_id, "⚙️ Generating Android project files...")
    result = chain.invoke({
        "prompt": prompt,
        "tech_stack": tech_stack,
        "design_summary": design_summary,
        "extra": extra,
    })

    record_generation(get_model_id(ModelTier.BALANCED), result, output={"chars": len(result.content)})

    # Parse the file map from LLM output
    file_map = _parse_file_map(result.content)
    if not file_map:
        # Fallback: generate the TPV demo project statically
        slack.status(ticket_id, "⚠️ LLM file parse failed — using TPV demo template")
        file_map = _tpv_demo_template(prompt)

    # Write files to poc/{ticket_id}/android/
    project_dir = Path(f"poc/{ticket_id}/android")
    project_dir.mkdir(parents=True, exist_ok=True)
    files_written = []

    for rel_path, content in file_map.items():
        target = project_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        files_written.append(str(target))

    # Make gradlew executable
    gradlew = project_dir / "gradlew"
    if gradlew.exists():
        gradlew.chmod(0o755)

    slack.status(
        ticket_id,
        f"✅ Android project ready — {len(files_written)} files in `poc/{ticket_id}/android/`"
    )
    _post_build_instructions(ticket_id, project_dir)

    registry_store.update_ticket(ticket_id, {
        "status": "code_generated",
        "agents_involved": _agents_so_far(state) + ["coder_mobile"],
    })

    # Write state
    coder_output = {
        "project_type": "android",
        "project_dir": str(project_dir.resolve()),
        "files": files_written,
        "build_cmd": f"cd {project_dir.resolve()} && ./gradlew assembleDebug",
        "apk_path": str(project_dir.resolve() / "app/build/outputs/apk/debug/app-debug.apk"),
    }
    state["coder_output"] = coder_output
    state["current_agent"] = "coder_mobile"
    state["next_agent"] = "infra"
    state["status"] = "code_generated"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["coder_mobile"] = AgentOutput(
        status="ok",
        data=coder_output,
        confidence=0.85,
        agent_name="coder_mobile",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("coder_mobile", "infra", "code_ready", ticket_id, coder_output)
    )

    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tech_stack(hld: dict, synthesis: dict) -> str:
    if synthesis.get("tech_stack"):
        return json.dumps(synthesis["tech_stack"])
    if hld.get("tech_stack"):
        return json.dumps(hld["tech_stack"])
    return "Android (Kotlin, Jetpack Compose, Material 3)"


def _extract_design_summary(brief: dict, synthesis: dict) -> str:
    if synthesis.get("design_summary"):
        return synthesis["design_summary"]
    screens = brief.get("screens", [])
    if screens:
        names = [s.get("name", "?") for s in screens]
        return f"Screens: {', '.join(names)}"
    return "Single-screen TPV app"


def _extract_extra_requirements(state: PipelineState) -> str:
    parts = []
    compliance = state.get("compliance_report") or {}
    gaps = compliance.get("compliance_gaps", [])
    if gaps:
        parts.append("Compliance gaps to address: " + "; ".join(g.get("gap", "") for g in gaps[:3]))
    summary = state.get("current_summary")
    if summary:
        parts.append("Context from prior agents:\n" + summary)
    return "\n".join(parts) or "None"


def _agents_so_far(state: PipelineState) -> list[str]:
    return list(state.get("agent_outputs", {}).keys())


def _parse_file_map(content: str) -> Optional[dict]:
    """Try to parse a JSON file map from LLM output."""
    cleaned = strip_fences(content).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _post_build_instructions(ticket_id: str, project_dir: Path) -> None:
    """Post build instructions to Slack."""
    abs_path = project_dir.resolve()
    instructions = textwrap.dedent(f"""
        🔨 *Build the Android APK:*
        ```
        cd {abs_path}
        ./gradlew assembleDebug
        ```
        APK will be at:
        `{abs_path}/app/build/outputs/apk/debug/app-debug.apk`

        📲 *Install on device/emulator:*
        ```
        adb install {abs_path}/app/build/outputs/apk/debug/app-debug.apk
        ```
    """).strip()
    slack.status(ticket_id, instructions)


# ---------------------------------------------------------------------------
# Static TPV demo template (fallback)
# ---------------------------------------------------------------------------

def _tpv_demo_template(prompt: str) -> dict[str, str]:
    """Return a hardcoded minimal Android TPV project as a file map.

    This is the fallback when the LLM doesn't produce parseable JSON.
    Produces a real, immediately buildable Jetpack Compose TPV app.
    """
    app_name = "HyperTPV"
    package = "com.hypertech.poc"
    package_path = package.replace(".", "/")

    return {
        "settings.gradle.kts": textwrap.dedent(f"""\
            pluginManagement {{
                repositories {{
                    google()
                    mavenCentral()
                    gradlePluginPortal()
                }}
            }}
            dependencyResolutionManagement {{
                repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
                repositories {{
                    google()
                    mavenCentral()
                }}
            }}
            rootProject.name = "{app_name}"
            include(":app")
        """),

        "build.gradle.kts": textwrap.dedent("""\
            plugins {
                id("com.android.application") version "8.2.0" apply false
                id("org.jetbrains.kotlin.android") version "1.9.20" apply false
            }
        """),

        "app/build.gradle.kts": textwrap.dedent(f"""\
            plugins {{
                id("com.android.application")
                id("org.jetbrains.kotlin.android")
            }}

            android {{
                namespace = "{package}"
                compileSdk = 34

                defaultConfig {{
                    applicationId = "{package}"
                    minSdk = 24
                    targetSdk = 34
                    versionCode = 1
                    versionName = "1.0"
                }}

                buildFeatures {{
                    compose = true
                }}

                composeOptions {{
                    kotlinCompilerExtensionVersion = "1.5.4"
                }}

                compileOptions {{
                    sourceCompatibility = JavaVersion.VERSION_1_8
                    targetCompatibility = JavaVersion.VERSION_1_8
                }}

                kotlinOptions {{
                    jvmTarget = "1.8"
                }}
            }}

            dependencies {{
                val composeBom = platform("androidx.compose:compose-bom:2024.01.00")
                implementation(composeBom)
                implementation("androidx.compose.ui:ui")
                implementation("androidx.compose.material3:material3")
                implementation("androidx.compose.ui:ui-tooling-preview")
                implementation("androidx.activity:activity-compose:1.8.2")
                debugImplementation("androidx.compose.ui:ui-tooling")
            }}
        """),

        f"app/src/main/AndroidManifest.xml": textwrap.dedent(f"""\
            <?xml version="1.0" encoding="utf-8"?>
            <manifest xmlns:android="http://schemas.android.com/apk/res/android">
                <application
                    android:allowBackup="true"
                    android:label="@string/app_name"
                    android:theme="@style/Theme.{app_name}"
                    android:supportsRtl="true">
                    <activity
                        android:name=".MainActivity"
                        android:exported="true">
                        <intent-filter>
                            <action android:name="android.intent.action.MAIN" />
                            <category android:name="android.intent.category.LAUNCHER" />
                        </intent-filter>
                    </activity>
                </application>
            </manifest>
        """),

        f"app/src/main/java/{package_path}/MainActivity.kt": textwrap.dedent(f"""\
            package {package}

            import android.os.Bundle
            import androidx.activity.ComponentActivity
            import androidx.activity.compose.setContent
            import androidx.compose.animation.AnimatedContent
            import androidx.compose.animation.core.*
            import androidx.compose.foundation.layout.*
            import androidx.compose.material3.*
            import androidx.compose.runtime.*
            import androidx.compose.ui.Alignment
            import androidx.compose.ui.Modifier
            import androidx.compose.ui.graphics.Color
            import androidx.compose.ui.text.font.FontWeight
            import androidx.compose.ui.unit.dp
            import androidx.compose.ui.unit.sp
            import kotlinx.coroutines.delay

            class MainActivity : ComponentActivity() {{
                override fun onCreate(savedInstanceState: Bundle?) {{
                    super.onCreate(savedInstanceState)
                    setContent {{
                        MaterialTheme {{
                            Surface(
                                modifier = Modifier.fillMaxSize(),
                                color = MaterialTheme.colorScheme.background
                            ) {{
                                PaymentScreen()
                            }}
                        }}
                    }}
                }}
            }}

            enum class PaymentState {{ IDLE, INSERTING, PROCESSING, APPROVED, DECLINED }}

            @Composable
            fun PaymentScreen() {{
                var state by remember {{ mutableStateOf(PaymentState.IDLE) }}
                var amount by remember {{ mutableStateOf("$150.00 MXN") }}
                val infiniteTransition = rememberInfiniteTransition(label = "pulse")
                val alpha by infiniteTransition.animateFloat(
                    initialValue = 0.3f, targetValue = 1f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(800),
                        repeatMode = RepeatMode.Reverse,
                    ),
                    label = "alpha"
                )

                LaunchedEffect(state) {{
                    when (state) {{
                        PaymentState.INSERTING -> {{
                            delay(1500)
                            state = PaymentState.PROCESSING
                        }}
                        PaymentState.PROCESSING -> {{
                            delay(2000)
                            state = PaymentState.APPROVED
                        }}
                        else -> {{}}
                    }}
                }}

                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.SpaceBetween
                ) {{
                    // Header
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {{
                        Text(
                            "HyperTPV",
                            fontSize = 28.sp,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary
                        )
                        Text("Terminal de Pago", fontSize = 14.sp, color = Color.Gray)
                    }}

                    // Amount
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {{
                        Text("Cobrar", fontSize = 16.sp, color = Color.Gray)
                        Text(
                            amount,
                            fontSize = 48.sp,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onBackground
                        )
                    }}

                    // Status card
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(
                            containerColor = when (state) {{
                                PaymentState.APPROVED -> Color(0xFF4CAF50).copy(alpha = 0.15f)
                                PaymentState.DECLINED -> Color(0xFFF44336).copy(alpha = 0.15f)
                                PaymentState.PROCESSING, PaymentState.INSERTING ->
                                    MaterialTheme.colorScheme.surfaceVariant
                                else -> MaterialTheme.colorScheme.surfaceVariant
                            }}
                        )
                    ) {{
                        Column(
                            modifier = Modifier.padding(24.dp),
                            horizontalAlignment = Alignment.CenterHorizontally
                        ) {{
                            AnimatedContent(targetState = state, label = "status") {{ s ->
                                when (s) {{
                                    PaymentState.IDLE ->
                                        Text("Esperando tarjeta...", fontSize = 18.sp)
                                    PaymentState.INSERTING ->
                                        Text(
                                            "Insertando tarjeta...",
                                            fontSize = 18.sp,
                                            color = MaterialTheme.colorScheme.primary.copy(alpha = alpha)
                                        )
                                    PaymentState.PROCESSING ->
                                        Column(horizontalAlignment = Alignment.CenterHorizontally) {{
                                            CircularProgressIndicator()
                                            Spacer(Modifier.height(12.dp))
                                            Text("Procesando pago...", fontSize = 18.sp)
                                        }}
                                    PaymentState.APPROVED ->
                                        Column(horizontalAlignment = Alignment.CenterHorizontally) {{
                                            Text("✅", fontSize = 48.sp)
                                            Text(
                                                "¡Pago Aprobado!",
                                                fontSize = 22.sp,
                                                fontWeight = FontWeight.Bold,
                                                color = Color(0xFF4CAF50)
                                            )
                                            Text("Transacción autorizada", fontSize = 14.sp, color = Color.Gray)
                                        }}
                                    PaymentState.DECLINED ->
                                        Column(horizontalAlignment = Alignment.CenterHorizontally) {{
                                            Text("❌", fontSize = 48.sp)
                                            Text(
                                                "Pago Declinado",
                                                fontSize = 22.sp,
                                                fontWeight = FontWeight.Bold,
                                                color = Color(0xFFF44336)
                                            )
                                        }}
                                }}
                            }}
                        }}
                    }}

                    // Action button
                    when (state) {{
                        PaymentState.IDLE ->
                            Button(
                                onClick = {{ state = PaymentState.INSERTING }},
                                modifier = Modifier.fillMaxWidth().height(56.dp),
                                shape = MaterialTheme.shapes.medium
                            ) {{
                                Text("💳  Insertar Tarjeta", fontSize = 18.sp)
                            }}
                        PaymentState.APPROVED, PaymentState.DECLINED ->
                            OutlinedButton(
                                onClick = {{ state = PaymentState.IDLE }},
                                modifier = Modifier.fillMaxWidth().height(56.dp)
                            ) {{
                                Text("Nueva Transacción", fontSize = 16.sp)
                            }}
                        else -> Spacer(Modifier.height(56.dp))
                    }}
                }}
            }}
        """),

        "app/src/main/res/values/strings.xml": textwrap.dedent(f"""\
            <resources>
                <string name="app_name">{app_name}</string>
            </resources>
        """),

        "app/src/main/res/values/themes.xml": textwrap.dedent(f"""\
            <resources>
                <style name="Theme.{app_name}" parent="Theme.Material3.DayNight.NoActionBar" />
            </resources>
        """),

        "gradle/wrapper/gradle-wrapper.properties": textwrap.dedent("""\
            distributionBase=GRADLE_USER_HOME
            distributionPath=wrapper/dists
            distributionUrl=https\\://services.gradle.org/distributions/gradle-8.4-bin.zip
            zipStoreBase=GRADLE_USER_HOME
            zipStorePath=wrapper/dists
        """),

        "gradlew": textwrap.dedent("""\
            #!/bin/sh
            # Gradle wrapper stub — replace with full wrapper after running:
            #   gradle wrapper --gradle-version 8.4
            echo "To build: gradle assembleDebug (or use Android Studio)"
            exec gradle "$@"
        """),
    }
