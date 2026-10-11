"""The window's own widgets, drawn with the current theme (theme.TOKENS)."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .i18n import ASSETS, t

ICONS = {p.stem: QSvgRenderer(str(p)) for p in (ASSETS / 'icons').glob('*.svg')}


def tok(name):
    return theme.colour(theme.TOKENS, name)


def set_role(widget, role, **props):
    widget.setProperty('role', role)
    for k, v in props.items():
        widget.setProperty(k, v)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def label(text='', role=None, rich=False, wrap=True, selectable=False):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    w.setWordWrap(wrap)
    if selectable:
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if role:
        set_role(w, role)
    return w


def button(text, role=None, slot=None, enabled=True):
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if role:
        set_role(b, role)
    if slot:
        b.clicked.connect(lambda _checked=False: slot())   # never pass Qt's 'checked' value on
    b.setEnabled(enabled)
    return b


def box(*children, spacing=12, margins=16):
    f = QFrame()
    set_role(f, 'box')
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    for c in children:
        add(lay, c)
    return f


def row(*children, spacing=12, stretch_end=True):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for c in children:
        add(lay, c)
    if stretch_end:
        lay.addStretch(1)
    return w


def add(layout, child):
    if child is None:
        return
    if isinstance(child, int):
        layout.addSpacing(child)
    else:
        layout.addWidget(child)


def pill(text, kind):
    w = label(text, f'pill-{kind}', wrap=False)
    w.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return w


def facts(pairs):
    """A two-column list of facts (term, value)."""
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(16)
    g.setVerticalSpacing(6)
    for i, (k, v) in enumerate(pairs):
        g.addWidget(label(k, 'dt', wrap=False), i, 0, Qt.AlignmentFlag.AlignTop)
        g.addWidget(label(v), i, 1)
    g.setColumnStretch(1, 1)
    return w


def glyph(text):
    g = label(text, 'glyph', wrap=False)
    g.setFixedSize(40, 48)
    g.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return g


def filecard(lead, title, hint, trailing=None):
    """A file (or look) on one line: a small glyph or icon, its name and a hint, an optional control at the end."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    lay.addWidget(lead)
    col = QVBoxLayout()
    col.setSpacing(2)
    name = label(title, wrap=True)
    f = name.font(); f.setWeight(QFont.Weight.DemiBold); name.setFont(f)
    col.addWidget(name)
    if hint:
        col.addWidget(label(hint, 'hint'))
    lay.addLayout(col, 1)
    if trailing is not None:
        lay.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


