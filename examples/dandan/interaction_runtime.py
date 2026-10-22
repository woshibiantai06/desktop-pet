"""Posture-safe owner interactions for the standalone Qt desktop dog."""
import time
import numpy as np
from PySide6.QtCore import QPoint,QTimer,Qt
from PySide6.QtWidgets import QApplication
from input_activity import KeyboardActivity
from posture_commands import route_to,after_command
from action_priority import SPECIAL_ACTIONS,is_urgent_route
from response_timeline import timeline_for


def plan_route(pet,target):
    """Start from a stable frame, finish a transition, or retrace its played prefix."""
    active_key=pet.pending if pet.waiting else pet.key;active=pet.assets[active_key]
    cost=lambda k,a:timeline_for(k,a,True).duration
    if pet.waiting:
        if active.get('command_prefix'):
            return [active_key]+route_to(pet.assets,active['end'],target,cost),pet.state,True
        if not active_key.startswith('entry'):
            # A decoded/prefetched clip has not changed the displayed posture.
            return route_to(pet.assets,pet.state,target,cost),pet.state,True
    stable=active['start']==active['end']==pet.state and not active_key.startswith('entry')
    source=pet.state if stable else active['end'];route=route_to(pet.assets,source,target,cost)
    interrupt=False
    if not pet.waiting and not stable and pet.index>=0 and not active.get('command_prefix') and pet.key not in ('entry_effect','entry_approach','exit_effect','turn_exit'):
        try:back=route_to(pet.assets,active['start'],target,cost)
        except ValueError:back=None
        if back is not None:
            timeline=timeline_for(active_key,active,True)
            route_cost=lambda keys:sum(cost(k,pet.assets[k]) for k in keys)
            forward=timeline.duration-timeline.media_seconds(pet.index)+route_cost(route)
            backward=timeline.media_seconds(pet.index)+route_cost(back)
            if backward<forward:
                key='command_return_prefix';state=f'partial:{pet.key}'
                start=active.get('frame_start',0)+(active['frames']-1-pet.index if active.get('reverse') else 0)
                pet.assets[key]={**active,'start':state,'end':active['start'],'frame_start':start,
                    'frames':pet.index+1,'reverse':not active.get('reverse',False),'command_prefix':True,
                    'timing_source_key':active_key,'timing_indices':list(range(pet.index,-1,-1))}
                pet.state=state;source=state;route=[key]+back;interrupt=True
    return route,source,stable or interrupt


