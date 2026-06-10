"""OpenGL-based transparent visual widget for Ghost Jarvis.

Renders a diamond of emissive cubes with high-tech holographic effects:
- Fresnel edge glow, scanlines, chromatic aberration
- Orbital particles, neural connection lines
- Holographic grid floor, expansion rings
- Real audio spectrum reactivity
"""
import colorsys
import math
import logging
import random
import time as _time
import numpy as np
from OpenGL.GL import *
from OpenGL.GL.shaders import compileProgram, compileShader

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtGui import QMatrix4x4, QVector3D

from config import APP_CONFIG

logger = logging.getLogger("visual")

# visual_quality → particle count mapping
_QUALITY_PARTICLES = {"low": 16, "medium": 32, "high": 64}

# ---------------------------------------------------------------------------
# GLSL shaders — High-tech holographic style
# ---------------------------------------------------------------------------
_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;

uniform mat4 uMVP;
uniform float uScale;
uniform float uTime;
uniform float uBreathAmp;
uniform float uDisplacement;

out vec3 vWorldPos;
out vec3 vNormal;
out vec3 vLocalPos;
out float vDistFromCenter;

void main() {
    vec3 p = aPos * uScale;
    float breath = sin(uTime * 1.5 + aPos.x * 2.0 + aPos.y * 2.0) * uBreathAmp;
    p += aNormal * (breath + uDisplacement) * uScale;
    vWorldPos = p;
    vNormal = aNormal;
    vLocalPos = aPos;
    vDistFromCenter = length(p.xy);
    gl_Position = uMVP * vec4(p, 1.0);
}
"""

_FRAG = """
#version 330 core
in vec3 vWorldPos;
in vec3 vNormal;
in vec3 vLocalPos;
in float vDistFromCenter;

uniform vec3 uColor;
uniform vec3 uColor2;
uniform float uGlow;
uniform float uTime;
uniform float uScanlineIntensity;
uniform float uGlitchIntensity;
uniform float uFresnelPower;
uniform float uEdgeWidth;
uniform float uGlassMode;
uniform float uAlphaMul;

out vec4 FragColor;

void main() {
    // Base color with glow
    vec3 c = uColor * (0.28 + uGlow * 3.2);
    float a = 0.78 + uGlow * 0.22;

    // Fresnel edge glow
    vec3 viewDir = normalize(-vWorldPos);
    float fresnel = pow(1.0 - abs(dot(viewDir, vNormal)), uFresnelPower);
    c += uColor * fresnel * uGlow * 2.0;
    a += fresnel * 0.15;

    // Subtle iridescence: drift toward a hue-rotated sibling at grazing angles
    c += (uColor2 - uColor) * pow(fresnel, 1.5) * 0.25;

    // Beveled edge: distance to the nearest cube edge in (undisplaced)
    // local space. Near an edge the two largest |coords| approach 0.5,
    // so the median component approaches 1 after the *2 normalization.
    vec3 q = abs(vLocalPos) * 2.0;
    float mx = max(q.x, max(q.y, q.z));
    float mn = min(q.x, min(q.y, q.z));
    float med = q.x + q.y + q.z - mx - mn;
    float edgeDist = 1.0 - med;
    // Clamp: huge derivatives at silhouette pixels cause color sparkles
    float w = clamp(fwidth(edgeDist) * 1.5, 0.004, 0.05);
    float edge = 1.0 - smoothstep(uEdgeWidth - w, uEdgeWidth + w, edgeDist);

    // Glass mode: faint faces, the edges carry the form
    if (uGlassMode > 0.5) {
        c *= 0.55;
        // Vidrio ahumado: cobertura extra (0.25) SIN color añadido, para
        // que el cristal oscurezca fondos claros detrás del overlay.
        // Sobre fondos oscuros es invisible (el alpha solo tapa al dst).
        a = 0.25 + a * 0.22 + fresnel * 0.26;
    }
    // HDR edges (>1) feed the bloom bright-pass
    c += mix(uColor, vec3(1.0), 0.35) * edge * (1.3 + uGlow * 1.8);
    a += edge * (uGlassMode > 0.5 ? 0.65 : 0.30);

    // Soft top light
    c *= 0.92 + 0.16 * clamp(vLocalPos.y + 0.5, 0.0, 1.0);

    // Scanlines
    if (uScanlineIntensity > 0.0) {
        float scan = sin(vWorldPos.y * 80.0 + uTime * 3.0) * 0.5 + 0.5;
        float scanline = pow(scan, 8.0) * uScanlineIntensity;
        c += uColor * scanline * 0.4;
    }

    // Chromatic aberration / glitch
    if (uGlitchIntensity > 0.0) {
        float glitch = sin(vWorldPos.x * 50.0 + uTime * 20.0) * cos(vWorldPos.y * 40.0 - uTime * 15.0);
        glitch = pow(abs(glitch), 4.0) * uGlitchIntensity;
        c.r += glitch * 0.3;
        c.b -= glitch * 0.2;
    }

    // Center brightening
    float centerBoost = 1.0 - clamp(vDistFromCenter / 2.5, 0.0, 1.0);
    c += uColor * centerBoost * uGlow * 0.5;

    FragColor = vec4(c, clamp(a, 0.0, 1.0) * uAlphaMul);
}
"""

# Particle vertex shader
_PARTICLE_VERT = """
#version 330 core
layout(location = 0) in vec2 aOffset;
layout(location = 1) in float aPhase;

uniform mat4 uMVP;
uniform float uTime;
uniform float uParticleSize;
uniform float uStateIntensity;

out float vAlpha;

void main() {
    float angle = aPhase + uTime * (0.5 + aPhase * 0.3);
    float radius = 1.8 + sin(uTime * 0.7 + aPhase * 5.0) * 0.3;
    float z = sin(uTime * 1.2 + aPhase * 3.0) * 0.5;
    vec3 pos = vec3(cos(angle) * radius + aOffset.x, sin(angle) * radius + aOffset.y, z);
    vAlpha = 0.4 + 0.6 * sin(uTime * 2.0 + aPhase * 10.0) * uStateIntensity;
    gl_Position = uMVP * vec4(pos, 1.0);
    gl_PointSize = uParticleSize * (1.0 + 0.5 * sin(uTime * 3.0 + aPhase * 7.0));
}
"""

_PARTICLE_FRAG = """
#version 330 core
in float vAlpha;
uniform vec3 uColor;

out vec4 FragColor;

