#!/usr/bin/env python3
"""
Jarvis HUD v3 — GPU (OpenGL) sinematik parçacık küresi.

30.000 parçacık ekran kartında shader'larla çizilir:
  - 18.000 yüzey + 12.000 iç hacim parçacığı (volumetrik küre)
  - Additive blending -> gerçek ışıma/bloom hissi
  - Derinliğe göre renk (koyu elektrik mavisi -> buz beyazı) ve boyut
  - Nefes + sese tepkili yüzey dalgaları, yüksek seste beyaz patlamalar
  - Koyu sinema zemini, vinyet, film greni, tarama çizgisi, veri yayları

jarvis.py'den UDP (127.0.0.1:5599) ile durum alır:
  {"state": "listening"|"thinking"|"speaking"|"bye", "level": 0.0-1.0}

Sürüklenebilir; çift tık kapatır. GPU gerektirir (her modern sistemde var).
"""

import json
import math
import random
import socket
import sys

import numpy as np
from OpenGL import GL as gl
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient, QSurfaceFormat
from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QApplication

UDP_PORT = 5599
SIZE = 420
SURFACE_PARTICLES = 18000
VOLUME_PARTICLES = 12000


STATE_TEXT = {
    "listening": "LISTENING",
    "thinking": "PROCESSING",
    "speaking": "RESPONDING",
}

VERTEX_SHADER = """
#version 120
attribute vec3 aPos;     // yön * yarıçap (birim küre içinde)
attribute float aSeed;   // parıltı fazı
uniform float uTime;
uniform float uEnergy;   // 0-1 ses/ritim
uniform float uRadius;   // ekran ölçeği (nefes dahil)
uniform float uPoint;    // nokta boyutu çarpanı (bloom/core geçişi)
varying float vDepth;
varying float vTw;
varying float vBoost;
varying float vBlur;     // alan derinliği: odaktan uzaklık
varying float vLen;      // merkeze uzaklık: renk katmanları için
void main() {
    vec3 dir = normalize(aPos);
    float len = length(aPos);
    vLen = len;
    float lon = atan(dir.z, dir.x);

    // Sese tepkili, yüzeyde akan dalga
    float wave = 1.0 + (0.04 + 0.15 * uEnergy)
                 * sin(uTime * 3.4 + dir.y * 6.0 + lon * 3.0);
    vec3 p = dir * len * wave;

    // Yavaş Y dönüşü + hafif paralaks eğimi + kamera salınımı
    float ay = uTime * 0.38;
    float ax = 0.45 + 0.07 * sin(uTime * 0.6);
    float c = cos(ay); float s = sin(ay);
    p = vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z);
    c = cos(ax); s = sin(ax);
    p = vec3(p.x, c * p.y - s * p.z, s * p.y + c * p.z);

    // GERÇEK PERSPEKTİF: yakın parçacık büyür, uzak küçülür
    float camDist = 2.6 + 0.10 * sin(uTime * 0.5);   // hafif kamera dolly
    float persp = camDist / (camDist - p.z);
    gl_Position = vec4(p.xy * persp * uRadius, 0.0, 1.0);

    vDepth = clamp((p.z + 1.1) / 2.2, 0.0, 1.0);

    // ALAN DERİNLİĞİ: odak ön yüzde (z ~ +0.5); uzaklaştıkça bulanıklaşır
    vBlur = clamp(abs(p.z - 0.5) * 1.1, 0.0, 1.0);

    vTw = 0.5 + 0.5 * sin(uTime * 3.0 + aSeed);
    vBoost = (uEnergy > 0.5 && vTw > 0.93) ? 1.0 : 0.0;

    gl_PointSize = (1.1 + 3.4 * vDepth) * persp * uPoint
                   * (1.0 + 0.22 * uEnergy)
                   * (0.7 + 0.6 * vTw)
                   * (1.0 + 1.6 * vBoost)
                   * (1.0 + 2.2 * vBlur);   // odak dışı: büyür ama (fragment'ta) soluklaşır
}
"""