class InteractionMixin:
    def set_urgency(self,kind,target_key=None,target_state=None):
        self.urgency_context=dict(kind=kind,target_key=target_key,target_state=target_state,requested_at=time.perf_counter())
        self.log('priority_request',kind=kind,label=SPECIAL_ACTIONS[kind]['label'],priority=SPECIAL_ACTIONS[kind]['priority'],target_key=target_key,target_state=target_state)

    def update_urgency(self):
        info=getattr(self,'urgency_context',None)
        if not info:return
        reached=(info.get('target_key')==self.key) if info.get('target_key') else (info.get('target_state')==self.state)
        if reached:
            self.log('priority_reached',kind=info['kind'],clip=self.key,latency_ms=round((time.perf_counter()-info['requested_at'])*1000,2))
            self.urgency_context=None

    def init_interactions(self):
        self.urgency_context=None
        self.free_play=False
        self.interaction=None;self.dragging=False;self.drag_press=None;self.drag_last=None
        self.drag_last_move=0.;self.walk_direction=None;self.keyboard_wanted=False
        self.input_cooldown=0.;self.keyboard_activity=KeyboardActivity()
        self.interactions_ready=all(k in self.assets for k in ('typing_enter','pet_sit','pet_paws','walk_left_enter','walk_right_enter'))
        self.keyboard_enabled=self.interactions_ready and not self.args.demo
        self.input_timer=QTimer(self);self.input_timer.timeout.connect(self.poll_interactions);self.input_timer.start(50)

    def free_play_label(self):
        return '取消自由活动' if getattr(self,'free_play',False) else '自由活动'

    def sync_free_play_ui(self):
        if hasattr(self,'free_play_action'):
            self.free_play_action.setText(self.free_play_label())
            self.free_play_action.setChecked(getattr(self,'free_play',False))
        if hasattr(self,'radial') and hasattr(self.radial,'update'):self.radial.update()

    def toggle_free_play(self):
        self.set_free_play(not getattr(self,'free_play',False))

    def set_free_play(self,on):
        """Mute owner specials without disabling explicit control or changing assets."""
        if self.exit_requested or self.quitting:return False
        on=bool(on)
        if on==getattr(self,'free_play',False):
            self.sync_free_play_ui();return True
        self.free_play=on
        self.keyboard_activity.reset();self.keyboard_wanted=False
        self.input_cooldown=time.perf_counter()+.35
        self.sync_free_play_ui()
        self.log('free_play_changed',enabled=on)
        if not on:return True
        self.random_mode=True;self.command_target=None;self.stand_bag=[]
        if self.interaction and self.interaction['kind']!='pet':
            # Retain the legal keyboard/walk exit, including cancellation at a
            # reached pose. Clearing only the flag would strand a stream state.
            self.stop_stream()
            return True
        self.interaction=None;self.urgency_context=None
        active=self.pending if self.waiting else self.key
        if active.startswith('entry'):
            # Both halves of the portal/approach remain intact even if selected
            # while the first frame is still loading.
            self.queue=[key for key in self.queue if key.startswith('entry')]+['stand_idle_rest']
        else:
            self.queue=[]
            if self.waiting and not self.assets[active].get('command_prefix'):
                # Pending frames have not been shown; discard old manual/demo
                # requests from the actual displayed state, not pending['end'].
                self.stop_at_pose();self.begin_next()
        return True

    def cancel_interactions(self,cooldown=4.):
        self.interaction=None;self.keyboard_wanted=False
        self.urgency_context=None
        self.input_cooldown=time.perf_counter()+cooldown
        self.keyboard_activity.reset()

    def set_keyboard_enabled(self,on):
        self.keyboard_enabled=bool(on);self.keyboard_activity.reset();self.keyboard_wanted=False
        if self.interaction and self.interaction['kind']=='typing':
            self.stop_stream()
        if hasattr(self,'keyboard_action'):self.keyboard_action.setChecked(self.keyboard_enabled)

    def poll_interactions(self):
        if self.quitting or self.exit_requested or not self.interactions_ready:return
        now=time.perf_counter()
        if getattr(self,'free_play',False) or not self.keyboard_enabled or not self.random_mode or self.dragging or self.command_target is not None or now<self.input_cooldown:
            self.keyboard_activity.reset();self.keyboard_wanted=False
            return
        detector=getattr(self.keyboard_activity,'detector',None)
        approaching=bool(self.interaction and self.interaction['kind']=='typing' and self.interaction['phase']=='approach')
        if detector is not None:
            policy=self.config.get('response_policy',{})
            detector.idle_timeout=policy.get('approach_idle_timeout',.55) if approaching else policy.get('active_idle_timeout',2.)
            detector.cooldown=.2 if approaching else 1.
        self.keyboard_wanted=self.keyboard_activity.poll(now)
        if self.interaction and self.interaction['kind']=='typing':
            self.interaction['wanted']=self.keyboard_wanted
            if self.keyboard_wanted and self.interaction.get('cancel_approach'):self.start_stream('typing')
            if self.keyboard_activity.stopped:self.stop_stream()
        elif self.keyboard_wanted and self.interaction is None and not self.radial.isVisible() and not self.key.startswith('entry'):
            self.start_stream('typing')

    def _apply_plan(self,pose,tail,interaction):
        target_key=interaction.get('clip') or interaction['kind']+'_loop'
        self.set_urgency(interaction['kind'],target_key=target_key)
        route,source,immediate=plan_route(self,pose)
        self.interaction=interaction;self.command_target=None;self.stand_bag=[]
        self.queue=route+tail;self.random_mode=True
        self.log('interaction_requested',kind=interaction['kind'],source=source,route=list(self.queue))
        if self.queue:self.request(self.queue[0])
        if immediate:self.begin_next()

    def start_stream(self,kind):
        if getattr(self,'free_play',False):return False
        if not self.interactions_ready or self.exit_requested or self.quitting:return False
        if kind not in ('typing','walk_left','walk_right'):return False
        if self.key.startswith('entry'):return False
        if self.interaction and self.interaction['kind']==kind:
            self.interaction['wanted']=True
            if self.interaction.get('cancel_approach'):
                self._apply_plan('stand_front',[kind+'_enter'],dict(kind=kind,wanted=True,phase='approach'))
            return True
        if kind.startswith('walk'):
            self.keyboard_activity.reset();self.keyboard_wanted=False
        if self.waiting and self.state=='stand_front' and self.assets[self.pending].get('interaction_role')=='enter' and not self.assets[self.pending].get('command_prefix'):
            self.interaction=dict(kind=kind,wanted=True,phase='approach')
            self.set_urgency(kind,target_key=kind+'_loop')
            self.command_target=None;self.queue=[kind+'_enter'];self.random_mode=True;self.stand_bag=[]
            self.log('interaction_requested',kind=kind,source=self.state,route=list(self.queue))
            self.begin_next();return True
        self._apply_plan('stand_front',[kind+'_enter'],dict(kind=kind,wanted=True,phase='approach'))
        return True

    def stop_at_pose(self):
        """Discard an unneeded approach and rest at its reached, legal posture."""
        self.interaction=None;self.urgency_context=None
        idle={'stand_front':'stand_idle_rest','sit_front':'sit_rest','lie_front':'lie_quiet',
              'lie_paws':'paws_idle_rest','stand_side_screen_left':'side_idle_rest',
              'sit_look_screen_left':'sit_left_idle_rest','sit_look_screen_right':'sit_right_idle_rest'}.get(self.state)
        if idle is None:
            a=self.assets[self.key]
            physical=a.get('hold_index',a.get('frame_start',0)+(0 if a.get('reverse') else a['frames']-1))
            idle='owner_pose_rest'
            self.assets[idle]={**a,'start':self.state,'end':self.state,'hold_index':physical,'frames':90,
                               'interaction_role':'rest','routing_transition':False,'command_prefix':False}
        self.queue=[idle]
        self.log('approach_cancelled_at_pose',state=self.state,idle=idle)

    def stop_stream(self):
        if not self.interaction or self.interaction['kind']=='pet':return
        self.interaction['wanted']=False
        role=self.assets[self.pending if self.waiting else self.key].get('interaction_role')
        if self.interaction['phase']=='approach' and role not in ('enter','loop','exit'):
            # Do not force a standing/type response after its input has ended.
            if self.waiting and not self.assets[self.pending].get('command_prefix'):
                self.stop_at_pose();self.begin_next()
            else:
                self.interaction['cancel_approach']=True;self.queue=[]
            return
        self.set_urgency(self.interaction['kind'],target_state='stand_front')
        if self.waiting and self.state=='stand_front' and self.assets[self.pending].get('interaction_role')=='enter' and not self.assets[self.pending].get('command_prefix'):
            self.interaction=None;self.urgency_context=None;self.queue=['stand_idle_rest'];self.begin_next();return
        if self.waiting and self.assets[self.pending].get('interaction_role')=='loop':
            self.begin_next();return
        # A short drag can retrace a partially played turn instead of walking on.
        if not self.waiting and self.assets[self.key].get('interaction_role')=='enter':
            route,source,immediate=plan_route(self,'stand_front')
            self.interaction=None;self.queue=route+['stand_idle_rest']
            if self.queue:self.request(self.queue[0])
            if immediate:self.begin_next()

    def interaction_boundary(self):
        """Called after state updates, before the player chooses its next clip."""
        info=self.interaction
        if not info:return False
        kind=info['kind']
        if info.get('cancel_approach'):
            self.stop_at_pose();return True
        if kind=='pet':
            if self.key==info['clip']:
                self.interaction=None;self.input_cooldown=time.perf_counter()+.35
            return False
        state=kind
        if self.state==state:
            repeat=info['wanted']
            info['phase']='loop' if repeat else 'exit'
            self.queue=[kind+('_loop' if repeat else '_exit')]
            if not repeat:self.set_urgency(kind,target_state='stand_front')
            return True
        if info['phase']=='exit' and self.state=='stand_front':
            if info['wanted']:
                info['phase']='approach';self.queue=[kind+'_enter'];return True
            self.interaction=None;self.input_cooldown=time.perf_counter()+1.
            self.queue=['stand_idle_rest'];return True
        if not info['wanted'] and self.key!=kind+'_enter':
            # Typing ended while a long posture route was still in progress.
            self.stop_at_pose();return True
        return False

    def interaction_rate(self):
        if is_urgent_route(self.key,self.assets[self.key],getattr(self,'urgency_context',None)):
            return 2. if self.key=='turn_exit' else self.config.get('response_policy',{}).get('route_motion_rate',3.5)
        return 1.

    def head_hit(self,pos):
        if self.current is None:return False
        ys,xs=np.nonzero(self.current[:,:,3]>64)
        if not len(xs):return False
        x=pos.x()*self.canvas_size/self.width();y=pos.y()*self.canvas_size/self.height()
        ix,iy=int(x),int(y)
        if not (0<=iy<self.canvas_size and 0<=ix<self.canvas_size) or self.current[iy,ix,3]<64:return False
        top,bottom=float(ys.min()),float(np.quantile(ys,.995));h=bottom-top
        if self.state=='lie_paws':
            band=(ys>top+.52*h);cx=float(np.median(xs[band]))
            return abs(x-cx)<.40*h and top+.23*h<y<bottom-.13*h
        upper=ys<top+.32*h;cx=float(np.median(xs[upper]))
        fraction=.52 if self.state=='lie_front' else .39
        return abs(x-cx)<.29*h and top<y<top+fraction*h

    def pet_head(self,pos):
        if getattr(self,'free_play',False):return False
        if not self.interactions_ready or self.exit_requested or self.quitting or self.waiting or self.dragging or self.interaction:return False
        a=self.assets[self.key]
        if a['start']!=a['end'] or self.state not in ('stand_front','sit_front','lie_front','lie_paws','sit_look_screen_left','sit_look_screen_right'):return False
        idle=any(s in self.key for s in ('idle','rest','quiet'))
        if not idle or not self.head_hit(pos):return False
        original=self.state
        pose,clip=('lie_paws','pet_paws') if original.startswith('lie') else ('sit_front','pet_sit')
        if original=='stand_front' and 'pet_stand' in self.assets:
            pose,clip='stand_front','pet_stand'
        # Prefer the dedicated transparent standing export. Older installations
        # without pet_stand can still sit down and then restore standing.
        tail=[clip]+route_to(self.assets,pose,original)
        if original in ('stand_front','sit_front','lie_front'):
            tail+=after_command(original)
        else:
            tail+=['paws_idle_rest'] if original=='lie_paws' else [f'sit_{"left" if original.endswith("left") else "right"}_idle_rest']
            tail+=route_to(self.assets,original,'stand_front')+['stand_idle_rest']
        self.cancel_interactions(cooldown=2.)
        self._apply_plan(pose,tail,dict(kind='pet',clip=clip,phase='pet',wanted=True))
        return True

    def interaction_mouse_press(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or self.exit_requested:return
        self.drag=event.globalPosition().toPoint()-self.pos()
        self.drag_press=event.globalPosition().toPoint();self.drag_last=self.drag_press
        self.dragging=False

    def interaction_mouse_move(self,event):
        if self.drag is None or self.drag_press is None or not (event.buttons()&Qt.MouseButton.LeftButton):return
        point=event.globalPosition().toPoint()
        if not self.dragging:
            if (point-self.drag_press).manhattanLength()<QApplication.startDragDistance():return
            self.dragging=True;self.radial.dismiss();self.cancel_interactions()
        self.move(point-self.drag)
        dx=point.x()-self.drag_last.x()
        if abs(dx)>=2:
            self.drag_last_move=time.perf_counter();direction='walk_left' if dx<0 else 'walk_right'
            if not getattr(self,'free_play',False) and direction!=self.walk_direction:
                self.walk_direction=direction;self.start_stream(direction)
        self.drag_last=point

    def interaction_mouse_release(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        was_dragging=self.dragging
        self.drag=None;self.drag_press=None;self.drag_last=None;self.dragging=False;self.walk_direction=None
        if was_dragging:
            self.stop_stream();self.input_cooldown=time.perf_counter()+.35
        if self.menu_restore_through and not self.radial.isVisible():self.menu_closed()

    def interaction_mouse_double_click(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.drag=None;self.drag_press=None
            if self.pet_head(event.position()):event.accept()