void main() {
    float dist = length(gl_PointCoord - vec2(0.5));
    if (dist > 0.5) discard;
    float glow = pow(1.0 - smoothstep(0.0, 0.5, dist), 1.4);
    FragColor = vec4(uColor, vAlpha * glow);
}
"""

# Grid floor shader
_GRID_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
uniform mat4 uMVP;
uniform float uTime;

out vec2 vUV;
out float vFade;

void main() {
    vUV = aPos.xz * 2.0;
    vFade = 1.0 - smoothstep(0.0, 4.0, length(aPos.xz));
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

_GRID_FRAG = """
#version 330 core
in vec2 vUV;
in float vFade;
uniform vec3 uColor;
uniform float uTime;
uniform float uIntensity;

out vec4 FragColor;

void main() {
    vec2 grid = abs(fract(vUV - 0.5) - 0.5) / fwidth(vUV);
    float line = min(grid.x, grid.y);
    float pattern = 1.0 - min(line, 1.0);
    float pulse = sin(vUV.x * 10.0 + uTime * 2.0) * sin(vUV.y * 10.0 + uTime * 1.5) * 0.1;
    vec3 c = uColor * (pattern + pulse) * uIntensity;
    float a = pattern * vFade * 0.5 * uIntensity;
    FragColor = vec4(c, a);
}
"""

# Ring expansion shader
_RING_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
uniform mat4 uMVP;

out vec2 vUV;

void main() {
    vUV = aPos.xy;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

_RING_FRAG = """
#version 330 core
in vec2 vUV;
uniform vec3 uColor;
uniform float uProgress;
uniform float uIntensity;

out vec4 FragColor;