class Segmented(QWidget):
    """A row of exclusive choices (key, text)."""
    changed = Signal(str)

    def __init__(self, choices, current, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for i, (key, text) in enumerate(choices):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            set_role(b, 'seg', first=i == 0, last=i == len(choices) - 1)
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)

    def set_current(self, key):
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class DropZone(QFrame):
    """Click to choose a file, or drop one here."""
    chosen = Signal(str)

    def __init__(self, title, hint, name_filter, parent=None):
        super().__init__(parent)
        set_role(self, 'drop')
        self.filter = name_filter
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(6)
        head = label(title, 'h3')
        lay.addWidget(head)
        lay.addWidget(label(hint, 'hint'))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.browse()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.browse()
        else:
            super().keyPressEvent(e)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self.window(), '', '', self.filter)
        if path:
            self.chosen.emit(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            set_role(self, 'drop', over=True)

    def dragLeaveEvent(self, e):
        set_role(self, 'drop', over=False)

    def dropEvent(self, e):
        set_role(self, 'drop', over=False)
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.chosen.emit(urls[0])


class StepButton(QAbstractButton):
    """One step in the rail: a circle (number, tick or dash), the step's name and what it is for."""

    def __init__(self, number, title, sub, state, current, parent=None):
        super().__init__(parent)
        self.number, self.title, self.sub, self.state, self.current = number, title, sub, state, current
        self.setCursor(Qt.CursorShape.ArrowCursor if state == 'skip' else Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(52)
        self.setToolTip('')
        self.setAccessibleName(f'{number}. {title}')

    def sizeHint(self):
        return QSize(200, 52)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.current:
            p.setBrush(tok('rail_current')); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 8, 8)
        elif self.underMouse() and self.state != 'skip':
            p.setBrush(tok('rail_hover')); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 8, 8)
        if self.state == 'skip':
            p.setOpacity(0.5)
        ink = tok('rail_ink') if self.current or self.underMouse() else tok('rail_dim')
        c = QRectF(11, r.center().y() - 13, 26, 26)
        if self.current:
            p.setBrush(tok('accent')); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(c)
        else:
            pen = QPen(tok('accent') if self.state == 'done' else ink, 1.5)
            if self.state == 'ahead':
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c)
            mark = '✓' if self.state == 'done' else '–' if self.state == 'skip' else str(self.number)
            f = QFont(theme.DISPLAY); f.setPixelSize(14); f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f); p.setPen(tok('accent') if self.state == 'done' else ink)
            p.drawText(c, Qt.AlignmentFlag.AlignCenter, mark)
        p.setPen(ink)
        f = QFont(theme.BODY); f.setPixelSize(15); f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        text = QRectF(48, r.top() + 7, r.width() - 54, 22)
        p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(text.width())))
        f.setPixelSize(12); f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        sub = QRectF(48, r.top() + 27, r.width() - 54, 18)
        p.drawText(sub, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.sub, Qt.TextElideMode.ElideRight, int(sub.width())))

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


def paint_icon(p, rect, slot=None, own=None):
    """The camera's icon for a slot (vector), or the user's own 40 x 40 picture on the camera's menu colour."""
    if own is not None:
        p.fillRect(rect, tok('camera'))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(rect, own)
        pen = QPen(tok('accent'), 1.5)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0.75, 0.75, -0.75, -0.75))
    elif slot in ICONS:
        ICONS[slot].render(p, rect)


class IconTile(QWidget):
    def __init__(self, slot=None, own=None, size=40, parent=None):
        super().__init__(parent)
        self.slot, self.own = slot, own
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_icon(p, QRectF(self.rect()), self.slot, self.own)


