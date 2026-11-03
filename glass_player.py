"""
Glass Player - a modern, iOS-26-style "liquid glass" music player.
Plays MP3, FLAC, OGG/Opus, WAV, M4A/AAC, WMA and more (via Qt Multimedia).

Run:    python glass_player.py
Build:  pyinstaller --onefile --windowed --name GlassPlayer glass_player.py
"""
import sys
import math
import random
import time
from pathlib import Path

from PySide6.QtCore import (Qt, QUrl, QTimer, QRectF, QPointF, QSize,
                            QVariantAnimation, QEasingCurve, QPropertyAnimation,
                            Property, QSettings)
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QLinearGradient,
                           QRadialGradient, QPixmap, QImage, QPen, QBrush,
                           QFont, QFontMetrics)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem,
                               QAbstractButton, QSlider, QFileDialog,
                               QStyledItemDelegate, QStyle, QSizeGrip,
                               QGraphicsOpacityEffect, QFrame)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData

EXTS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".wav", ".m4a", ".aac",
        ".wma", ".aiff", ".aif", ".mp4"}
DEFAULT_ACCENT = QColor(88, 120, 255)
FONT = "Segoe UI Variable Display"


# ───────────────────────────── helpers ─────────────────────────────
def fmt_time(ms):
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


def with_alpha(c, a):
    c = QColor(c)
    c.setAlpha(max(0, min(255, int(a))))
    return c