void main() {
    float dist = length(vUV);
    float ring = smoothstep(uProgress, uProgress - 0.05, dist) * smoothstep(uProgress - 0.15, uProgress - 0.05, dist);
    float fade = 1.0 - smoothstep(0.0, 1.5, dist);
    FragColor = vec4(uColor, ring * fade * uIntensity);
}
"""

# ---------------------------------------------------------------------------
# Post-processing (real bloom): fullscreen triangle + bright-pass +
# separable gaussian blur + composite copy.
# ---------------------------------------------------------------------------
_FS_VERT = """
#version 330 core
out vec2 vUV;
void main() {
    // Fullscreen triangle from gl_VertexID (no VBO; a dummy VAO is bound)
    vec2 pos = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    vUV = pos;
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"""

_BRIGHT_FRAG = """
#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform float uThreshold;
out vec4 FragColor;
void main() {
    vec4 s = texture(uTex, vUV);
    float luma = dot(s.rgb, vec3(0.2126, 0.7152, 0.0722));
    // Soft knee around the threshold
    float knee = uThreshold * 0.5;
    float soft = clamp((luma - uThreshold + knee) / (2.0 * knee), 0.0, 1.0);
    float contrib = max(luma - uThreshold, 0.0) + soft * soft * knee;
    float w = contrib / max(luma, 1e-4);
    FragColor = vec4(s.rgb * w, s.a * w);
}
"""

_BLUR_FRAG = """
#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform vec2 uDir;   // (1/w, 0) horizontal or (0, 1/h) vertical
out vec4 FragColor;
void main() {
    // 9-tap separable gaussian
    vec4 c = texture(uTex, vUV) * 0.227027;
    c += (texture(uTex, vUV + uDir * 1.0) + texture(uTex, vUV - uDir * 1.0)) * 0.194594;
    c += (texture(uTex, vUV + uDir * 2.0) + texture(uTex, vUV - uDir * 2.0)) * 0.121622;
    c += (texture(uTex, vUV + uDir * 3.0) + texture(uTex, vUV - uDir * 3.0)) * 0.054054;
    c += (texture(uTex, vUV + uDir * 4.0) + texture(uTex, vUV - uDir * 4.0)) * 0.016216;
    FragColor = c;
}
"""

_COPY_FRAG = """
#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTonemap;
out vec4 FragColor;
void main() {
    vec4 s = texture(uTex, vUV);
    // Optional Reinhard: compress HDR bloom peaks so they don't clip
    // per-channel (which reads as hue shifts / color sparkles).
    s.rgb = mix(s.rgb, s.rgb / (1.0 + max(s.rgb, vec3(0.0))), uTonemap);
    FragColor = s * uIntensity;
}
"""

# Cube geometry with correct per-face normals (36 vertices, no index buffer needed)
def _build_cube():
    h = 0.5
    # Each tuple: (list of 2 triangles as vertex triples, outward face normal)
    faces = [
        ([(-h,-h,-h),(-h, h,-h),( h, h,-h), ( h, h,-h),( h,-h,-h),(-h,-h,-h)], ( 0, 0,-1)),  # Back
        ([(-h,-h, h),( h,-h, h),( h, h, h), ( h, h, h),(-h, h, h),(-h,-h, h)], ( 0, 0, 1)),  # Front
        ([(-h,-h,-h),(-h,-h, h),(-h, h, h), (-h, h, h),(-h, h,-h),(-h,-h,-h)], (-1, 0, 0)),  # Left
        ([( h,-h, h),( h,-h,-h),( h, h,-h), ( h, h,-h),( h, h, h),( h,-h, h)], ( 1, 0, 0)),  # Right
        ([(-h,-h,-h),( h,-h,-h),( h,-h, h), ( h,-h, h),(-h,-h, h),(-h,-h,-h)], ( 0,-1, 0)),  # Bottom
        ([(-h, h, h),( h, h, h),( h, h,-h), ( h, h,-h),(-h, h,-h),(-h, h, h)], ( 0, 1, 0)),  # Top
    ]
    verts, norms = [], []
    for face_verts, n in faces:
        for v in face_verts:
            verts.append(v)
            norms.append(n)
    return np.array(verts, dtype=np.float32), np.array(norms, dtype=np.float32)

_CUBE_VERTS, _CUBE_NORMALS = _build_cube()
_CUBE_VERT_COUNT = 36

# Diamond layout: 1-2-3-2-1 = 9 cubes
_DIAMOND_ROWS = [
    [0],
    [-1, 1],
    [-2, 0, 2],
    [-1, 1],
    [0],
]

# Plano del piso (grid + reflejo) y encuadre de cámara. A -2.0 el reflejo
# proyectaba fuera del cuadro; a -1.35 queda anclado bajo el diamante.
_FLOOR_Y = -1.35
_CAM_Z = 6.4

# High-tech color palette
_COLORS = {
    "IDLE":    QVector3D(0.00, 0.83, 1.00),  # Cyan neon
    "WAKE":    QVector3D(1.00, 1.00, 1.00),  # White flash
    "LISTENING": QVector3D(0.00, 1.00, 0.53),  # Green mint
    "PROCESSING": QVector3D(1.00, 0.42, 0.00),  # Amber/orange
    "SPEAKING": QVector3D(0.00, 0.83, 1.00),  # Cyan
    "STANDBY": QVector3D(1.00, 0.20, 0.20),  # Red
}

_HALO_COLORS = {
    "IDLE":    QVector3D(0.00, 0.50, 0.60),
    "WAKE":    QVector3D(0.80, 0.90, 1.00),
    "LISTENING": QVector3D(0.00, 0.60, 0.30),
    "PROCESSING": QVector3D(0.80, 0.30, 0.00),
    "SPEAKING": QVector3D(0.00, 0.50, 0.60),
    "STANDBY": QVector3D(0.60, 0.10, 0.10),
}


def _hue_shift(v: QVector3D, deg: float) -> QVector3D:
    h, l, s = colorsys.rgb_to_hls(v.x(), v.y(), v.z())
    r, g, b = colorsys.hls_to_rgb((h + deg / 360.0) % 1.0, l, s)
    return QVector3D(r, g, b)


# Iridescent sibling per state (hue +18°) for the fresnel color drift
_COLORS2 = {k: _hue_shift(v, 18.0) for k, v in _COLORS.items()}


class Cube:
    __slots__ = ("x", "y", "z_base", "idx", "phase", "base_color", "halo_color")

    def __init__(self, x, y, z_base, idx):
        self.x = x
        self.y = y
        self.z_base = z_base
        self.idx = idx
        self.phase = random.random() * math.pi * 2
        self.base_color = _COLORS["IDLE"]
        self.halo_color = _HALO_COLORS["IDLE"]


class VisualGLWidget(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAutoFillBackground(False)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

        self._state = "IDLE"
        self._audio_vol = 0.0
        self._speech_vol = 0.0
        self._spectrum = [0.0] * 8
        self._time = 0.0
        self._last_tick = _time.perf_counter()

        # Valores suavizados por estado: los colores e intensidades hacen
        # cross-fade (~180 ms) en vez de brincar al cambiar de estado.
        self._color_cur = QVector3D(_COLORS["IDLE"])
        self._halo_cur = QVector3D(_HALO_COLORS["IDLE"])
        self._color2_cur = QVector3D(_COLORS2["IDLE"])
        self._scan_cur = 0.0
        self._glitch_cur = 0.0
        self._fresnel_cur = 3.0
        # Envolvente del eco "descuadrado" (contorno fantasma en PROCESSING)
        self._echo_cur = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        fps = max(15, min(int(APP_CONFIG.visual_fps or 60), 144))
        self._timer.start(max(1, int(1000 / fps)))

        self._cubes: list[Cube] = []
        self._init_cubes()

        # Particle data — count from visual_quality
        self._particle_count = _QUALITY_PARTICLES.get(APP_CONFIG.visual_quality, 64)
        self._particle_offsets = np.random.randn(self._particle_count, 2).astype(np.float32) * 0.3
        self._particle_phases = np.random.rand(self._particle_count).astype(np.float32) * math.pi * 2

        # Expansion rings
        self._rings: list[dict] = []  # {birth: float, color: QVector3D}
        self._last_ring_time = -10.0

        # GL objects
        self._vbos: list = []
        self._prog = 0
        self._particle_prog = 0
        self._grid_prog = 0
        self._ring_prog = 0
        self._vao = 0
        self._particle_vao = 0
        self._grid_vao = 0
        self._ring_vao = 0
        self._uni = {}
        self._puni = {}
        self._guni = {}
        self._runi = {}

        # Bloom post-processing state. _bloom_ok=False ⇒ legacy halo path.
        self._bloom_ok = False
        self._fb_size = (0, 0)       # last resizeGL size (viewport convention)
        self._bloom_size = (0, 0)    # size the FBO targets were allocated at
        self._blur_size = (1, 1)
        self._scene_fbo = 0
        self._scene_tex = 0
        self._scene_depth = 0
        self._ping_fbo = [0, 0]
        self._ping_tex = [0, 0]
        self._fs_vao = 0
        self._bright_prog = 0
        self._blur_prog = 0
        self._copy_prog = 0
        self._buni = {}
        self._bluni = {}
        self._cuni = {}

        # Precomputed matrices
        self._proj = QMatrix4x4()
        self._view = QMatrix4x4()
        self._aspect = 1.0

        # Reusable float32 buffer for uniform matrix uploads (avoids per-draw alloc).
        self._mvp_buf = np.empty(16, dtype=np.float32)

    def _init_cubes(self):
        spacing = 0.78 * 0.8 * 0.75
        rows = _DIAMOND_ROWS
        offset_y = (len(rows) - 1) * spacing / 2.0
        max_row = len(rows) - 1
        idx = 0
        for row, xs in enumerate(rows):
            y = row * spacing - offset_y
            z_base = -(1.0 - row / max_row) * 0.5
            for x_off in xs:
                x = x_off * spacing
                self._cubes.append(Cube(x, y, z_base, idx))
                idx += 1

    def initializeGL(self):
        try:
            self._prog = compileProgram(
                compileShader(_VERT, GL_VERTEX_SHADER),
                compileShader(_FRAG, GL_FRAGMENT_SHADER),
            )
            self._particle_prog = compileProgram(
                compileShader(_PARTICLE_VERT, GL_VERTEX_SHADER),
                compileShader(_PARTICLE_FRAG, GL_FRAGMENT_SHADER),
            )
            self._grid_prog = compileProgram(
                compileShader(_GRID_VERT, GL_VERTEX_SHADER),
                compileShader(_GRID_FRAG, GL_FRAGMENT_SHADER),
            )
            self._ring_prog = compileProgram(
                compileShader(_RING_VERT, GL_VERTEX_SHADER),
                compileShader(_RING_FRAG, GL_FRAGMENT_SHADER),
            )
        except Exception as e:
            logger.error("Shader compile error: %s", e)
            return

        # Post-processing programs — failing here only disables real bloom;
        # the legacy halo pass keeps working.
        try:
            self._bright_prog = compileProgram(
                compileShader(_FS_VERT, GL_VERTEX_SHADER),
                compileShader(_BRIGHT_FRAG, GL_FRAGMENT_SHADER),
            )
            self._blur_prog = compileProgram(
                compileShader(_FS_VERT, GL_VERTEX_SHADER),
                compileShader(_BLUR_FRAG, GL_FRAGMENT_SHADER),
            )
            self._copy_prog = compileProgram(
                compileShader(_FS_VERT, GL_VERTEX_SHADER),
                compileShader(_COPY_FRAG, GL_FRAGMENT_SHADER),
            )
            # Dummy VAO: core profile requires one bound even for
            # attribute-less fullscreen-triangle draws.
            self._fs_vao = glGenVertexArrays(1)
            self._buni = {
                "uTex": glGetUniformLocation(self._bright_prog, "uTex"),
                "uThreshold": glGetUniformLocation(self._bright_prog, "uThreshold"),
            }
            self._bluni = {
                "uTex": glGetUniformLocation(self._blur_prog, "uTex"),
                "uDir": glGetUniformLocation(self._blur_prog, "uDir"),
            }
            self._cuni = {
                "uTex": glGetUniformLocation(self._copy_prog, "uTex"),
                "uIntensity": glGetUniformLocation(self._copy_prog, "uIntensity"),
                "uTonemap": glGetUniformLocation(self._copy_prog, "uTonemap"),
            }
            self._bloom_ok = True
        except Exception as e:
            logger.warning("Bloom unavailable (%s); using legacy halo pass.", e)
            self._bloom_ok = False

        # Si el contexto muere (recreación de ventana nativa), zerear los
        # handles para que un initializeGL re-entrante arranque limpio.
        try:
            ctx = self.context()
            if ctx is not None:
                ctx.aboutToBeDestroyed.connect(
                    self._release_gl_handles, Qt.ConnectionType.UniqueConnection
                )
        except Exception:
            pass

        # Cube VAO
        self._vao = glGenVertexArrays(1)
        glBindVertexArray(self._vao)

        vbo_pos = glGenBuffers(1)
        self._vbos.append(vbo_pos)
        glBindBuffer(GL_ARRAY_BUFFER, vbo_pos)
        glBufferData(GL_ARRAY_BUFFER, _CUBE_VERTS.nbytes, _CUBE_VERTS, GL_STATIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 0, None)

        vbo_nor = glGenBuffers(1)
        self._vbos.append(vbo_nor)
        glBindBuffer(GL_ARRAY_BUFFER, vbo_nor)
        glBufferData(GL_ARRAY_BUFFER, _CUBE_NORMALS.nbytes, _CUBE_NORMALS, GL_STATIC_DRAW)
        glEnableVertexAttribArray(1)
        glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 0, None)

        glBindVertexArray(0)

        # Particle VAO
        self._particle_vao = glGenVertexArrays(1)
        glBindVertexArray(self._particle_vao)
        pbo_off = glGenBuffers(1)
        self._vbos.append(pbo_off)
        glBindBuffer(GL_ARRAY_BUFFER, pbo_off)
        glBufferData(GL_ARRAY_BUFFER, self._particle_offsets.nbytes, self._particle_offsets, GL_STATIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, None)
        pbo_pha = glGenBuffers(1)
        self._vbos.append(pbo_pha)
        glBindBuffer(GL_ARRAY_BUFFER, pbo_pha)
        glBufferData(GL_ARRAY_BUFFER, self._particle_phases.nbytes, self._particle_phases, GL_STATIC_DRAW)
        glEnableVertexAttribArray(1)
        glVertexAttribPointer(1, 1, GL_FLOAT, GL_FALSE, 0, None)
        glBindVertexArray(0)

        # Grid VAO (simple plane)
        g, fy = 2.6, _FLOOR_Y
        grid_verts = np.array([
            [-g, fy, -g], [g, fy, -g], [g, fy, g],
            [-g, fy, -g], [g, fy, g], [-g, fy, g],
        ], dtype=np.float32)
        self._grid_vao = glGenVertexArrays(1)
        glBindVertexArray(self._grid_vao)
        gbo = glGenBuffers(1)
        self._vbos.append(gbo)
        glBindBuffer(GL_ARRAY_BUFFER, gbo)
        glBufferData(GL_ARRAY_BUFFER, grid_verts.nbytes, grid_verts, GL_STATIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 0, None)
        glBindVertexArray(0)

        # Ring VAO (unit quad)
        ring_verts = np.array([
            [-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0],
            [-1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0],
        ], dtype=np.float32)
        self._ring_vao = glGenVertexArrays(1)
        glBindVertexArray(self._ring_vao)
        rbo = glGenBuffers(1)
        self._vbos.append(rbo)
        glBindBuffer(GL_ARRAY_BUFFER, rbo)
        glBufferData(GL_ARRAY_BUFFER, ring_verts.nbytes, ring_verts, GL_STATIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 0, None)
        glBindVertexArray(0)

        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)

        # Cache uniforms
        self._uni["uMVP"] = glGetUniformLocation(self._prog, "uMVP")
        self._uni["uScale"] = glGetUniformLocation(self._prog, "uScale")
        self._uni["uTime"] = glGetUniformLocation(self._prog, "uTime")
        self._uni["uBreathAmp"] = glGetUniformLocation(self._prog, "uBreathAmp")
        self._uni["uColor"] = glGetUniformLocation(self._prog, "uColor")
        self._uni["uGlow"] = glGetUniformLocation(self._prog, "uGlow")
        self._uni["uScanlineIntensity"] = glGetUniformLocation(self._prog, "uScanlineIntensity")
        self._uni["uGlitchIntensity"] = glGetUniformLocation(self._prog, "uGlitchIntensity")
        self._uni["uFresnelPower"] = glGetUniformLocation(self._prog, "uFresnelPower")
        self._uni["uDisplacement"] = glGetUniformLocation(self._prog, "uDisplacement")
        self._uni["uColor2"] = glGetUniformLocation(self._prog, "uColor2")
        self._uni["uEdgeWidth"] = glGetUniformLocation(self._prog, "uEdgeWidth")
        self._uni["uGlassMode"] = glGetUniformLocation(self._prog, "uGlassMode")
        self._uni["uAlphaMul"] = glGetUniformLocation(self._prog, "uAlphaMul")

        self._puni["uMVP"] = glGetUniformLocation(self._particle_prog, "uMVP")
        self._puni["uTime"] = glGetUniformLocation(self._particle_prog, "uTime")
        self._puni["uParticleSize"] = glGetUniformLocation(self._particle_prog, "uParticleSize")
        self._puni["uStateIntensity"] = glGetUniformLocation(self._particle_prog, "uStateIntensity")
        self._puni["uColor"] = glGetUniformLocation(self._particle_prog, "uColor")

        self._guni["uMVP"] = glGetUniformLocation(self._grid_prog, "uMVP")
        self._guni["uTime"] = glGetUniformLocation(self._grid_prog, "uTime")
        self._guni["uColor"] = glGetUniformLocation(self._grid_prog, "uColor")
        self._guni["uIntensity"] = glGetUniformLocation(self._grid_prog, "uIntensity")

        self._runi["uMVP"] = glGetUniformLocation(self._ring_prog, "uMVP")
        self._runi["uColor"] = glGetUniformLocation(self._ring_prog, "uColor")
        self._runi["uProgress"] = glGetUniformLocation(self._ring_prog, "uProgress")
        self._runi["uIntensity"] = glGetUniformLocation(self._ring_prog, "uIntensity")

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)
        self._fb_size = (max(1, int(w)), max(1, int(h)))
        self._aspect = w / h if h else 1.0
        self._proj = QMatrix4x4()
        self._proj.perspective(45.0, self._aspect, 0.1, 100.0)
        self._view = QMatrix4x4()
        # Encuadre ajustado: el diamante llena ~45% del alto (antes ~33%) y
        # el centro mira ligeramente abajo para darle aire al reflejo.
        self._view.lookAt(
            QVector3D(0.0, 0.0, _CAM_Z),
            QVector3D(0.0, -0.15, 0.0),
            QVector3D(0.0, 1.0, 0.0),
        )
        self._ensure_bloom_targets(*self._fb_size)

    def _ensure_bloom_targets(self, w: int, h: int):
        """(Re)create the HDR scene FBO and the half-res blur ping/pong
        FBOs at the given pixel size. Context must be current."""
        if not self._bloom_ok or (w, h) == self._bloom_size or w <= 0 or h <= 0:
            return
        self._destroy_bloom_targets()
        try:
            def _make_tex(tw, th):
                tex = glGenTextures(1)
                glBindTexture(GL_TEXTURE_2D, tex)
                # RGBA16F: el shader emite hasta ~3.5; HDR libera esos
                # valores para el bright-pass en vez de clampear a 1.0.
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA16F, tw, th, 0,
                             GL_RGBA, GL_FLOAT, None)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                return tex

            self._scene_tex = _make_tex(w, h)
            self._scene_depth = glGenRenderbuffers(1)
            glBindRenderbuffer(GL_RENDERBUFFER, self._scene_depth)
            glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, w, h)
            self._scene_fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self._scene_fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, self._scene_tex, 0)
            glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                                      GL_RENDERBUFFER, self._scene_depth)
            ok = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE

            bw, bh = max(1, w // 2), max(1, h // 2)
            for i in range(2):
                self._ping_tex[i] = _make_tex(bw, bh)
                self._ping_fbo[i] = glGenFramebuffers(1)
                glBindFramebuffer(GL_FRAMEBUFFER, self._ping_fbo[i])
                glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                       GL_TEXTURE_2D, self._ping_tex[i], 0)
                ok = ok and (glCheckFramebufferStatus(GL_FRAMEBUFFER)
                             == GL_FRAMEBUFFER_COMPLETE)

            glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
            glBindTexture(GL_TEXTURE_2D, 0)
            glBindRenderbuffer(GL_RENDERBUFFER, 0)

            if not ok:
                logger.warning("Bloom FBO incomplete; using legacy halo pass.")
                self._destroy_bloom_targets()
                self._bloom_ok = False
                return
            self._bloom_size = (w, h)
            self._blur_size = (bw, bh)
            logger.info("Bloom pipeline active (%dx%d, blur %dx%d)", w, h, bw, bh)
        except Exception as e:
            logger.warning("Bloom FBO setup failed (%s); legacy halo pass.", e)
            self._destroy_bloom_targets()
            self._bloom_ok = False

    def _destroy_bloom_targets(self):
        try:
            if self._scene_fbo:
                glDeleteFramebuffers(1, [self._scene_fbo])
            if self._scene_depth:
                glDeleteRenderbuffers(1, [self._scene_depth])
            if self._scene_tex:
                glDeleteTextures([self._scene_tex])
            for i in range(2):
                if self._ping_fbo[i]:
                    glDeleteFramebuffers(1, [self._ping_fbo[i]])
                if self._ping_tex[i]:
                    glDeleteTextures([self._ping_tex[i]])
        except Exception:
            pass
        self._scene_fbo = self._scene_tex = self._scene_depth = 0
        self._ping_fbo = [0, 0]
        self._ping_tex = [0, 0]
        self._bloom_size = (0, 0)

    def _release_gl_handles(self):
        """The GL context is about to die (native window re-creation):
        drop every handle so a re-entrant initializeGL starts clean."""
        self._vbos.clear()
        self._prog = self._particle_prog = self._grid_prog = self._ring_prog = 0
        self._vao = self._particle_vao = self._grid_vao = self._ring_vao = 0
        self._bright_prog = self._blur_prog = self._copy_prog = 0
        self._fs_vao = 0
        self._scene_fbo = self._scene_tex = self._scene_depth = 0
        self._ping_fbo = [0, 0]
        self._ping_tex = [0, 0]
        self._bloom_size = (0, 0)
        self._bloom_ok = False

    def paintGL(self):
        if not self._prog:
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            from PyQt6.QtGui import QPainter, QColor, QBrush, QPen
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(0, 230, 255, 200), 2))
            p.setBrush(QBrush(QColor(0, 230, 255, 60)))
            cx, cy = self.width() // 2, self.height() // 2
            size = min(self.width(), self.height()) // 3
            p.drawEllipse(cx - size, cy - size, size * 2, size * 2)
            p.end()
            return

        # Con bloom real la escena se dibuja a un FBO HDR y se compone al
        # final; sin él (config o fallback) se dibuja directo como siempre.
        use_bloom = (
            self._bloom_ok
            and bool(APP_CONFIG.bloom_enabled)
            and bool(self._scene_fbo)
            and self._bloom_size == self._fb_size
        )
        if use_bloom:
            glBindFramebuffer(GL_FRAMEBUFFER, self._scene_fbo)
            glViewport(0, 0, self._fb_size[0], self._fb_size[1])

        glClearColor(0.0, 0.0, 0.0, 0.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        t = self._time
        state = self._state
        vol = self._speech_vol if state == "SPEAKING" else 0.0

        # Eased colors (cross-fade between state palettes)
        base_col = self._color_cur
        halo_col = self._halo_cur
        for c in self._cubes:
            c.base_color = base_col
            c.halo_color = halo_col

        # Global rotation
        rot = QMatrix4x4()
        if state == "PROCESSING":
            rot.rotate(t * 60.0, 0.0, 1.0, 0.0)
            rot.rotate(math.sin(t * 2.0) * 10.0, 1.0, 0.0, 0.0)
        else:
            rot.rotate(math.sin(t * 0.15) * 8.0, 0.0, 1.0, 0.0)
            rot.rotate(math.cos(t * 0.12) * 5.0, 1.0, 0.0, 0.0)

        max_dist = 3.0 * (0.78 * 0.8 * 0.75)
        animated = state in ("WAKE", "PROCESSING", "SPEAKING")
        standby = state == "STANDBY"

        # Effect intensities, eased in _ease_state_values()
        scanline_int = self._scan_cur
        glitch_int = self._glitch_cur
        fresnel_pow = self._fresnel_cur

        # ---- REFLECTION PASS (cubos espejados bajo el piso) ----
        if APP_CONFIG.reflection_enabled and not standby:
            glUseProgram(self._prog)
            glBindVertexArray(self._vao)
            col2 = self._color2_cur
            glUniform3f(self._uni["uColor2"], col2.x(), col2.y(), col2.z())
            glUniform1f(self._uni["uEdgeWidth"], 0.22)
            glUniform1f(self._uni["uGlassMode"], 1.0 if APP_CONFIG.wireframe_enabled else 0.0)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glDepthMask(GL_FALSE)
            # Espejo en espacio de mundo respecto al plano y=_FLOOR_Y,
            # aplicado después de la rotación global.
            mirror = QMatrix4x4()
            mirror.translate(0.0, _FLOOR_Y, 0.0)
            mirror.scale(1.0, -1.0, 1.0)
            mirror.translate(0.0, -_FLOOR_Y, 0.0)
            rot_mirror = mirror * rot
            for c in self._cubes:
                # Desvanecer con la altura: solo los cubos bajos reflejan
                fade = 0.15 * max(0.0, 1.0 - (c.y - _FLOOR_Y) / 1.6)
                if fade <= 0.005:
                    continue
                dist = math.sqrt(c.x * c.x + c.y * c.y)
                dist_norm = min(dist / max_dist, 1.0)
                scale, glow, z_off, px, py, disp = self._cube_params(c, t, state, vol, dist_norm)
                glUniform1f(self._uni["uAlphaMul"], fade)
                self._draw_cube(
                    rot_mirror, c, scale, glow * 0.8, z_off, px, py, c.base_color, t,
                    breath_amp=0.021, scanline=0.0, glitch=0.0,
                    fresnel=fresnel_pow, displacement=disp
                )
            glDepthMask(GL_TRUE)
            glUniform1f(self._uni["uAlphaMul"], 1.0)
            glBindVertexArray(0)

        # ---- GRID PASS ----
        if APP_CONFIG.grid_enabled and not standby:
            glUseProgram(self._grid_prog)
            glBindVertexArray(self._grid_vao)
            grid_mvp = self._proj * self._view * rot
            self._mvp_buf[:] = grid_mvp.data()
            glUniformMatrix4fv(self._guni["uMVP"], 1, GL_FALSE, self._mvp_buf)
            glUniform1f(self._guni["uTime"], t)
            glUniform3f(self._guni["uColor"], base_col.x(), base_col.y(), base_col.z())
            glUniform1f(self._guni["uIntensity"], 0.4 if animated else 0.22)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE)
            glDrawArrays(GL_TRIANGLES, 0, 6)
            glBindVertexArray(0)

        # ---- HALO PASS (additive) ----
        glUseProgram(self._prog)
        glBindVertexArray(self._vao)
        # Per-frame material uniforms, shared by the halo and solid passes.
        # wireframe_enabled selecciona el estilo cristal/aristas (antes
        # alternaba el wireframe GL_LINE legacy).
        col2 = self._color2_cur
        glUniform3f(self._uni["uColor2"], col2.x(), col2.y(), col2.z())
        glUniform1f(self._uni["uEdgeWidth"], 0.22)
        glUniform1f(self._uni["uGlassMode"], 1.0 if APP_CONFIG.wireframe_enabled else 0.0)
        glUniform1f(self._uni["uAlphaMul"], 1.0)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        # Camino legacy únicamente: con bloom real el glow sale del
        # post-proceso, no de redibujar los cubos escalados.
        if not use_bloom:
            # Sin aristas en los cubos-halo: a escala 1.6-3.8x se verían
            # como cubos duplicados, no como resplandor.
            glUniform1f(self._uni["uEdgeWidth"], 0.0)
            for c in self._cubes:
                dist = math.sqrt(c.x * c.x + c.y * c.y)
                dist_norm = min(dist / max_dist, 1.0)
                scale, glow, z_off, px, py, disp = self._cube_params(c, t, state, vol, dist_norm)
                halo_boost = 1.0 if animated else (0.55 if standby else 0.75)
                halo_scale = scale * (1.6 + glow * 2.2 * halo_boost)
                halo_glow = glow * 0.75 * halo_boost
                self._draw_cube(
                    rot, c, halo_scale, halo_glow, z_off, px, py, c.halo_color, t,
                    breath_amp=0.042, scanline=scanline_int, glitch=glitch_int,
                    fresnel=fresnel_pow, displacement=disp
                )

        # ---- SOLID PASS ----
        glUniform1f(self._uni["uEdgeWidth"], 0.22)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for c in self._cubes:
            dist = math.sqrt(c.x * c.x + c.y * c.y)
            dist_norm = min(dist / max_dist, 1.0)
            scale, glow, z_off, px, py, disp = self._cube_params(c, t, state, vol, dist_norm)
            self._draw_cube(
                rot, c, scale, glow, z_off, px, py, c.base_color, t,
                breath_amp=0.021, scanline=scanline_int, glitch=glitch_int,
                fresnel=fresnel_pow, displacement=disp
            )

        # ---- GHOST ECHO (descuadre al pensar) ----
        # Contorno fantasma additive con las caras desplazadas a CONTRAFASE
        # del cubo principal: dos juegos de aristas desfasándose entre sí,
        # heredero del doble contorno descuadrado del halo wireframe legacy.
        if self._echo_cur > 0.02:
            glBlendFunc(GL_SRC_ALPHA, GL_ONE)
            glUniform1f(self._uni["uAlphaMul"], 0.35 * self._echo_cur)
            for c in self._cubes:
                dist = math.sqrt(c.x * c.x + c.y * c.y)
                dist_norm = min(dist / max_dist, 1.0)
                scale, glow, z_off, px, py, disp = self._cube_params(c, t, state, vol, dist_norm)
                self._draw_cube(
                    rot, c, scale * 1.05, glow * 0.5, z_off, px, py, c.halo_color, t,
                    breath_amp=0.05, scanline=0.0, glitch=glitch_int,
                    fresnel=fresnel_pow, displacement=-(disp * 1.4) - 0.045
                )
            glUniform1f(self._uni["uAlphaMul"], 1.0)

        glBindVertexArray(0)

        # ---- PARTICLE PASS ----
        if APP_CONFIG.particles_enabled:
            glUseProgram(self._particle_prog)
            glBindVertexArray(self._particle_vao)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE)
            self._mvp_buf[:] = (self._proj * self._view * rot).data()
            glUniformMatrix4fv(self._puni["uMVP"], 1, GL_FALSE, self._mvp_buf)
            glUniform1f(self._puni["uTime"], t)
            glUniform1f(self._puni["uParticleSize"], 3.2 + vol * 6.0)
            glUniform1f(self._puni["uStateIntensity"], 1.0 if animated else 0.6)
            glUniform3f(self._puni["uColor"], base_col.x(), base_col.y(), base_col.z())
            glDrawArrays(GL_POINTS, 0, self._particle_count)
            glBindVertexArray(0)

        # ---- EXPANSION RINGS ----
        self._update_rings()
        if self._rings:
            glUseProgram(self._ring_prog)
            glBindVertexArray(self._ring_vao)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE)
            for ring in self._rings[:]:
                age = t - ring["birth"]
                if age > 2.0:
                    continue
                progress = age / 2.0
                intensity = (1.0 - progress) * ring.get("intensity", 1.0)
                if intensity < 0.01:
                    continue
                ring_model = QMatrix4x4()
                ring_model.translate(0.0, 0.0, 0.0)
                ring_model.scale(0.2 + progress * 3.0)
                ring_model.rotate(90.0, 1.0, 0.0, 0.0)
                ring_mvp = self._proj * self._view * rot * ring_model
                self._mvp_buf[:] = ring_mvp.data()
                glUniformMatrix4fv(self._runi["uMVP"], 1, GL_FALSE, self._mvp_buf)
                col = ring["color"]
                glUniform3f(self._runi["uColor"], col.x(), col.y(), col.z())
                glUniform1f(self._runi["uProgress"], 0.15)
                glUniform1f(self._runi["uIntensity"], intensity)
                glDrawArrays(GL_TRIANGLES, 0, 6)
            glBindVertexArray(0)

        if use_bloom:
            self._composite_bloom()

        glUseProgram(0)

    def _composite_bloom(self):
        """Bright-pass + separable blur de la escena HDR y composición al
        FBO de respaldo del widget, preservando el alpha de la escena."""
        w, h = self._fb_size
        bw, bh = self._blur_size
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_BLEND)
        glBindVertexArray(self._fs_vao)
        glActiveTexture(GL_TEXTURE0)

        # Bright-pass: escena -> ping0 (media resolución)
        glBindFramebuffer(GL_FRAMEBUFFER, self._ping_fbo[0])
        glViewport(0, 0, bw, bh)
        glClearColor(0.0, 0.0, 0.0, 0.0)
        glClear(GL_COLOR_BUFFER_BIT)
        glUseProgram(self._bright_prog)
        glBindTexture(GL_TEXTURE_2D, self._scene_tex)
        glUniform1i(self._buni["uTex"], 0)
        # 0.60 + soft knee: IDLE edges (luma ~1.0) already glow softly;
        # active states amplify naturally.
        glUniform1f(self._buni["uThreshold"], 0.60)
        glDrawArrays(GL_TRIANGLES, 0, 3)

        # Blur gaussiano separable: H (ping0->ping1), V (ping1->ping0)
        glUseProgram(self._blur_prog)
        glUniform1i(self._bluni["uTex"], 0)
        glBindFramebuffer(GL_FRAMEBUFFER, self._ping_fbo[1])
        glClear(GL_COLOR_BUFFER_BIT)
        glBindTexture(GL_TEXTURE_2D, self._ping_tex[0])
        glUniform2f(self._bluni["uDir"], 1.0 / bw, 0.0)
        glDrawArrays(GL_TRIANGLES, 0, 3)
        glBindFramebuffer(GL_FRAMEBUFFER, self._ping_fbo[0])
        glClear(GL_COLOR_BUFFER_BIT)
        glBindTexture(GL_TEXTURE_2D, self._ping_tex[1])
        glUniform2f(self._bluni["uDir"], 0.0, 1.0 / bh)
        glDrawArrays(GL_TRIANGLES, 0, 3)

        # Composite al FBO de respaldo de QOpenGLWidget — NUNCA bind 0.
        glBindFramebuffer(GL_FRAMEBUFFER, self.defaultFramebufferObject())
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 0.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glUseProgram(self._copy_prog)
        glUniform1i(self._cuni["uTex"], 0)
        # 1) Escena con blending OFF: el alpha queda bit-idéntico al render
        #    directo (crítico para la ventana translúcida / DWM).
        glBindTexture(GL_TEXTURE_2D, self._scene_tex)
        glUniform1f(self._cuni["uIntensity"], 1.0)
        glUniform1f(self._cuni["uTonemap"], 0.0)
        glDrawArrays(GL_TRIANGLES, 0, 3)
        # 2) Bloom aditivo en color Y alpha (DWM compone por alpha de
        #    ventana; RGB aditivo con alpha 0 sería invisible). Tonemap ON
        #    para que los picos HDR no clipeen por canal.
        glEnable(GL_BLEND)
        glBlendFuncSeparate(GL_ONE, GL_ONE, GL_ONE, GL_ONE)
        glBindTexture(GL_TEXTURE_2D, self._ping_tex[0])
        glUniform1f(self._cuni["uIntensity"], max(0.0, float(APP_CONFIG.bloom_intensity)))
        glUniform1f(self._cuni["uTonemap"], 1.0)
        glDrawArrays(GL_TRIANGLES, 0, 3)

        # Restaurar estado para el siguiente frame / camino legacy
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_DEPTH_TEST)
        glBindVertexArray(0)
        glBindTexture(GL_TEXTURE_2D, 0)

    def _cube_params(self, c: Cube, t: float, state: str, vol: float, dist_norm: float):
        scale = 1.0
        glow = 0.0
        z_off = 0.0
        displacement = 0.0
        radial_wave = math.sin(t * 2.5 - dist_norm * math.pi * 2.5 + vol * 4) * 0.5 + 0.5

        if state == "STANDBY":
            glow = 0.12 + math.sin(t * 0.8 + c.idx * 0.5) * 0.06
            scale = 0.96
        elif state == "IDLE":
            glow = 0.10 + math.sin(t * 1.2 + c.idx * 0.7) * 0.05
        elif state == "WAKE":
            flash = math.exp(-((t % 2.0) * 4.0)) * 0.35
            glow = 0.85 + flash
            scale = 1.05 + flash * 0.12
            displacement = flash * 0.15
        elif state == "LISTENING":
            glow = 0.15 + math.sin(t * 2.0 + c.idx * 0.8) * 0.08
            # React to audio spectrum
            spec_idx = min(c.idx, 7)
            spec_val = self._spectrum[spec_idx] if spec_idx < len(self._spectrum) else 0.0
            glow += spec_val * 0.3
            displacement = spec_val * 0.08
        elif state == "PROCESSING":
            wave = math.sin(t * 3.5 + c.idx * 0.9) * 0.5 + 0.5
            glow = 0.40 + wave * 0.55 + radial_wave * 0.25
            scale = 1.0 + wave * 0.045 + radial_wave * 0.02
            z_off = math.sin(t * 4.0 + dist_norm * 5.0) * 0.12
            # Descuadre marcado al pensar: las caras se separan a lo largo
            # de su normal (el eco fantasma va a contrafase de esto)
            displacement = math.sin(t * 6.0 + c.idx) * 0.15
        elif state == "SPEAKING":
            react = self._speech_vol * 0.6
            center_first = (1.0 - dist_norm * 0.6) * react
            glow = 0.30 + center_first * 0.9 + react * 0.25
            scale = 1.0 + center_first * 0.18
            z_off = react * 0.18 * math.sin(t * 5.0 + dist_norm * 3.0)
            displacement = react * 0.16

        radial_scale = 1.0 + radial_wave * vol * 0.12 * (1.0 - dist_norm * 0.3)
        scale *= radial_scale
        scale *= 0.42

        push = vol * 0.08 * (1.0 + radial_wave)
        angle = math.atan2(c.y, c.x)
        px = math.cos(angle) * push * dist_norm
        py = math.sin(angle) * push * dist_norm

        return scale, glow, z_off, px, py, displacement

    def _draw_cube(self, rot, c: Cube, scale, glow, z_off, px, py, color, t,
                   breath_amp, scanline, glitch, fresnel, displacement):
        model = QMatrix4x4()
        model.translate(c.x + px, c.y + py, z_off + c.z_base)

        if self._state == "PROCESSING":
            model.rotate(t * 90.0 + c.idx * 40.0, 0.0, 1.0, 0.0)
            model.rotate(math.sin(t * 3.0 + c.idx) * 20.0, 1.0, 0.0, 0.0)

        model = rot * model
        mvp = self._proj * self._view * model

        self._mvp_buf[:] = mvp.data()
        glUniformMatrix4fv(self._uni["uMVP"], 1, GL_FALSE, self._mvp_buf)
        glUniform1f(self._uni["uScale"], scale)
        glUniform1f(self._uni["uTime"], t)
        glUniform1f(self._uni["uBreathAmp"], breath_amp)
        glUniform3f(self._uni["uColor"], color.x(), color.y(), color.z())
        glUniform1f(self._uni["uGlow"], glow)
        glUniform1f(self._uni["uScanlineIntensity"], scanline)
        glUniform1f(self._uni["uGlitchIntensity"], glitch)
        glUniform1f(self._uni["uFresnelPower"], fresnel)
        glUniform1f(self._uni["uDisplacement"], displacement)

        glDrawArrays(GL_TRIANGLES, 0, _CUBE_VERT_COUNT)

    def _update_rings(self):
        """Remove old rings in-place to avoid per-frame list allocation."""
        i = 0
        while i < len(self._rings):
            if self._time - self._rings[i]["birth"] >= 2.0:
                self._rings.pop(i)
            else:
                i += 1

    def _spawn_ring(self, color: QVector3D, intensity: float = 1.0):
        # Rate-limit: max 1 ring per 200 ms; cap total active rings to 8
        if len(self._rings) >= 8:
            return
        if self._time - self._last_ring_time < 0.2:
            return
        self._last_ring_time = self._time
        self._rings.append({"birth": self._time, "color": color, "intensity": intensity})

    def _tick(self):
        now = _time.perf_counter()
        dt = min(now - self._last_tick, 0.05)
        self._time += dt
        self._last_tick = now
        self._ease_state_values(dt)
        self.update()

    def _ease_state_values(self, dt: float):
        """Exponential ease (~180 ms) of colors and effect intensities
        toward the current state's targets."""
        state = self._state
        animated = state in ("WAKE", "PROCESSING", "SPEAKING")
        scan_t = 0.3 if state == "PROCESSING" else (0.1 if animated else 0.0)
        # 0.08: strong glitch reads as dirt on the crystal material
        glitch_t = 0.08 if state == "PROCESSING" else 0.0
        if not APP_CONFIG.scanlines_enabled:
            scan_t = 0.0
        if not APP_CONFIG.glitch_enabled:
            glitch_t = 0.0
        fresnel_t = 2.0 if animated else 3.0
        color_t = _COLORS.get(state, _COLORS["IDLE"])
        halo_t = _HALO_COLORS.get(state, _HALO_COLORS["IDLE"])
        color2_t = _COLORS2.get(state, _COLORS2["IDLE"])

        echo_t = 1.0 if state == "PROCESSING" else 0.0

        k = 1.0 - math.exp(-dt / 0.18)
        self._color_cur = self._color_cur + (color_t - self._color_cur) * k
        self._halo_cur = self._halo_cur + (halo_t - self._halo_cur) * k
        self._color2_cur = self._color2_cur + (color2_t - self._color2_cur) * k
        self._scan_cur += (scan_t - self._scan_cur) * k
        self._glitch_cur += (glitch_t - self._glitch_cur) * k
        self._fresnel_cur += (fresnel_t - self._fresnel_cur) * k
        self._echo_cur += (echo_t - self._echo_cur) * k

    def _cleanup_gl(self):
        self.makeCurrent()
        try:
            for vao in [self._vao, self._particle_vao, self._grid_vao,
                        self._ring_vao, self._fs_vao]:
                if vao:
                    glDeleteVertexArrays(1, [vao])
            for prog in [self._prog, self._particle_prog, self._grid_prog,
                         self._ring_prog, self._bright_prog, self._blur_prog,
                         self._copy_prog]:
                if prog:
                    glDeleteProgram(prog)
            for vbo in self._vbos:
                if vbo:
                    glDeleteBuffers(1, [vbo])
            self._vbos.clear()
            self._destroy_bloom_targets()
        except Exception:
            pass
        finally:
            self.doneCurrent()

    def set_state(self, state: str, status_text: str = ""):
        old_state = self._state
        self._state = state
        # Spawn expansion ring on state transitions to WAKE or SPEAKING
        if state == "WAKE" and old_state != "WAKE":
            self._spawn_ring(_COLORS["WAKE"], intensity=1.2)
        elif state == "SPEAKING" and old_state != "SPEAKING":
            self._spawn_ring(_COLORS["SPEAKING"], intensity=0.8)
        elif state == "PROCESSING" and old_state != "PROCESSING":
            self._spawn_ring(_COLORS["PROCESSING"], intensity=0.6)

    def set_audio_volume(self, vol: float):
        self._audio_vol = max(0.0, min(1.0, vol))

    def set_speech_volume(self, vol: float):
        self._speech_vol = max(0.0, min(1.0, vol))

    def set_audio_spectrum(self, bins: list):
        self._spectrum = bins[:8] if len(bins) >= 8 else bins + [0.0] * (8 - len(bins))

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._timer.isActive():
            self._timer.start(max(1, int(1000 / max(15, min(int(APP_CONFIG.visual_fps or 60), 144)))))
        super().showEvent(event)
