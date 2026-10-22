import argparse
import ctypes
import json
import random
import sys
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import psutil
from shape_match import fit as fit_shape, warp as warp_shape
from companion import install_idle_assets, bouts
from posture_commands import route_to,after_command
from radial_menu import RadialMenu
from interaction_runtime import InteractionMixin,plan_route
from playback_timing import RateRamp
from action_priority import install_action_tags,is_urgent_route
from response_timeline import timeline_for
from frame_cache import load_cached_frames
from app_paths import writable_output
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QImage, QPainter, QBitmap, QRegion
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

ROOT=Path(__file__).resolve().parent

def load_frames(path,mmap_mode=None):
    return load_cached_frames(path,mmap_mode)

def foot_anchor(frame):
    """Robust lower-body anchor: ignore the tail and isolated faint pixels."""
    ys,xs=np.nonzero(frame[:,:,3]>64)
    if not len(ys):return None
    bottom=int(np.quantile(ys,.995))
    top=int(ys.min())
    band=(ys>=bottom-max(4,int((bottom-top)*.18))) & (ys<=bottom)
    return np.array([np.median(xs[band]),bottom],dtype=float)

def place_frame(frame,offset,pad):
    h,w=frame.shape[:2];dx,dy=map(int,offset)
    size=420+2*pad
    out=np.zeros((size,size,4),dtype=np.uint8)
    x=(size-w)//2+dx;y=(size-h)//2+dy
    x0=max(0,-x);y0=max(0,-y);x1=min(w,size-x);y1=min(h,size-y)
    if x1<=x0 or y1<=y0:raise ValueError('Frame outside canvas')
    if np.any(frame[:y0,:,3]) or np.any(frame[y1:,:,3]) or np.any(frame[:,:x0,3]) or np.any(frame[:,x1:,3]):
        raise ValueError('Alignment would crop visible pixels')
    out[y+y0:y+y1,x+x0:x+x1]=frame[y0:y1,x0:x1]
    return out

def validate_edge(manifest,state,key):
    if manifest[key]['start'] != state:
        raise ValueError(f'Illegal transition: {state} -> {key}')

def frame_at(data,clip,index):
    if 'hold_index' in clip:raw=data[clip['hold_index']]
    else:
        if 'pingpong_frames' in clip:
            n=clip['pingpong_frames'];index=min(index,2*n-2-index)
        raw=data[clip.get('frame_start',0)+(clip['frames']-1-index if clip.get('reverse') else index)]
    if 'crop_rect' not in clip:return raw
    x,y,w,h=clip['crop_rect'];size=clip['canvas_size']
    restored=np.zeros((size,size,4),dtype=np.uint8)
    restored[y:y+h,x:x+w]=raw
    return restored