FRAGMENT_SHADER = """
#version 120
varying float vDepth;
varying float vTw;
varying float vBoost;
varying float vBlur;
varying float vLen;
uniform float uAlphaMul;
void main() {
    vec2 d = gl_PointCoord - vec2(0.5);
    float r2 = dot(d, d) * 4.0;
    if (r2 > 1.0) discard;

    // Odakta keskin, odak dışında geniş/yumuşak sprite (bokeh hissi)
    float sharp = mix(3.4, 1.1, vBlur);
    float fall = exp(-r2 * sharp);

    // RADYAL katmanlar: çekirdek mor -> orta derin mavi -> dış kabuk turkuaz
    vec3 cCore = vec3(0.184, 0.024, 0.631);   // #2F06A1 (merkez)
    vec3 cMid  = vec3(0.024, 0.169, 0.631);   // #062BA1 (orta katman)
    vec3 cOut  = vec3(0.024, 0.471, 0.631);   // #0678A1 (dış kabuk)
    vec3 col = mix(cCore, cMid, smoothstep(0.30, 0.62, vLen));
    col = mix(col, cOut, smoothstep(0.62, 0.95, vLen));
    col *= (0.60 + 0.40 * vTw);               // parıltı: mat, beyazlatma yok
    col = mix(col, cOut * 1.25, vBoost * 0.5); // patlama: hafif turkuaz vurgusu

    // Derinlik kontrastı: arka söner, ön belirgin (mat tavan)
    float a = fall * uAlphaMul * (0.04 + 0.96 * pow(vDepth, 1.6));
    a *= mix(1.0, 0.30, vBlur);             // bulanık olan soluklaşır
    gl_FragColor = vec4(col * a, a);        // additive için ön-çarpılmış
}
"""



