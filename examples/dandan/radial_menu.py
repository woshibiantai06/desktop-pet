"""Locally drawn translucent glass-style controls; no screenshots or real backdrop blur."""
import math,time,sys,ctypes
from PySide6.QtCore import Qt,QTimer,QPoint,QPointF,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QBrush,QLinearGradient,QRadialGradient,QFont,QPainterPath,QRegion,QCursor
from PySide6.QtWidgets import QWidget

class RadialMenu(QWidget):
    ITEMS=[('sit_front','坐下'),('lie_front','趴下'),('stand_front','站起来'),('free','自由活动'),('exit','退出')]
    def __init__(self,owner):
        super().__init__(owner,Qt.WindowType.Tool|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.FramelessWindowHint|Qt.WindowType.NoDropShadowWindowHint)
        self.owner=owner;self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.timer=QTimer(self);self.timer.timeout.connect(self.animate)
        self.hover=-1;self.amount=[0.0]*5;self.targets=[];self.centers=[];self.opened=0
        self.closing=False;self.visibility=1.0
        self.setAccessibleName('小狗环形控制菜单')

    def popup_at(self,center):
        screen=self.owner.screen().availableGeometry();margin=33
        safe=screen.adjusted(margin,margin,-margin,-margin)
        # One regular 96-degree arc. Translate the WHOLE arc at screen boundaries.
        radius=138
        points=[QPointF(center.x()+radius*math.cos(math.radians(a)),center.y()+radius*math.sin(math.radians(a))) for a in [-96,-72,-48,-24,0]]
        dx=max(0,safe.left()-min(p.x() for p in points))+min(0,safe.right()-max(p.x() for p in points))
        dy=max(0,safe.top()-min(p.y() for p in points))+min(0,safe.bottom()-max(p.y() for p in points))
        points=[p+QPointF(dx,dy) for p in points]
        left=max(screen.left(),int(min([p.x() for p in points]+[center.x()])-margin))
        top=max(screen.top(),int(min([p.y() for p in points]+[center.y()])-margin))
        right=min(screen.right(),int(max([p.x() for p in points]+[center.x()])+margin))
        bottom=min(screen.bottom(),int(max([p.y() for p in points]+[center.y()])+margin))
        self.setGeometry(left,top,right-left+1,bottom-top+1)
        self.origin=QPointF(center.x()-left,center.y()-top)
        self.targets=[p-QPointF(left,top) for p in points]
        self.opened=time.perf_counter();self.hover=-1;self.amount=[0.0]*5
        self.closing=False;self.visibility=1.0
        self.mouse_down=False
        self.show();self.raise_();self.setFocus();self.animate();self.timer.start(16)

    def animate(self):
        if self.closing:
            t=min(1,(time.perf_counter()-self.close_started)/.26)
            ease=t*t*(3-2*t)
            self.visibility=1-ease
            self.centers=[q+(self.origin-q)*ease*.65 for q in self.close_centers]
            region=QRegion()
            for c in self.centers:
                region=region.united(QRegion(int(c.x()-28),int(c.y()-28),56,56,QRegion.RegionType.Ellipse))
            self.setMask(region)
            self.update()
            if t>=1:self.hide()
            return
        elapsed=time.perf_counter()-self.opened;t=min(1,elapsed/.38)
        p=1+2.70158*(t-1)**3+1.70158*(t-1)**2
        self.centers=[self.origin+(q-self.origin)*p for q in self.targets]
        self.hover=self.hit(self.mapFromGlobal(QCursor.pos()))
        if sys.platform=='win32':
            down=bool(ctypes.windll.user32.GetAsyncKeyState(1)&0x8000)
            escape=bool(ctypes.windll.user32.GetAsyncKeyState(27)&0x8000)
            if escape or (down and not self.mouse_down and self.hover<0):
                self.dismiss();return
            self.mouse_down=down
        region=QRegion()
        for i,c in enumerate(self.centers):
            self.amount[i]+=(float(self.hover==i)-self.amount[i])*.23
            region=region.united(QRegion(int(c.x()-32),int(c.y()-32),64,64,QRegion.RegionType.Ellipse))
        self.setMask(region);self.update()

    def hit(self,pos):
        for i,c in enumerate(self.centers):
            if math.hypot(pos.x()-c.x(),pos.y()-c.y())<=21+self.amount[i]*3:return i
        return -1

    def dismiss(self):
        if not self.isVisible() or self.closing:return
        self.closing=True;self.close_started=time.perf_counter()
        self.close_centers=list(self.centers)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)

    def showEvent(self,e):
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,False)
        super().showEvent(e)

    def mouseMoveEvent(self,e):
        self.hover=self.hit(e.position());self.setCursor(Qt.CursorShape.PointingHandCursor if self.hover>=0 else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self,e):self.hover=-1

    def mousePressEvent(self,e):
        if self.closing:return
        i=self.hit(e.position())
        if e.button()==Qt.MouseButton.LeftButton and i>=0:
            action=self.ITEMS[i][0];self.dismiss()
            if action=='exit':self.owner.close()
            elif action=='free':self.owner.toggle_free_play()
            else:self.owner.command_pose(action)
        else:self.dismiss()

    def keyPressEvent(self,e):
        if e.key()==Qt.Key.Key_Escape:self.dismiss()
        else:super().keyPressEvent(e)

    def hideEvent(self,e):self.timer.stop();self.owner.menu_closed();super().hideEvent(e)

    def label_for(self,key,default):
        return self.owner.free_play_label() if key=='free' else default

    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        elapsed=time.perf_counter()-self.opened
        for i,(key,label) in enumerate(self.ITEMS):
            if i>=len(self.centers):continue
            label=self.label_for(key,label)
            c=self.centers[i];scale=1+.095*self.amount[i]+.009*math.sin(elapsed*2+i)
            p.save();p.setOpacity(self.visibility);p.translate(c)
            scale*=.83*(.8+.2*self.visibility)
            p.scale(scale,scale)
            p.setPen(Qt.PenStyle.NoPen)
            for radius,alpha in [(28,10),(26,14)]:
                p.setBrush(QColor(10,24,45,alpha));p.drawEllipse(QRectF(-radius,-radius+3,radius*2,radius*2))
            gradient=QLinearGradient(-18,-25,20,27)
            gradient.setColorAt(0,QColor(255,255,255,175))
            gradient.setColorAt(.30,QColor(248,251,255,95))
            gradient.setColorAt(.58,QColor(232,241,250,58))
            gradient.setColorAt(1,QColor(247,252,255,146))
            p.setBrush(QBrush(gradient));p.setPen(QPen(QColor(255,255,255,225),.85));p.drawEllipse(QRectF(-24,-24,48,48))
            # Thin inner refraction rim and a moving specular lens highlight.
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(183,216,250,110),.8));p.drawEllipse(QRectF(-22.4,-22.4,44.8,44.8))
            cursor=self.mapFromGlobal(QCursor.pos());hx=max(-8,min(8,(cursor.x()-c.x())*.08));hy=max(-5,min(5,(cursor.y()-c.y())*.05))
            gloss=QRadialGradient(QPointF(-8+hx,-17+hy),22)
            gloss.setColorAt(0,QColor(255,255,255,165));gloss.setColorAt(.45,QColor(255,255,255,48));gloss.setColorAt(1,QColor(255,255,255,0))
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(gloss);p.drawEllipse(QRectF(-22,-22,44,44))
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(255,255,255,235),1.2));p.drawArc(QRectF(-22,-22,44,44),42*16,86*16)
            p.setPen(QPen(QColor(224,243,255,155),.9));p.drawArc(QRectF(-22,-22,44,44),225*16,72*16)
            if key=='free' and getattr(self.owner,'free_play',False):
                p.setPen(QPen(QColor(78,176,160,215),1.4));p.drawEllipse(QRectF(-22.4,-22.4,44.8,44.8))
            ink=QColor('#993f4f') if key=='exit' else QColor('#172f4a')
            p.save();p.translate(0,4);p.scale(.74,.74)
            p.setPen(QPen(ink,1.9,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));self.icon(p,key);p.restore()
            p.setPen(ink);p.setFont(QFont('Microsoft YaHei UI',6 if len(label)>4 else 7,QFont.Weight.Medium))
            p.drawText(QRectF(-24,5,48,16),Qt.AlignmentFlag.AlignCenter,label)
            p.restore()

    def icon(self,p,key):
        if key=='exit':
            p.drawArc(QRectF(-9,-23,18,18),125*16,290*16);p.drawLine(QPointF(0,-26),QPointF(0,-15));return
        if key=='free':
            p.drawArc(QRectF(-10,-25,20,20),20*16,290*16);p.drawLine(QPointF(10,-22),QPointF(10,-15));p.drawLine(QPointF(10,-22),QPointF(3,-22));return
        # Small consistent dog posture pictograms drawn as vectors.
        path=QPainterPath();path.addEllipse(QRectF(-1,-27,10,10))
        if key=='stand_front':
            path.moveTo(0,-17);path.lineTo(-11,-17);path.lineTo(-13,-7);path.moveTo(-9,-17);path.lineTo(-8,-7);path.moveTo(3,-17);path.lineTo(3,-7);path.moveTo(-11,-17);path.lineTo(-16,-22)
        elif key=='sit_front':
            path.moveTo(1,-17);path.lineTo(-7,-7);path.lineTo(8,-7);path.moveTo(5,-17);path.lineTo(6,-7);path.moveTo(-7,-7);path.lineTo(-13,-11)
        else:
            path.moveTo(0,-17);path.lineTo(-10,-12);path.quadTo(-16,-5,-8,-7);path.lineTo(12,-7);path.moveTo(4,-17);path.lineTo(8,-10)
        p.drawPath(path)