class Pet(InteractionMixin,QWidget):
    def __init__(self,args):
        super().__init__()
        self.args=args
        self.config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        self.assets=self.config['manifest'];self.state='stand_front'
        install_idle_assets(self.assets)
        install_action_tags(self.assets)
        self.render_size=self.config.get('render_size',self.config['size'])
        self.pad=192;self.max_registration_offset=96;self.canvas_size=self.render_size+2*self.pad
        self.offset=np.zeros(2,dtype=int)
        self.executor=ThreadPoolExecutor(max_workers=2)
        self.futures={};self.data=None;self.current=None;self.previous=None
        self.key='';self.index=-1;self.events=[];self.samples=[];self.skipped=0;self.paints=0
        self.started=time.perf_counter();self.process=psutil.Process();self.process.cpu_percent()
        self.rate_ramp=RateRamp(duration=self.config.get('response_policy',{}).get('ramp_seconds',.22));self.playback_rate=1.;self.rate_tick=self.started
        self.random_mode=not args.demo;self.quitting=False;self.drag=None
        self.stand_bag=[]
        self.command_target=None;self.mask_frame=None;self.menu_restore_through=False
        self.exit_requested=False;self.exit_route_started=False;self.fading=False;self.allow_close=False
        self.exit_bridging=False;self.bridge_start=None
        self.queue=list(self.config['demo']) if args.demo else ['stand_quiet']
        if self.config.get('startup'):
            self.state='offscreen';self.queue=list(self.config['startup'])+self.queue
        self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.stats_timer=QTimer(self);self.stats_timer.timeout.connect(self.sample);self.stats_timer.start(1000)
        self.setWindowTitle('DogPet Alpha MVP')
        self.setWindowIcon(QIcon(str(ROOT/'runtime/app.ico')))
        self.setFixedSize(round(self.config['size']*self.canvas_size/self.render_size),round(self.config['size']*self.canvas_size/self.render_size))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.radial=RadialMenu(self)
        self.init_interactions()
        icon=ROOT/'runtime/app.ico'
        self.tray=QSystemTrayIcon(QIcon(str(icon if icon.exists() else ROOT/'runtime/stand_idle_first.png')),self)
        menu=QMenu();self.through_action=QAction('点击穿透',menu,checkable=True)
        self.through_action.triggered.connect(self.set_through);menu.addAction(self.through_action)
        self.keyboard_action=QAction('键盘联动',menu,checkable=True)
        self.keyboard_action.setChecked(self.keyboard_enabled)
        self.keyboard_action.triggered.connect(self.set_keyboard_enabled);menu.addAction(self.keyboard_action)
        self.free_play_action=QAction('自由活动',menu,checkable=True)
        self.free_play_action.triggered.connect(self.set_free_play);menu.addAction(self.free_play_action)
        companion=QAction('自然陪伴模式',menu);companion.triggered.connect(lambda:self.switch_mode(False));menu.addAction(companion)
        demo=QAction('全动作演示',menu);demo.triggered.connect(self.restart);menu.addAction(demo)
        quit_action=QAction('退出',menu);quit_action.triggered.connect(self.close);menu.addAction(quit_action)
        self.tray.setContextMenu(menu);self.tray.setToolTip('小狗桌宠 · Alpha 素材版');self.tray.show()
        self.tray.activated.connect(lambda reason:self.open_controls() if reason==QSystemTrayIcon.ActivationReason.Trigger else None)
        screen=QApplication.primaryScreen().availableGeometry()
        margin=round(self.pad*self.config['size']/self.render_size)
        self.move(screen.right()-self.width()-24+margin,screen.bottom()-self.height()-12+margin)
        # Load initial + next clip before showing. Later boundary changes never block on Future.result().
        self.request(self.queue[0]);self.show();self.set_through(getattr(args,'through',False))
        self.begin_next();self.timer.start(8)
        if args.check_window:
            QTimer.singleShot(1200,self.check_window)
            QTimer.singleShot(14000,self.snapshot)
        if args.duration:QTimer.singleShot(int(args.duration*1000),self.force_close)
        if args.exit_after:QTimer.singleShot(int(args.exit_after*1000),self.close)

    def check_window(self):
        original=self.through
        self.set_through(False);off=getattr(self,'ex_style',0)
        self.set_through(True);on=getattr(self,'ex_style',0)
        self.set_through(original)
        self.log('window_check',off_style=off,on_style=on,
                 pass_through_flag_ok=not bool(off&0x20) and bool(on&0x20),
                 topmost_flag=bool(on&0x8),layered_flag=bool(on&0x80000),
                 device_pixel_ratio=self.devicePixelRatioF(),render_size=self.render_size)
        self.snapshot()

    def snapshot(self):
        self.screen().grabWindow(0,self.x(),self.y(),self.width(),self.height()).save(str(writable_output('desktop_pet.png')))

    def log(self,event,**values):
        row=dict(t=round(time.perf_counter()-self.started,4),event=event,**deepcopy(values))
        self.events.append(row);print(json.dumps(row),flush=True)

    def request(self,key):
        path=self.assets[key]['file']
        if getattr(self,'urgency_context',None):
            current_path=self.assets.get(self.key,{}).get('file')
            for old,future in list(self.futures.items()):
                if old not in (path,current_path) and future.cancel():self.futures.pop(old,None)
        if path not in self.futures:
            expected=self.assets[key].get('canvas_size',self.render_size)
            storage=tuple(self.assets[key].get('storage_shape',(expected,expected,4)))
            def load():
                a=load_frames(ROOT/path)
                if a.dtype!=np.uint8 or a.shape[1:]!=storage:
                    raise ValueError(f'Invalid runtime frame array: {path}')
                return a
            self.futures[path]=self.executor.submit(load)

    def choose(self):
        if getattr(self,'free_play',False) and self.state!='stand_front':
            if self.state in ('sit_front','lie_front'):
                self.queue.extend(after_command(self.state))
            else:
                idle={'lie_paws':'paws_idle_rest','stand_side_screen_left':'side_idle_rest',
                      'sit_look_screen_left':'sit_left_idle_rest','sit_look_screen_right':'sit_right_idle_rest'}.get(self.state)
                if idle:self.queue.extend([idle]*random.randint(1,2))
                self.queue.extend(route_to(self.assets,self.state,'stand_front')+['stand_idle_rest'])
            return
        if self.state=='stand_front':
            if not self.stand_bag:
                self.stand_bag=bouts()
            self.queue.extend(self.stand_bag.pop()+['stand_idle_rest'])
        elif self.state=='lie_paws':self.queue.append('paws_idle')
        elif self.state in ('sit_front','lie_front'):self.queue.append({'sit_front':'sit_quiet','lie_front':'lie_quiet'}[self.state])
        else:self.queue.extend(route_to(self.assets,self.state,'stand_front')+['stand_idle_rest'])

    def begin_next(self):
        if not self.exit_requested:self.interaction_boundary()
        if self.exit_requested:
            if not self.exit_route_started:
                self.queue=list(self.config.get('exit_routes',{}).get(self.state,[]))
                self.exit_route_started=True
                if not self.queue:
                    self.log('error',message='Missing stand-front departure route')
                    self.force_close();return
            if not self.queue:
                self.start_fade(.25);return
        if not self.queue:self.choose()
        self.pending=self.queue.pop(0);validate_edge(self.assets,self.state,self.pending)
        self.wait_draw_index=None
        self.request(self.pending);self.wait_started=time.perf_counter();self.waiting=True

    def draw_while_loading(self):
        # An interaction may request an archive that could not be prefetched.
        # Continue the outgoing stable animation while the worker decodes it.
        if self.current is None or self.data is None or not self.key:return
        clip=self.assets[self.key]
        if clip['start']!=clip['end'] or self.key.startswith('entry'):return
        index=int((time.perf_counter()-self.clip_start)*clip['fps']*self.playback_rate)%clip['frames']
        if index==self.wait_draw_index:return
        self.wait_draw_index=index
        self.current=place_frame(frame_at(self.data,clip,index),self.offset,self.pad);self.update()

    def activate(self):
        path=self.assets[self.pending]['file'];future=self.futures[path]
        if not future.done():return False
        try:newdata=future.result()
        except Exception as exc:
            self.log('error',message=str(exc));self.force_close();return False
        self.previous=self.current.copy() if self.current is not None else np.zeros((self.canvas_size,self.canvas_size,4),dtype=np.uint8)
        # Entry/portal pairs share a scene transform: retain their authored motion.
        group=self.assets[self.pending].get('continuous_group')
        shared_pair=(self.key,self.pending) in {('entry_effect','entry_approach'),('turn_exit','exit_effect')} or bool(group and self.assets.get(self.key,{}).get('continuous_group')==group)
        same_rest=self.key==self.pending and 'pingpong_frames' in self.assets[self.pending]
        first=frame_at(newdata,self.assets[self.pending],0)
        before=foot_anchor(self.current) if self.current is not None else None
        incoming=foot_anchor(first)
        if incoming is not None:incoming-=(first.shape[0]-self.render_size)/2
        if before is not None and incoming is not None and not shared_pair:
            wanted=np.rint(before-incoming-self.pad).astype(int)
            if np.max(np.abs(wanted))>self.max_registration_offset:
                # Repeated user interactions need not wait for a full idle unit
                # to absorb drift. Recenter the invisible window, preserving
                # the dog's desktop position and any active mouse grab.
                safe=np.clip(wanted,-self.max_registration_offset,self.max_registration_offset)
                shift=wanted-safe
                self.current=place_frame(self.current,-shift,self.pad)
                self.previous=place_frame(self.previous,-shift,self.pad)
                move=QPoint(round(shift[0]*self.width()/self.canvas_size),round(shift[1]*self.height()/self.canvas_size))
                self.move(self.pos()+move)
                if self.drag is not None:self.drag-=move
                before=foot_anchor(self.current);wanted=safe
                self.log('registration_recenter',render_shift=shift.tolist(),window_shift=[move.x(),move.y()])
            self.offset=wanted
            residual=incoming+self.pad+self.offset-before
            self.log('alignment',from_clip=self.key,to_clip=self.pending,offset=self.offset.tolist(),residual_px=residual.tolist())
        self.blend_seconds={'turn_exit':.9,'exit_effect':.65,'entry_effect':.35,'entry_approach':.45}.get(self.pending,.28)
        if self.pending=='entry_effect':self.blend_seconds=0
        if same_rest:self.blend_seconds=0
        if self.key=='entry_approach':self.blend_seconds=.65
        if group and shared_pair:
            self.blend_seconds=.12 if self.key==self.pending else .10
            old=self.assets.get(self.key,{})
            if not old.get('reverse') and old.get('frame_start',0)+old.get('frames',0)==self.assets[self.pending].get('frame_start',0):self.blend_seconds=0
        self.shape_scale=np.ones(2);self.shape_pivot=None
        if before is not None and incoming is not None and not shared_pair and not same_rest and not self.assets[self.pending].get('fixed_body_scale') and self.pending not in ('entry_effect','exit_effect','turn_exit'):
            aligned=place_frame(first,self.offset,self.pad)
            self.shape_pivot=incoming+self.offset+self.pad
            self.shape_scale,metrics=fit_shape(self.previous,aligned,self.shape_pivot)
            self.log('shape_match',from_clip=self.key,to_clip=self.pending,**metrics)
        self.data=newdata;self.key=self.pending;self.index=-1;self.clip_start=time.perf_counter();self.waiting=False
        InteractionMixin.update_urgency(self)
        urgent=is_urgent_route(self.key,self.assets[self.key],self.urgency_context)
        self.timeline=timeline_for(self.key,self.assets[self.key],urgent)
        self.timeline_urgent=urgent
        if urgent:self.blend_seconds=min(self.blend_seconds,.16)
        # Carry speed through a multi-clip route; the loop then eases back to 1x.
        self.activated_at=self.rate_tick=self.clip_start
        self.playback_rate=self.rate_ramp.value(self.clip_start)
        self.clip_offset_origin=self.offset.copy()
        self.log('start',clip=self.key,state=self.state,load_wait_ms=round((self.clip_start-self.wait_started)*1000,2))
        # Decide and warm the next clip while the current one is playing.
        if not self.exit_requested and not self.queue and self.random_mode and self.interaction is None:
            if getattr(self,'free_play',False) and self.assets[self.key]['start']==self.assets[self.key]['end'] and self.state!='stand_front':self.choose()
            elif self.assets[self.key]['end']=='lie_paws':self.queue.append('paws_idle')
            elif self.assets[self.key]['end']=='stand_front':
                if self.assets[self.key]['start']=='stand_front':self.choose()
                else:self.queue.append('stand_idle_rest')
        keep={path}
        if self.queue:
            self.request(self.queue[0]);keep.add(self.assets[self.queue[0]]['file'])
        self.futures={k:v for k,v in self.futures.items() if k in keep}
        return True

    def tick(self):
        if self.quitting:return
        if self.exit_bridging:
            future=self.futures[self.assets['stand_quiet']['file']]
            if not future.done():return
            try:source=future.result()[-1]
            except Exception as exc:
                self.log('error',message=str(exc));self.force_close();return
            if self.bridge_start is None:
                before=foot_anchor(self.bridge_source);after=foot_anchor(source)
                self.offset=np.rint(before-after-self.pad).astype(int) if before is not None and after is not None else np.zeros(2,dtype=int)
                self.bridge_target=place_frame(source,self.offset,self.pad)
                self.bridge_start=time.perf_counter()
                self.log('exit_bridge_start',from_state=self.state,to_state='stand_front',duration=.9)
            p=min(1,(time.perf_counter()-self.bridge_start)/.9)
            p=p*p*(3-2*p)
            self.current=(self.bridge_source.astype(np.float32)*(1-p)+self.bridge_target.astype(np.float32)*p).astype(np.uint8)
            self.update()
            if p>=1:
                self.exit_bridging=False;self.state='stand_front'
                self.log('exit_bridge_complete',state=self.state)
                self.begin_next()
            return
        if self.fading:
            p=min(1,(time.perf_counter()-self.fade_start)/self.fade_duration)
            self.current=(self.fade_source.astype(np.float32)*(1-p)).astype(np.uint8)
            self.update()
            if p>=1:
                self.log('exit_animation_complete');self.force_close()
            return
        if self.waiting and not self.activate():
            if not self.quitting:self.draw_while_loading()
            return
        clip=self.assets[self.key];now=time.perf_counter()
        desired=self.interaction_rate()
        context=getattr(self,'urgency_context',None)
        if (self.exit_requested or self.command_target is not None) and not context:desired=1.25
        if self.key.startswith('entry') or self.key=='exit_effect':desired=1.
        # Integrate the ramp rather than multiplying all elapsed time by the
        # new speed. Retargeting cannot jump the current source-frame position.
        media_time=(now-self.clip_start)*self.playback_rate
        media_time+=self.rate_ramp.integral(self.rate_tick,now)-(now-self.rate_tick)*self.playback_rate
        urgent=is_urgent_route(self.key,clip,context)
        old_timeline=getattr(self,'timeline',None)
        if old_timeline is None or getattr(self,'timeline_urgent',False)!=urgent:
            source=old_timeline.source_position(media_time) if old_timeline else media_time*clip['fps']
            self.timeline=timeline_for(self.key,clip,urgent);self.timeline_urgent=urgent
            media_time=self.timeline.media_seconds(source)
        if self.rate_ramp.retarget(desired,now):
            self.log('speed_target',clip=self.key,rate=desired,from_rate=round(self.playback_rate,4),ramp_seconds=self.rate_ramp.duration)
        self.playback_rate=self.rate_ramp.value(now);self.rate_tick=now
        self.clip_start=now-media_time/self.playback_rate
        elapsed=now-self.clip_start
        blend_elapsed=now-self.activated_at
        if self.dragging and clip.get('interaction_role')=='loop' and self.key.startswith('walk_') and now-self.drag_last_move>.18:
            media_time=self.timeline.media_seconds(max(0,self.index));elapsed=media_time/self.playback_rate;self.clip_start=now-elapsed
        target=int(self.timeline.source_position(elapsed*self.playback_rate)+1e-8)
        if target>=clip['frames']:
            if self.key in ('stand_idle','stand_idle_rest'):self.offset=np.zeros(2,dtype=int)
            self.current=place_frame(frame_at(self.data,clip,clip['frames']-1),self.offset,self.pad)
            self.state=clip['end'];self.log('complete',clip=self.key,state=self.state)
            if self.command_target==self.state:
                self.log('command_reached',state=self.state);self.command_target=None
            InteractionMixin.update_urgency(self)
            self.begin_next()
            # Consume an already-prefetched next clip in this same timer callback.
            if not self.fading and self.waiting and self.activate():self.tick()
            return
        if target==self.index:return
        self.skipped+=max(0,target-self.index-1);self.index=target
        # Absorb tiny registration drift gradually during idle, never at the cut.
        if self.key in ('stand_idle','stand_idle_rest'):
            p=target/max(1,clip['frames']-1);p=p*p*(3-2*p)
            self.offset=np.rint(self.clip_offset_origin*(1-p)).astype(int)
        raw=place_frame(frame_at(self.data,clip,target),self.offset,self.pad)
        if self.shape_pivot is not None and blend_elapsed<.8 and np.any(self.shape_scale!=1):
            p=min(1,blend_elapsed/.8);p=p*p*(3-2*p)
            raw=warp_shape(raw,self.shape_pivot,1+(self.shape_scale-1)*(1-p))
        if self.previous is not None and blend_elapsed<self.blend_seconds:
            # Mix premultiplied RGBA once, avoiding a double-opacity dip.
            p=min(1,blend_elapsed/self.blend_seconds)
            p=p*p*(3-2*p)
            self.current=(self.previous.astype(np.float32)*(1-p)+raw.astype(np.float32)*p).astype(np.uint8)
        else:self.current=raw
        self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(),Qt.GlobalColor.transparent)
        if self.current is not None:
            a=self.current
            im=QImage(a.data,a.shape[1],a.shape[0],a.strides[0],QImage.Format.Format_RGBA8888_Premultiplied)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
            p.drawImage(self.rect(),im);self.paints+=1
            if not self.through and self.mask_frame is not self.current:
                bitmap=QBitmap.fromImage(im.scaled(self.size()).createAlphaMask(Qt.ImageConversionFlag.ThresholdDither))
                region=QRegion(bitmap)
                self.setMask(region if not region.isEmpty() else QRegion(0,0,1,1));self.mask_frame=self.current

    def set_through(self,on):
        self.through=bool(on);self.through_action.setChecked(self.through)
        if sys.platform=='win32':
            u=ctypes.windll.user32
            u.GetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int];u.GetWindowLongW.restype=ctypes.c_long
            u.SetWindowLongW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_long];u.SetWindowLongW.restype=ctypes.c_long
            hwnd=int(self.winId());style=u.GetWindowLongW(hwnd,-20)
            style=style|0x20 if on else style&~0x20
            u.SetWindowLongW(hwnd,-20,style|0x80000|0x80)
            self.ex_style=u.GetWindowLongW(hwnd,-20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,self.through)
        self.mask_frame=None
        if self.through:self.clearMask()
        self.update()

    def open_controls(self):
        if self.quitting or self.exit_requested:return
        if self.radial.isVisible():self.radial.dismiss();return
        self.menu_restore_through=self.through
        if self.through:self.set_through(False)
        # Fixed authored standing-body center, independent of pose, tail and frame.
        center=QPoint(round(self.width()*.5),round(self.height()*.53))
        self.radial.popup_at(self.mapToGlobal(center))

    def menu_closed(self):
        if self.dragging:return
        self.drag=None
        if self.menu_restore_through:
            self.menu_restore_through=False;self.set_through(True)

    def contextMenuEvent(self,event):
        self.open_controls();event.accept()

    def command_pose(self,target):
        if self.exit_requested or self.quitting:return
        if getattr(self,'free_play',False):self.set_free_play(False)
        if hasattr(self,'keyboard_activity'):self.cancel_interactions()
        InteractionMixin.set_urgency(self,'manual_pose',target_state=target)
        route,source,immediate=plan_route(self,target)
        self.queue=route+after_command(target)
        self.random_mode=True;self.stand_bag=[];self.command_target=target
        self.command_immediate=immediate
        self.log('posture_command',target=target,source=source,immediate=immediate,route=route)
        if self.queue:self.request(self.queue[0])
        if immediate:
            # Blend from the currently displayed frame, not the old clip's final frame.
            if source==target:
                self.log('command_reached',state=target);self.command_target=None;self.urgency_context=None
            self.begin_next()

    def restart(self):
        self.switch_mode(True)

    def switch_mode(self,demo):
        if self.exit_requested:return
        if getattr(self,'free_play',False):self.set_free_play(False)
        self.cancel_interactions()
        end=self.assets[self.pending if self.waiting else self.key]['end']
        route=list(self.config['exit_routes'][end][:-2])
        if end=='stand_threequarter':route=['turn_exit_return']
        self.command_target=None
        self.random_mode=not demo;self.stand_bag=[]
        self.queue=route+(list(self.config['demo']) if demo else ['stand_idle_rest'])
        if self.queue:self.request(self.queue[0])
        self.log('mode_queued',mode='demo' if demo else 'companion',after_state=end)

    def request_exit(self):
        if self.exit_requested:return
        self.cancel_interactions()
        self.dragging=False;self.drag=None;self.drag_press=None;self.drag_last=None;self.walk_direction=None
        self.exit_requested=True;self.random_mode=False;self.queue=[]
        self.command_target=None;self.radial.dismiss()
        self.log('exit_requested',state=self.state,clip=self.key)
        self.tray.setToolTip('小狗正在离场，动画结束后退出')
        InteractionMixin.set_urgency(self,'exit',target_key='exit_effect')
        route,source,immediate=plan_route(self,'stand_front')
        self.queue=route+['turn_exit','exit_effect']
        self.exit_route_started=True
        if self.queue:self.request(self.queue[0])
        self.log('exit_route_queued',after_state=source,route=self.queue,immediate=immediate)
        if immediate:self.begin_next()

    def start_fade(self,duration):
        self.fading=True;self.fade_start=time.perf_counter();self.fade_duration=duration
        self.fade_source=self.current.copy() if self.current is not None else np.zeros((self.canvas_size,self.canvas_size,4),dtype=np.uint8)
        self.log('exit_fade_start',state=self.state,duration=duration)

    def force_close(self):
        self.allow_close=True;self.close()

    def mousePressEvent(self,e):
        self.interaction_mouse_press(e)
    def mouseMoveEvent(self,e):
        self.interaction_mouse_move(e)
    def mouseReleaseEvent(self,e):self.interaction_mouse_release(e)
    def mouseDoubleClickEvent(self,e):self.interaction_mouse_double_click(e)

    def sample(self):
        self.samples.append(dict(t=round(time.perf_counter()-self.started,3),cpu=self.process.cpu_percent(),rss_mb=round(self.process.memory_info().rss/2**20,2),paints=self.paints,skipped=self.skipped,clip=self.key))

    def closeEvent(self,event):
        if not self.allow_close:
            event.ignore();self.request_exit();return
        if not self.quitting:
            self.log('shutdown')
            self.quitting=True;self.timer.stop();self.stats_timer.stop();self.tray.hide()
            self.input_timer.stop();self.keyboard_activity.reset()
            self.radial.hide()
            self.executor.shutdown(wait=False,cancel_futures=True)
            if self.args.report:
                self.args.report.parent.mkdir(parents=True,exist_ok=True)
                self.args.report.write_text(json.dumps(dict(events=self.events,samples=self.samples,ex_style=getattr(self,'ex_style',None),skipped=self.skipped,paints=self.paints),indent=2),encoding='utf-8')
        event.accept();QApplication.instance().quit()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--demo',action='store_true');parser.add_argument('--duration',type=float,default=0)
    parser.add_argument('--interactive',action='store_true');parser.add_argument('--report',type=Path)
    parser.add_argument('--check-window',action='store_true')
    parser.add_argument('--through',action='store_true',help='Start in full click-through mode; tray click opens controls')
    parser.add_argument('--seed',type=int)
    parser.add_argument('--exit-after',type=float,default=0,help='Trigger normal animated exit after N seconds (test aid)')
    parser.add_argument('--self-test-report',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.seed is not None:random.seed(args.seed)
    app=QApplication(sys.argv);app.setApplicationName('诞诞桌宠');app.setApplicationVersion('0.3.2')
    app.setWindowIcon(QIcon(str(ROOT/'runtime/app.ico')))
    window=Pet(args)
    if args.self_test_report:
        from portable_self_test import run_checks
        return run_checks(app,window,args.self_test_report)
    return app.exec()

if __name__=='__main__':raise SystemExit(main())