def build_particles() -> np.ndarray:
    """[x,y,z,seed] float32 dizisi: yüzey kabuğu + volumetrik iç hacim."""
    # Yüzey: fibonacci dağılımı
    i = np.arange(SURFACE_PARTICLES, dtype=np.float64)
    golden = math.pi * (3.0 - math.sqrt(5.0))
    y = 1.0 - 2.0 * i / (SURFACE_PARTICLES - 1)
    r = np.sqrt(np.clip(1.0 - y * y, 0, 1))
    th = golden * i
    surf = np.stack([np.cos(th) * r, y, np.sin(th) * r], axis=1)
    surf *= np.random.default_rng(1).uniform(0.97, 1.0, (SURFACE_PARTICLES, 1))

    # İç hacim: rastgele yönler, kürede homojen yarıçap
    rng = np.random.default_rng(2)
    v = rng.normal(size=(VOLUME_PARTICLES, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    rad = 0.20 + 0.75 * np.cbrt(rng.uniform(0, 1, (VOLUME_PARTICLES, 1)))
    vol = v * rad

    pos = np.vstack([surf, vol]).astype(np.float32)
    seed = rng.uniform(0, 2 * math.pi, (len(pos), 1)).astype(np.float32)
    return np.hstack([pos, seed])  # (N, 4)


class JarvisHUD(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(SIZE, SIZE)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - SIZE - 30, screen.bottom() - SIZE - 60)

        self.state = "listening"
        self.level = 0.0
        self.smooth = 0.0
        self.phase = 0.0
        self._drag = None
        self.program = None
        self.vbo = None
        self.data = build_particles()
        self.n = len(self.data)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", UDP_PORT))
        self.sock.setblocking(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)

    # ------------------------- veri + animasyon -------------------------
    def tick(self):
        try:
            while True:
                data, _ = self.sock.recvfrom(1024)
                msg = json.loads(data.decode())
                self.state = msg.get("state", self.state)
                self.level = max(0.0, min(1.0, float(msg.get("level", 0.0))))
        except (BlockingIOError, ValueError):
            pass

        if self.state in ("bye", "idle"):
            QApplication.quit()
            return

        self.smooth += (self.level - self.smooth) * 0.28
        self.phase += 0.030 * (2.3 if self.state == "thinking" else 1.0)
        self.update()

    def _energy(self) -> float:
        if self.state == "speaking":
            return 0.14 + 0.10 * (0.5 + 0.5 * math.sin(self.phase * 5.0))
        return self.smooth

    # ------------------------------ OpenGL ------------------------------
    def initializeGL(self):
        self.program = QOpenGLShaderProgram(self)
        ok = self.program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER
        ) and self.program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT_SHADER
        ) and self.program.link()
        if not ok:
            print("SHADER HATASI:\n", self.program.log(), file=sys.stderr)
            QApplication.quit()
            return

        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.vbo.create()
        self.vbo.bind()
        raw = self.data.tobytes()
        self.vbo.allocate(raw, len(raw))
        self.vbo.release()


    def paintGL(self):
        energy = self._energy()
        breath = 0.045 * math.sin(self.phase * 1.6)
        radius = 0.62 * (1.0 + breath + energy * 0.20)

        # Koyu sinema zemini
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)  # tamamen şeffaf zemin
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE)     # additive: ışıklar toplanır
        gl.glEnable(gl.GL_VERTEX_PROGRAM_POINT_SIZE)
        try:
            gl.glEnable(gl.GL_POINT_SPRITE)      # core profilde gereksiz olabilir
        except Exception:
            pass

        self.program.bind()
        self.vbo.bind()
        pos_loc = self.program.attributeLocation("aPos")
        seed_loc = self.program.attributeLocation("aSeed")
        self.program.enableAttributeArray(pos_loc)
        self.program.enableAttributeArray(seed_loc)
        self.program.setAttributeBuffer(pos_loc, gl.GL_FLOAT, 0, 3, 16)
        self.program.setAttributeBuffer(seed_loc, gl.GL_FLOAT, 12, 1, 16)
        self.program.setUniformValue("uTime", float(self.phase))
        self.program.setUniformValue("uEnergy", float(energy))
        self.program.setUniformValue("uRadius", float(radius))

        # 1. geçiş: bloom katmanı (büyük, soluk noktalar -> volumetrik ışıma)
        self.program.setUniformValue("uPoint", 3.0)
        self.program.setUniformValue("uAlphaMul", 0.026)
        gl.glDrawArrays(gl.GL_POINTS, 0, self.n)

        # 2. geçiş: keskin çekirdek parçacıklar
        self.program.setUniformValue("uPoint", 1.0)
        self.program.setUniformValue("uAlphaMul", 0.40)
        gl.glDrawArrays(gl.GL_POINTS, 0, self.n)

        self.vbo.release()
        self.program.release()

        # ----------------- 2D sinema katmanı (QPainter) -----------------
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = SIZE / 2, SIZE / 2
        R = 130 * (1.0 + breath + energy * 0.20)

        # Çekirdek: #2F06A1 MOR, büyük, ışıması dar alanda sönen mat küre
        core_r = (34 + energy * 14)
        core = QRadialGradient(QPointF(cx, cy), core_r * 1.45)
        core.setColorAt(0.0, QColor(118, 70, 220, 215))   # aydınlık mor merkez
        core.setColorAt(0.45, QColor(47, 6, 161, 170))    # #2F06A1
        core.setColorAt(0.85, QColor(30, 5, 110, 40))
        core.setColorAt(1.0, QColor(20, 4, 80, 0))        # ışıma hızla söner
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), core_r * 1.45, core_r * 1.45)

        # Dönen veri yayları
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rr, spd, alpha, wdt in ((1.18, 1.0, 110, 1.6), (1.36, -0.55, 65, 1.0)):
            pen = QPen(QColor(10, 150, 200, alpha), wdt)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            r = R * rr
            rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            a0 = math.degrees(self.phase * spd * 1.7) % 360
            for off in (0, 150, 255):
                p.drawArc(rect, int((a0 + off) * 16), 38 * 16)

        # Radyal ızgara kesitleri
        p.setPen(QPen(QColor(8, 120, 161, 60), 1))
        for gdeg in range(0, 360, 30):
            g = math.radians(gdeg) + self.phase * 0.08
            p.drawLine(
                QPointF(cx + math.cos(g) * R * 1.26, cy + math.sin(g) * R * 1.26),
                QPointF(cx + math.cos(g) * R * 1.32, cy + math.sin(g) * R * 1.32),
            )

        # Holografik tarama çizgisi
        scan_y = cy + math.sin(self.phase * 0.8) * R * 0.9
        half_w = math.sqrt(max(0.0, R * R - (scan_y - cy) ** 2))
        p.setPen(QPen(QColor(10, 160, 210, 55), 1))
        p.drawLine(QPointF(cx - half_w, scan_y), QPointF(cx + half_w, scan_y))

        # Durum yazısı
        p.setPen(QPen(QColor(20, 170, 220, 220)))
        font = QFont("Monospace", 10)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 5)
        p.setFont(font)
        p.drawText(QRectF(0, SIZE - 34, SIZE, 24),
                   Qt.AlignmentFlag.AlignCenter,
                   STATE_TEXT.get(self.state, ""))
        p.end()

    # ----------------------- sürükleme / kapatma -----------------------
    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        QApplication.quit()


if __name__ == "__main__":
    fmt = QSurfaceFormat()
    fmt.setSamples(4)        # kenar yumuşatma
    fmt.setAlphaBufferSize(8)  # şeffaf pencere için alfa kanalı
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv)
    hud = JarvisHUD()
    hud.show()
    sys.exit(app.exec())