def draw_glass(p, rect, radius, strength=1.0):
    """Frosted glass card: translucent fill, specular edge, inner glow."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(rect)
    fill = QLinearGradient(r.topLeft(), r.bottomRight())
    fill.setColorAt(0, QColor(255, 255, 255, int(46 * strength)))
    fill.setColorAt(1, QColor(255, 255, 255, int(16 * strength)))
    p.setPen(Qt.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(r, radius, radius)
    # specular rim light
    rim = QLinearGradient(r.topLeft(), r.bottomRight())
    rim.setColorAt(0.0, QColor(255, 255, 255, int(150 * strength)))
    rim.setColorAt(0.35, QColor(255, 255, 255, int(30 * strength)))
    rim.setColorAt(0.7, QColor(255, 255, 255, int(20 * strength)))
    rim.setColorAt(1.0, QColor(255, 255, 255, int(90 * strength)))
    p.setPen(QPen(QBrush(rim), 1.2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(r.adjusted(0.6, 0.6, -0.6, -0.6), radius, radius)
    # top sheen
    sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + r.height() * 0.5))
    sheen.setColorAt(0, QColor(255, 255, 255, int(26 * strength)))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(sheen)
    p.drawRoundedRect(r.adjusted(1, 1, -1, -r.height() * 0.5), radius, radius)
    p.restore()


def average_color(img):
    small = img.scaled(12, 12, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    r = g = b = n = 0
    for x in range(small.width()):
        for y in range(small.height()):
            c = small.pixelColor(x, y)
            r += c.red(); g += c.green(); b += c.blue(); n += 1
    c = QColor(r // n, g // n, b // n)
    h, s, v, _ = c.getHsv()
    return QColor.fromHsv(max(h, 0), min(255, max(s, 130) + 40), max(150, min(v + 40, 255)))


def make_default_art(size=400):
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(120, 140, 255))
    g.setColorAt(1, QColor(200, 110, 255))
    p.fillRect(img.rect(), g)
    p.setPen(QPen(QColor(255, 255, 255, 230), size * 0.035, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(QColor(255, 255, 255, 230))
    s = size
    p.drawEllipse(QPointF(s * 0.40, s * 0.68), s * 0.09, s * 0.07)
    p.drawLine(QPointF(s * 0.48, s * 0.66), QPointF(s * 0.48, s * 0.30))
    path = QPainterPath(QPointF(s * 0.48, s * 0.30))
    path.cubicTo(s * 0.60, s * 0.34, s * 0.66, s * 0.40, s * 0.64, s * 0.52)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.end()
    return QPixmap.fromImage(img)


# ───────────────────────────── widgets ─────────────────────────────
class GlassPanel(QWidget):
    def __init__(self, radius=26, strength=1.0, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.strength = strength

    def paintEvent(self, _):
        p = QPainter(self)
        draw_glass(p, self.rect(), self.radius, self.strength)


class ElideLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(10)
        self.setText(text)

    def setFullText(self, t):
        self._full = t
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        self.setText(QFontMetrics(self.font()).elidedText(
            self._full, Qt.ElideRight, max(10, self.width())))


class GlassButton(QAbstractButton):
    """Vector-icon glass button with animated hover / press / morph."""

    def __init__(self, kind, size=44, circle=True, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.circle = circle
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._hover = 0.0
        self._press = 0.0
        self._morph = 0.0      # play -> pause
        self._on = 0.0         # checked glow
        self._anims = {}

    def _anim(self, name, end, ms=180, curve=QEasingCurve.OutCubic):
        a = self._anims.get(name)
        if a:
            a.stop()
        a = QVariantAnimation(self)
        a.setStartValue(getattr(self, "_" + name))
        a.setEndValue(end)
        a.setDuration(ms)
        a.setEasingCurve(curve)
        a.valueChanged.connect(lambda v, n=name: (setattr(self, "_" + n, v), self.update()))
        a.start()
        self._anims[name] = a

    def set_playing(self, playing):
        self._anim("morph", 1.0 if playing else 0.0, 260)

    def setChecked(self, c):
        super().setChecked(c)
        self._anim("on", 1.0 if c else 0.0, 220)

    def enterEvent(self, e):
        self._anim("hover", 1.0)

    def leaveEvent(self, e):
        self._anim("hover", 0.0, 260)

    def mousePressEvent(self, e):
        self._anim("press", 1.0, 90)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._anim("press", 0.0, 380, QEasingCurve.OutBack)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        scale = 1.0 - 0.10 * self._press + 0.04 * self._hover
        p.translate(w / 2, h / 2)
        p.scale(scale, scale)
        p.translate(-w / 2, -h / 2)
        r = QRectF(1, 1, w - 2, h - 2)
        if self.circle:
            a = 0.55 + 0.6 * self._hover + 0.5 * self._on
            draw_glass(p, r, r.width() / 2, a)
        elif self._hover > 0.01:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, int(40 * self._hover)))
            p.drawRoundedRect(r, 10, 10)

        s = min(w, h)
        col = QColor(255, 255, 255, int(200 + 55 * max(self._hover, self._on)))
        p.translate(w / 2, h / 2)
        pen = QPen(col, s * 0.058, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        k = self.kind

        if k == "play":
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            # triangle fades/scales out, bars in
            p.save()
            p.setOpacity(1 - self._morph)
            p.scale(1 - 0.4 * self._morph, 1 - 0.4 * self._morph)
            t = QPainterPath()
            t.moveTo(-s * 0.13, -s * 0.2)
            t.lineTo(s * 0.22, 0)
            t.lineTo(-s * 0.13, s * 0.2)
            t.closeSubpath()
            p.drawPath(t)
            p.restore()
            p.save()
            p.setOpacity(self._morph)
            p.scale(0.6 + 0.4 * self._morph, 0.6 + 0.4 * self._morph)
            bw = s * 0.095
            p.drawRoundedRect(QRectF(-s * 0.17, -s * 0.19, bw, s * 0.38), bw / 2, bw / 2)
            p.drawRoundedRect(QRectF(s * 0.075, -s * 0.19, bw, s * 0.38), bw / 2, bw / 2)
            p.restore()
        elif k in ("next", "prev"):
            if k == "prev":
                p.scale(-1, 1)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            for dx in (-s * 0.14, 0.0):
                t = QPainterPath()
                t.moveTo(dx - s * 0.02, -s * 0.15)
                t.lineTo(dx + s * 0.16, 0)
                t.lineTo(dx - s * 0.02, s * 0.15)
                t.closeSubpath()
                p.drawPath(t)
            p.drawRoundedRect(QRectF(s * 0.17, -s * 0.15, s * 0.05, s * 0.3), 2, 2)
        elif k == "shuffle":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            a = s * 0.20
            path = QPainterPath(QPointF(-a, -a * 0.7))
            path.cubicTo(0, -a * 0.7, 0, a * 0.7, a, a * 0.7)
            p.drawPath(path)
            path = QPainterPath(QPointF(-a, a * 0.7))
            path.cubicTo(0, a * 0.7, 0, -a * 0.7, a, -a * 0.7)
            p.drawPath(path)
            for y in (-a * 0.7, a * 0.7):
                p.drawPolyline([QPointF(a - s * 0.07, y - s * 0.06), QPointF(a, y),
                                QPointF(a - s * 0.07, y + s * 0.06)])
        elif k in ("repeat", "repeat1"):
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            a = s * 0.2
            p.drawRoundedRect(QRectF(-a, -a * 0.65, a * 2, a * 1.3), a * 0.65, a * 0.65)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.setPen(QPen(self.palette().window().color(), 0))
            tri = QPainterPath()
            tri.moveTo(a * 0.2, -a * 0.65 - s * 0.07)
            tri.lineTo(a * 0.2 + s * 0.11, -a * 0.65)
            tri.lineTo(a * 0.2, -a * 0.65 + s * 0.07)
            tri.closeSubpath()
            p.setPen(Qt.NoPen)
            p.drawPath(tri)
            if k == "repeat1":
                f = QFont(FONT, max(6, int(s * 0.17)), QFont.Bold)
                p.setFont(f)
                p.setPen(col)
                p.drawText(QRectF(-a, -a, a * 2, a * 2), Qt.AlignCenter, "1")
        elif k == "add":
            p.setPen(pen)
            a = s * 0.18
            p.drawLine(QPointF(-a, 0), QPointF(a, 0))
            p.drawLine(QPointF(0, -a), QPointF(0, a))
        elif k == "folder":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            a = s * 0.2
            f = QPainterPath(QPointF(-a, -a * 0.6))
            f.lineTo(-a * 0.25, -a * 0.6)
            f.lineTo(0, -a * 0.3)
            f.lineTo(a, -a * 0.3)
            f.lineTo(a, a * 0.75)
            f.lineTo(-a, a * 0.75)
            f.closeSubpath()
            p.drawPath(f)
        elif k in ("vol_lo", "vol_hi"):
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            a = s * 0.2
            sp = QPainterPath()
            sp.moveTo(-a, -a * 0.35); sp.lineTo(-a * 0.45, -a * 0.35)
            sp.lineTo(a * 0.1, -a * 0.8); sp.lineTo(a * 0.1, a * 0.8)
            sp.lineTo(-a * 0.45, a * 0.35); sp.lineTo(-a, a * 0.35)
            sp.closeSubpath()
            p.drawPath(sp)
            if k == "vol_hi":
                p.setPen(QPen(col, s * 0.05, Qt.SolidLine, Qt.RoundCap))
                p.setBrush(Qt.NoBrush)
                p.drawArc(QRectF(-a * 0.5, -a * 0.6, a * 1.5, a * 1.2), -45 * 16, 90 * 16)


class TrafficLight(QAbstractButton):
    def __init__(self, color, glyph, parent=None):
        super().__init__(parent)
        self.color, self.glyph = QColor(color), glyph
        self.setFixedSize(16, 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._h = 0.0
        self._a = QVariantAnimation(self)
        self._a.setDuration(150)
        self._a.valueChanged.connect(lambda v: (setattr(self, "_h", v), self.update()))

    def enterEvent(self, e):
        self._a.stop(); self._a.setStartValue(self._h); self._a.setEndValue(1.0); self._a.start()

    def leaveEvent(self, e):
        self._a.stop(); self._a.setStartValue(self._h); self._a.setEndValue(0.0); self._a.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.color
        g = QRadialGradient(QPointF(8, 6), 10)
        g.setColorAt(0, c.lighter(130))
        g.setColorAt(1, c)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(g)
        p.drawEllipse(QRectF(1, 1, 14, 14))
        if self._h > 0.01:
            p.setPen(QPen(QColor(0, 0, 0, int(150 * self._h)), 1.5, Qt.SolidLine, Qt.RoundCap))
            if self.glyph == "x":
                p.drawLine(5, 5, 11, 11); p.drawLine(11, 5, 5, 11)
            else:
                p.drawLine(5, 8, 11, 8)


class GlassSlider(QSlider):
    """Pill slider with an animated knob."""

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setFixedHeight(24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self._k = 0.0
        self._drag = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_k", v), self.update()))
        self.accent = QColor(255, 255, 255)

    def _to(self, v):
        self._anim.stop(); self._anim.setStartValue(self._k); self._anim.setEndValue(v); self._anim.start()

    def enterEvent(self, e):
        self._to(1.0)

    def leaveEvent(self, e):
        if not self._drag:
            self._to(0.0)

    def _val_at(self, x):
        m = 8
        f = (x - m) / max(1, self.width() - 2 * m)
        f = min(1, max(0, f))
        return int(self.minimum() + f * (self.maximum() - self.minimum()))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = True
            self.setSliderDown(True)
            self.setValue(self._val_at(e.position().x()))
            self.sliderMoved.emit(self.value())

    def mouseMoveEvent(self, e):
        if self._drag:
            self.setValue(self._val_at(e.position().x()))
            self.sliderMoved.emit(self.value())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.setSliderDown(False)
            self.sliderReleased.emit()
            if not self.underMouse():
                self._to(0.0)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = 8
        th = 5 + 3 * self._k
        cy = self.height() / 2
        track = QRectF(m, cy - th / 2, self.width() - 2 * m, th)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 45))
        p.drawRoundedRect(track, th / 2, th / 2)
        rng = max(1, self.maximum() - self.minimum())
        f = (self.value() - self.minimum()) / rng
        x = track.left() + track.width() * f
        fill = QRectF(track.left(), track.top(), max(th, x - track.left()), th)
        g = QLinearGradient(fill.topLeft(), fill.topRight())
        g.setColorAt(0, QColor(255, 255, 255, 190))
        g.setColorAt(1, QColor(255, 255, 255, 250))
        p.setBrush(g)
        p.drawRoundedRect(fill, th / 2, th / 2)
        kr = 8 * self._k
        if kr > 0.5:
            p.setBrush(QColor(0, 0, 0, 40))
            p.drawEllipse(QPointF(x, cy + 1.5), kr + 1, kr + 1)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(x, cy), kr, kr)


class ArtView(QWidget):
    """Album art with soft shadow, crossfade, pop animation and breathing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.cur = make_default_art()
        self.prev = None
        self._fade = 1.0
        self._pop = 1.0
        self.breath = 0.0
        self._fa = QVariantAnimation(self)
        self._fa.setDuration(500)
        self._fa.setStartValue(0.0); self._fa.setEndValue(1.0)
        self._fa.valueChanged.connect(lambda v: (setattr(self, "_fade", v), self.update()))
        self._pa = QVariantAnimation(self)
        self._pa.setDuration(650)
        self._pa.setStartValue(0.90); self._pa.setEndValue(1.0)
        self._pa.setEasingCurve(QEasingCurve.OutBack)
        self._pa.valueChanged.connect(lambda v: (setattr(self, "_pop", v), self.update()))

    def set_art(self, pm):
        self.prev, self.cur = self.cur, (pm if pm and not pm.isNull() else make_default_art())
        self._fa.stop(); self._fa.start()
        self._pa.stop(); self._pa.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        side = min(self.width(), self.height()) - 40
        sc = self._pop * (1 + 0.012 * self.breath)
        c = QPointF(self.width() / 2, self.height() / 2)
        p.translate(c); p.scale(sc, sc); p.translate(-c)
        r = QRectF(c.x() - side / 2, c.y() - side / 2, side, side)
        # layered soft shadow
        p.setPen(Qt.NoPen)
        for i in range(14, 0, -1):
            p.setBrush(QColor(0, 0, 0, 5))
            p.drawRoundedRect(r.adjusted(-i * 1.6, -i * 0.6 + 8, i * 1.6, i * 2.2 + 8), 30 + i, 30 + i)
        clip = QPainterPath()
        clip.addRoundedRect(r, 30, 30)
        p.save()
        p.setClipPath(clip)
        if self.prev is not None and self._fade < 1:
            p.drawPixmap(r.toRect(), self.prev.scaled(r.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        p.setOpacity(self._fade)
        pm = self.cur.scaled(r.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap(r.toRect().topLeft(), pm,
                     QRectF((pm.width() - r.width()) / 2, (pm.height() - r.height()) / 2,
                            r.width(), r.height()).toRect())
        p.restore()
        p.setOpacity(1)
        glow = QLinearGradient(r.topLeft(), r.bottomRight())
        glow.setColorAt(0, QColor(255, 255, 255, 140))
        glow.setColorAt(0.5, QColor(255, 255, 255, 0))
        glow.setColorAt(1, QColor(255, 255, 255, 70))
        p.setPen(QPen(QBrush(glow), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 30, 30)


class TrackDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index):
        return QSize(100, 58)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 3, -6, -3)
        current = index.row() == self.win.index
        hover = bool(option.state & QStyle.State_MouseOver)
        if current:
            a = QColor(255, 255, 255, 54)
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.setBrush(a)
            p.drawRoundedRect(r, 16, 16)
        elif hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 24))
            p.drawRoundedRect(r, 16, 16)

        left = r.left() + 14
        if current:
            # animated equalizer
            playing = self.win.player.playbackState() == QMediaPlayer.PlayingState
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 235))
            for i in range(3):
                hgt = 6 + (10 * (0.5 + 0.5 * math.sin(self.win.t * (5 + i * 1.7) + i)) if playing else 3)
                p.drawRoundedRect(QRectF(left + i * 6, r.center().y() + 8 - hgt, 3.5, hgt), 1.7, 1.7)
            left += 28
        else:
            p.setPen(QColor(255, 255, 255, 110))
            p.setFont(QFont(FONT, 9))
            p.drawText(QRectF(left - 2, r.top(), 24, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       str(index.row() + 1))
            left += 28

        title = index.data(Qt.UserRole + 1)
        sub = index.data(Qt.UserRole + 2)
        tw = r.right() - left - 12
        p.setPen(QColor(255, 255, 255, 245 if current else 220))
        f = QFont(FONT, 10); f.setWeight(QFont.DemiBold if current else QFont.Normal)
        p.setFont(f)
        p.drawText(QRectF(left, r.top() + 8, tw, 20), Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(title, Qt.ElideRight, int(tw)))
        p.setPen(QColor(255, 255, 255, 120))
        f2 = QFont(FONT, 8)
        p.setFont(f2)
        p.drawText(QRectF(left, r.top() + 28, tw, 18), Qt.AlignVCenter,
                   QFontMetrics(f2).elidedText(sub, Qt.ElideRight, int(tw)))
        p.restore()


# ───────────────────────────── main window ─────────────────────────────
class GlassPlayer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glass Player")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.resize(1040, 660)
        self.setMinimumSize(880, 580)

        self.tracks = []
        self.index = -1
        self.history = []
        self.shuffle = False
        self.repeat = 0           # 0 off, 1 all, 2 one
        self.t = 0.0
        self._t0 = time.perf_counter()
        self.accent = QColor(DEFAULT_ACCENT)
        self.accent2 = QColor(190, 100, 255)
        self._blur_pm = None
        self._seeking = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.7)

        self._build_ui()
        self._wire()

        self._acc_anim = QVariantAnimation(self)
        self._acc_anim.setDuration(900)
        self._acc_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._acc_anim.valueChanged.connect(self._set_accent_mix)
        self._from = (QColor(self.accent), QColor(self.accent2))
        self._to = (QColor(self.accent), QColor(self.accent2))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

        self.setWindowOpacity(0.0)
        fa = QPropertyAnimation(self, b"windowOpacity", self)
        fa.setDuration(550); fa.setStartValue(0.0); fa.setEndValue(1.0)
        fa.setEasingCurve(QEasingCurve.OutCubic)
        fa.start()
        self._fade_in = fa

    # ----- UI -----
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 22)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.btn_close = TrafficLight("#ff5f57", "x")
        self.btn_min = TrafficLight("#febc2e", "-")
        self.btn_close.clicked.connect(self.close)
        self.btn_min.clicked.connect(self.showMinimized)
        top.addWidget(self.btn_close)
        top.addWidget(self.btn_min)
        top.addStretch()
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, 1)

        # Library panel
        lib = GlassPanel(28, 0.9)
        lib.setFixedWidth(330)
        lv = QVBoxLayout(lib)
        lv.setContentsMargins(14, 16, 14, 14)
        lv.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 0, 0)
        title = QLabel("Library")
        title.setStyleSheet(f"color:white;font:700 17pt '{FONT}';background:transparent;")
        head.addWidget(title)
        head.addStretch()
        self.btn_add = GlassButton("add", 34)
        self.btn_folder = GlassButton("folder", 34)
        self.btn_add.setToolTip("Add files")
        self.btn_folder.setToolTip("Add folder")
        head.addWidget(self.btn_add)
        head.addWidget(self.btn_folder)
        lv.addLayout(head)
        self.count_lbl = QLabel("Drop music here")
        self.count_lbl.setStyleSheet(f"color:rgba(255,255,255,120);font:9pt '{FONT}';padding-left:8px;background:transparent;")
        lv.addWidget(self.count_lbl)

        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setItemDelegate(TrackDelegate(self))
        self.list.setStyleSheet("""
            QListWidget{background:transparent;border:none;outline:none;}
            QListWidget::item{background:transparent;}
            QScrollBar:vertical{background:transparent;width:8px;margin:4px 0;}
            QScrollBar::handle:vertical{background:rgba(255,255,255,70);border-radius:3px;min-height:30px;}
            QScrollBar::handle:vertical:hover{background:rgba(255,255,255,120);}
            QScrollBar::add-line,QScrollBar::sub-line{height:0;}
            QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}
        """)
        lv.addWidget(self.list, 1)
        body.addWidget(lib)

        # Now playing panel
        now = GlassPanel(34, 0.8)
        nv = QVBoxLayout(now)
        nv.setContentsMargins(34, 22, 34, 26)
        nv.setSpacing(4)
        self.art = ArtView()
        nv.addWidget(self.art, 1)

        self.lbl_title = ElideLabel("Nothing playing")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setStyleSheet(f"color:white;font:700 21pt '{FONT}';background:transparent;")
        self.lbl_artist = ElideLabel("Add some music to get started")
        self.lbl_artist.setAlignment(Qt.AlignCenter)
        self.lbl_artist.setStyleSheet(f"color:rgba(255,255,255,150);font:12pt '{FONT}';background:transparent;")
        self.fx_t = QGraphicsOpacityEffect(self.lbl_title)
        self.fx_a = QGraphicsOpacityEffect(self.lbl_artist)
        self.lbl_title.setGraphicsEffect(self.fx_t)
        self.lbl_artist.setGraphicsEffect(self.fx_a)
        nv.addWidget(self.lbl_title)
        nv.addWidget(self.lbl_artist)
        nv.addSpacing(8)

        seek = QHBoxLayout()
        self.lbl_pos = QLabel("0:00")
        self.lbl_dur = QLabel("0:00")
        for l in (self.lbl_pos, self.lbl_dur):
            l.setStyleSheet(f"color:rgba(255,255,255,140);font:9pt '{FONT}';background:transparent;")
            l.setFixedWidth(40)
        self.lbl_dur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.seek = GlassSlider()
        self.seek.setRange(0, 0)
        seek.addWidget(self.lbl_pos)
        seek.addWidget(self.seek, 1)
        seek.addWidget(self.lbl_dur)
        nv.addLayout(seek)

        ctr = QHBoxLayout()
        ctr.setSpacing(16)
        self.btn_shuffle = GlassButton("shuffle", 40)
        self.btn_shuffle.setCheckable(True)
        self.btn_prev = GlassButton("prev", 52)
        self.btn_play = GlassButton("play", 70)
        self.btn_next = GlassButton("next", 52)
        self.btn_repeat = GlassButton("repeat", 40)
        self.btn_repeat.setCheckable(True)
        ctr.addStretch()
        for b in (self.btn_shuffle, self.btn_prev, self.btn_play, self.btn_next, self.btn_repeat):
            ctr.addWidget(b)
        ctr.addStretch()
        nv.addLayout(ctr)

        vol = QHBoxLayout()
        vol.setContentsMargins(60, 4, 60, 0)
        vol.addWidget(GlassButton("vol_lo", 26, circle=False))
        self.vol = GlassSlider()
        self.vol.setRange(0, 100)
        self.vol.setValue(70)
        vol.addWidget(self.vol, 1)
        vol.addWidget(GlassButton("vol_hi", 26, circle=False))
        nv.addLayout(vol)
        body.addWidget(now, 1)

        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        self.grip = grip

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.grip.move(self.width() - 18, self.height() - 18)
        self._blur_pm = None

    # ----- wiring -----
    def _wire(self):
        self.btn_add.clicked.connect(self.add_files)
        self.btn_folder.clicked.connect(self.add_folder)
        self.btn_play.clicked.connect(self.toggle)
        self.btn_next.clicked.connect(self.next)
        self.btn_prev.clicked.connect(self.prev)
        self.btn_shuffle.toggled.connect(self._set_shuffle)
        self.btn_repeat.clicked.connect(self._cycle_repeat)
        self.list.itemClicked.connect(lambda it: self.play_index(self.list.row(it)))
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.metaDataChanged.connect(self._on_meta)
        self.player.errorOccurred.connect(self._on_error)

    # ----- playlist -----
    def _add_paths(self, paths):
        files = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                files += sorted(f for f in p.rglob("*") if f.suffix.lower() in EXTS)
            elif p.suffix.lower() in EXTS:
                files.append(p)
        had = len(self.tracks)
        for f in files:
            self.tracks.append(str(f))
            it = QListWidgetItem()
            it.setData(Qt.UserRole + 1, f.stem)
            it.setData(Qt.UserRole + 2, f"{f.suffix[1:].upper()} • {f.parent.name}")
            self.list.addItem(it)
        self._update_count()
        if files and had == 0:
            self.play_index(0)

    def _update_count(self):
        n = len(self.tracks)
        self.count_lbl.setText(f"{n} song{'s' if n != 1 else ''}" if n else "Drop music here")

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add music", "", f"Audio ({exts});;All files (*)")
        if files:
            self._add_paths(files)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder")
        if d:
            self._add_paths([d])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self._add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ----- playback -----
    def play_index(self, i, push=True):
        if not (0 <= i < len(self.tracks)):
            return
        if push and self.index >= 0 and self.index != i:
            self.history.append(self.index)
        self.index = i
        self.player.setSource(QUrl.fromLocalFile(self.tracks[i]))
        self.player.play()
        it = self.list.item(i)
        self.list.scrollToItem(it, QListWidget.PositionAtCenter)
        self._set_text(it.data(Qt.UserRole + 1), it.data(Qt.UserRole + 2))
        self.art.set_art(None)
        self._set_accent(DEFAULT_ACCENT, QColor(190, 100, 255), None)
        self.list.viewport().update()

    def toggle(self):
        if not self.tracks:
            self.add_files()
            return
        if self.index < 0:
            self.play_index(0)
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def next(self):
        if not self.tracks:
            return
        if self.shuffle and len(self.tracks) > 1:
            n = self.index
            while n == self.index:
                n = random.randrange(len(self.tracks))
        else:
            n = (self.index + 1) % len(self.tracks)
        self.play_index(n)

    def prev(self):
        if not self.tracks:
            return
        if self.player.position() > 3000 or not self.history:
            self.player.setPosition(0)
            return
        self.play_index(self.history.pop(), push=False)

    def _set_shuffle(self, on):
        self.shuffle = on

    def _cycle_repeat(self):
        self.repeat = (self.repeat + 1) % 3
        self.btn_repeat.kind = "repeat1" if self.repeat == 2 else "repeat"
        self.btn_repeat.setChecked(self.repeat != 0)
        self.btn_repeat.update()

    # ----- player callbacks -----
    def _on_pos(self, ms):
        self.lbl_pos.setText(fmt_time(ms))
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)

    def _on_dur(self, ms):
        self.seek.setRange(0, ms)
        self.lbl_dur.setText(fmt_time(ms))

    def _on_state(self, st):
        self.btn_play.set_playing(st == QMediaPlayer.PlayingState)
        self.list.viewport().update()

    def _on_status(self, st):
        if st == QMediaPlayer.EndOfMedia:
            if self.repeat == 2:
                self.player.setPosition(0)
                self.player.play()
            elif self.index == len(self.tracks) - 1 and self.repeat == 0 and not self.shuffle:
                self.player.stop()
            else:
                self.next()

    def _on_error(self, err, msg):
        self.lbl_artist.setFullText(f"Can't play this file: {msg}")

    def _on_meta(self):
        md = self.player.metaData()
        title = md.value(QMediaMetaData.Title)
        artist = md.value(QMediaMetaData.ContributingArtist) or md.value(QMediaMetaData.AlbumArtist)
        album = md.value(QMediaMetaData.AlbumTitle)
        if isinstance(artist, list):
            artist = ", ".join(map(str, artist))
        it = self.list.item(self.index) if self.index >= 0 else None
        if it is not None:
            if title:
                it.setData(Qt.UserRole + 1, str(title))
            sub = " • ".join(str(x) for x in (artist, album) if x)
            if sub:
                it.setData(Qt.UserRole + 2, sub)
            self._set_text(it.data(Qt.UserRole + 1), it.data(Qt.UserRole + 2), animate=False)
        img = md.value(QMediaMetaData.CoverArtImage) or md.value(QMediaMetaData.ThumbnailImage)
        if isinstance(img, QImage) and not img.isNull():
            self.art.set_art(QPixmap.fromImage(img))
            a = average_color(img)
            h, s, v, _ = a.getHsv()
            self._set_accent(a, QColor.fromHsv((h + 50) % 360, s, v), img)
        self.list.viewport().update()

    # ----- visuals -----
    def _set_text(self, title, sub, animate=True):
        self.lbl_title.setFullText(title)
        self.lbl_artist.setFullText(sub)
        if animate:
            for fx in (self.fx_t, self.fx_a):
                a = QPropertyAnimation(fx, b"opacity", self)
                a.setDuration(550)
                a.setStartValue(0.0); a.setEndValue(1.0)
                a.setEasingCurve(QEasingCurve.OutCubic)
                a.start(QPropertyAnimation.DeleteWhenStopped)

    def _set_accent(self, c1, c2, img):
        self._from = (QColor(self.accent), QColor(self.accent2))
        self._to = (QColor(c1), QColor(c2))
        self._acc_anim.stop()
        self._acc_anim.setStartValue(0.0)
        self._acc_anim.setEndValue(1.0)
        self._acc_anim.start()
        if img is not None:
            small = img.scaled(24, 24, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self._art_small = QPixmap.fromImage(small)
        else:
            self._art_small = None
        self._blur_pm = None

    def _set_accent_mix(self, f):
        def mix(a, b):
            return QColor(int(a.red() + (b.red() - a.red()) * f),
                          int(a.green() + (b.green() - a.green()) * f),
                          int(a.blue() + (b.blue() - a.blue()) * f))
        self.accent = mix(self._from[0], self._to[0])
        self.accent2 = mix(self._from[1], self._to[1])

    def _tick(self):
        self.t = time.perf_counter() - self._t0
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        target = math.sin(self.t * 2.2) if playing else 0.0
        self.art.breath += (target - self.art.breath) * 0.08
        self.art.update()
        if playing:
            self.list.viewport().update()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 30, 30)
        p.setClipPath(clip)

        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0, self.accent.darker(380))
        base.setColorAt(1, self.accent2.darker(420))
        p.fillRect(self.rect(), base)

        if getattr(self, "_art_small", None) is not None:
            if self._blur_pm is None or self._blur_pm.size() != self.size():
                self._blur_pm = self._art_small.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                                      Qt.SmoothTransformation)
            p.setOpacity(0.28)
            p.drawPixmap(0, 0, self._blur_pm)
            p.setOpacity(1)

        # drifting light blobs
        blobs = [
            (0.25 + 0.18 * math.sin(self.t * 0.35), 0.30 + 0.20 * math.cos(self.t * 0.28), 0.55, self.accent, 150),
            (0.75 + 0.15 * math.cos(self.t * 0.30), 0.35 + 0.22 * math.sin(self.t * 0.41), 0.50, self.accent2, 130),
            (0.50 + 0.25 * math.sin(self.t * 0.22 + 1), 0.85 + 0.10 * math.cos(self.t * 0.36), 0.60, self.accent.lighter(130), 110),
        ]
        for fx, fy, fr, col, al in blobs:
            c = QPointF(fx * w, fy * h)
            rad = fr * max(w, h)
            g = QRadialGradient(c, rad)
            g.setColorAt(0, with_alpha(col, al))
            g.setColorAt(1, with_alpha(col, 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawRect(self.rect())

        # window glass edge
        p.setClipping(False)
        edge = QLinearGradient(0, 0, w, h)
        edge.setColorAt(0, QColor(255, 255, 255, 120))
        edge.setColorAt(0.5, QColor(255, 255, 255, 25))
        edge.setColorAt(1, QColor(255, 255, 255, 80))
        p.setPen(QPen(QBrush(edge), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0.7, 0.7, w - 1.4, h - 1.4), 30, 30)

    # ----- window behaviour -----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Space:
            self.toggle()
        elif k == Qt.Key_Right:
            self.player.setPosition(self.player.position() + 5000)
        elif k == Qt.Key_Left:
            self.player.setPosition(max(0, self.player.position() - 5000))
        elif k == Qt.Key_Up:
            self.vol.setValue(min(100, self.vol.value() + 5))
        elif k == Qt.Key_Down:
            self.vol.setValue(max(0, self.vol.value() - 5))
        elif k == Qt.Key_N:
            self.next()
        elif k == Qt.Key_P:
            self.prev()
        else:
            super().keyPressEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Glass Player")
    win = GlassPlayer()
    win.show()
    # open files passed on the command line / "Open with"
    if len(sys.argv) > 1:
        win._add_paths(sys.argv[1:])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