class SlotButton(QAbstractButton):
    """A slot in the camera-menu picker: its icon, its name, and a short line under it."""

    def __init__(self, slot, name, sub, selected, own=None, parent=None):
        super().__init__(parent)
        self.slot, self.name, self.sub, self.selected, self.own = slot, name, sub, selected, own
        self.setCheckable(True)
        self.setChecked(selected)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(150, 56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(name)

    def sizeHint(self):
        return QSize(170, 60)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not self.isEnabled():
            p.setOpacity(0.38)
        if self.selected:
            p.setBrush(theme.colour(theme.TOKENS, 'select_bg')); p.setPen(QPen(tok('accent'), 1))
            p.drawRoundedRect(r, 6, 6)
        elif self.underMouse() and self.isEnabled():
            p.setBrush(tok('menu_hover')); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 6, 6)
        if self.hasFocus():
            p.setPen(QPen(tok('focus'), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        icon = QRectF(8, r.center().y() - 20, 40, 40)
        paint_icon(p, icon, self.slot, self.own if self.selected else None)
        p.setPen(tok('menu_ink'))
        f = QFont(theme.BODY); f.setPixelSize(14)
        p.setFont(f)
        text = QRectF(58, r.top() + 4, r.width() - 64, r.height() - 22)
        p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap, self.name)
        f.setPixelSize(12)
        p.setFont(f); p.setPen(tok('menu_dim'))
        p.drawText(QRectF(58, r.bottom() - 19, r.width() - 64, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.sub)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class Meter(QWidget):
    """How much of the shared room the looks take."""

    def __init__(self, fraction, over, parent=None):
        super().__init__(parent)
        self.fraction, self.over = max(0.0, min(1.0, fraction)), over
        self.setFixedHeight(12)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tok('rule')); p.drawRoundedRect(r, 6, 6)
        if self.fraction > 0:
            path = QPainterPath(); path.addRoundedRect(r, 6, 6)
            p.setClipPath(path)
            p.fillRect(QRectF(0, 0, r.width() * self.fraction, r.height()), tok('bad' if self.over else 'accent'))


class Board(QWidget):
    """The ColorChecker: each patch split, the LUT on top and the camera below, with the difference (ΔE00)."""

    def __init__(self, patches, parent=None):
        super().__init__(parent)
        self.patches = patches                    # [(name, lut rgb 0..255, camera rgb 0..255, ΔE00)]
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)

    def heightForWidth(self, w):
        return int((w - 20) / 6 * 4 + 10 * 2 + 6 * 3)

    def hasHeightForWidth(self):
        return True

    def cells(self):
        gap, pad = 6, 10
        cw = (self.width() - 2 * pad - 5 * gap) / 6
        for i in range(24):
            yield i, QRectF(pad + (i % 6) * (cw + gap), pad + (i // 6) * (cw + gap), cw, cw)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(theme.TOKENS['board'])); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        f = QFont(theme.DISPLAY); f.setPixelSize(12); f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        for i, cell in self.cells():
            name, top, bottom, d = self.patches[i]
            path = QPainterPath(); path.addRoundedRect(cell, 3, 3)
            p.save(); p.setClipPath(path)
            p.fillRect(QRectF(cell.left(), cell.top(), cell.width(), cell.height() / 2), QColor(*top))
            p.fillRect(QRectF(cell.left(), cell.center().y(), cell.width(), cell.height() / 2), QColor(*bottom))
            p.restore()
            txt = theme_num(d)
            w = p.fontMetrics().horizontalAdvance(txt) + 8
            badge = QRectF(cell.right() - w - 4, cell.bottom() - 19, w, 16)
            p.setBrush(tok('bad') if d >= 2 else QColor(0, 0, 0, 140)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(badge, 3, 3)
            p.setPen(QColor('#ffffff'))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, txt)

    def mouseMoveEvent(self, e):
        for i, cell in self.cells():
            if cell.contains(QPointF(e.position())):
                name, _, _, d = self.patches[i]
                self.setToolTip(f'{t(name)}: ΔE00 {theme_num(d)}')
                return
        self.setToolTip('')


def theme_num(d):
    from .i18n import num
    return num(d, 1)


class Compare(QWidget):
    """Two pictures of the same photo; drag the divider (or use the arrow keys) to compare them."""

    def __init__(self, left, right, left_tag, right_tag, parent=None):
        super().__init__(parent)
        self.left, self.right, self.tags = left, right, (left_tag, right_tag)
        self.x = 0.5
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(t('Divider'))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * self.left.height() / max(1, self.left.width()))

    def sizeHint(self):
        return QSize(480, self.heightForWidth(480))

    def set_x(self, x):
        self.x = max(0.0, min(1.0, x))
        self.update()

    def mousePressEvent(self, e):
        self.set_x(e.position().x() / max(1, self.width()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.set_x(e.position().x() / max(1, self.width()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Left:
            self.set_x(self.x - 0.05)
        elif e.key() == Qt.Key.Key_Right:
            self.set_x(self.x + 0.05)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath(); path.addRoundedRect(r, 8, 8)
        p.setClipPath(path)
        p.drawImage(r, self.right)
        cut = r.width() * self.x
        p.save(); p.setClipRect(QRectF(0, 0, cut, r.height()), Qt.ClipOperation.IntersectClip)
        p.drawImage(r, self.left); p.restore()
        p.setPen(QPen(QColor('#ffffff'), 2)); p.drawLine(QPointF(cut, 0), QPointF(cut, r.height()))
        p.setBrush(QColor('#ffffff')); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cut, r.height() / 2), 10, 10)
        f = QFont(theme.BODY); f.setPixelSize(12); p.setFont(f)
        for i, tag in enumerate(self.tags):
            w = p.fontMetrics().horizontalAdvance(tag) + 16
            box_ = QRectF(8 if i == 0 else r.width() - w - 8, 8, w, 20)
            p.setBrush(QColor(0, 0, 0, 155)); p.drawRoundedRect(box_, 4, 4)
            p.setPen(QColor('#ffffff')); p.drawText(box_, Qt.AlignmentFlag.AlignCenter, tag); p.setPen(Qt.PenStyle.NoPen)
        if self.hasFocus():
            p.setClipping(False)
            p.setPen(QPen(tok('focus'), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)


class Track(QWidget):
    """A turn's stages as a row of marks: done, the one running now, still to come."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps, self.done, self.running = 6, 0, False
        self.setFixedHeight(10)
        self.setMinimumWidth(120)

    def set(self, steps, done, running):
        self.steps, self.done, self.running = steps, done, running
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gap, n = 3.0, max(1, self.steps)
        w = (self.width() - gap * (n - 1)) / n
        for i in range(n):
            c = tok('rule')
            if i < self.done:
                c = tok('accent')
            elif i == self.done and self.running:
                c = tok('accent'); c.setAlphaF(0.45)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(i * (w + gap), 0, w, self.height()), 3, 3)


class LaneRow(QWidget):
    """One turn of a fit: its name and start, its stages, and how it is doing."""

    def __init__(self, lane, parent=None):
        super().__init__(parent)
        self.running = False
        g = QGridLayout(self)
        g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(0)
        self.title, self.start = label('', 'laneTitle', wrap=False), label('', 'hint', wrap=False)
        self.track = Track()
        self.status = label('', 'laneStatus', wrap=False)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(self.title, 0, 0)
        g.addWidget(self.start, 1, 0)
        g.addWidget(self.track, 0, 1, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        g.addWidget(self.status, 0, 2, 2, 1)
        g.setColumnMinimumWidth(0, 230)
        g.setColumnMinimumWidth(2, 200)
        g.setColumnStretch(1, 1)
        self.show_lane(lane, '')

    def show_lane(self, lane, status):
        from .logic import lane_steps
        k = lane['turn']
        self.title.setText(t('Turn {n} with colour after the curves', n=k) if lane['post'] else t('Turn {n}', n=k))
        self.start.setText(t('usual start') if lane['seed'] is None else t('random start, seed {seed}', seed=lane['seed']))
        self.status.setText(status)
        dim = lane['state'] in ('waiting', 'stopped')
        set_role(self.title, 'laneTitle', dim=dim)
        set_role(self.status, 'laneStatus', dim=dim)
        self.track.set(lane_steps(lane['post']), lane['done'], lane['state'] == 'running')
        self.running = lane['state'] == 'running'
        self.setAccessibleName(f'{self.title.text()}: {status}')
        self.update()

    def paintEvent(self, _):
        if not self.running:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        c = tok('accent')
        c.setAlphaF(0.07 if theme.TOKENS is theme.DAY else 0.12)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)


class Toast(QLabel):
    """A short message at the bottom of the window."""

    def __init__(self, parent):
        super().__init__(parent)
        self.hide()
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.hide)

    def show_text(self, text):
        k = theme.TOKENS
        self.setStyleSheet(f'background: {k["ink"]}; color: {k["paper"]}; border-radius: 8px; padding: 8px 16px; font-size: 14px;')
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 24)
        self.raise_()
        self.show()
        self.timer.start(2600)


def qimage_from_rgb(arr):
    """(h, w, 3) float 0..1 -> QImage (copied)."""
    import numpy as np
    a = np.ascontiguousarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8))
    h, w = a.shape[:2]
    return QImage(a.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def qimage_from_pil(img):
    img = img.convert('RGBA')
    data = img.tobytes()
    return QImage(data, img.width, img.height, 4 * img.width, QImage.Format.Format_RGBA8888).copy()
