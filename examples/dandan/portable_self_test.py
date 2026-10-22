"""Bounded frozen-package checks; explicit hidden command-line opt-in only.

This module sends events only to the pet's own Qt widget.  It never moves the
system cursor or injects keyboard input.  Source clocks are advanced on purpose:
the resulting wall time is not an animation latency or performance benchmark.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent


def run_checks(app, pet, report_path) -> int:
    report_path = Path(report_path).resolve()
    started = time.perf_counter()
    deadline = started + 90.0
    checks = []
    screenshots = []
    expected_ids = set(range(1, 41))
    demo_required_ids = expected_ids - {15, 16}
    demo_coverage = set()
    stage = 'initializing'
    result = {'status': 'failed', 'clock': 'artificial source-clock advance; not a speed benchmark',
              'input': 'Qt events to this widget only; native keyboard polling disabled',
              'frozen': bool(getattr(sys, 'frozen', False)), 'checks': checks,
              'screenshots': screenshots, 'errors': []}

    def ensure(condition, message):
        if not condition:
            raise AssertionError(message)

    def context():
        return {'stage': stage, 'clip': pet.key, 'pending': getattr(pet, 'pending', None),
                'index': pet.index, 'state': pet.state, 'waiting': pet.waiting,
                'interaction': pet.interaction, 'queue': list(pet.queue[:6])}

    def pump():
        if time.perf_counter() >= deadline:
            raise TimeoutError('90-second self-test limit exceeded: ' + json.dumps(context()))
        if pet.quitting:
            return
        if pet.dragging:
            pet.drag_last_move = time.perf_counter()
        if pet.waiting:
            pet.tick()
            app.processEvents()
            # Give frame-reader worker threads their GIL turn; QTest.qWait can
            # hold it during Python worker startup on some Windows builds.
            time.sleep(.001)
            return
        if pet.fading:
            pet.fade_start = time.perf_counter() - pet.fade_duration - .001
            pet.tick()
            app.processEvents()
            return
        ensure(bool(pet.key), 'No active clip outside a loading state')
        ensure(pet.assets[pet.key]['start'] == pet.state,
               'Active clip starts in another posture: ' + json.dumps(context()))
        now = time.perf_counter()
        # Quiet sections use an invertible timeline.  Advancing raw fps would
        # overshoot or jump backwards when that timeline changes.
        media_seconds = pet.timeline.media_seconds(pet.index + 1.05)
        pet.clip_start = now - media_seconds / pet.playback_rate
        pet.rate_tick = now
        # This is a state/asset functional check, not a wall-clock blend check.
        # Allow each real crossfade its first rendered frame, then age it out
        # so a 4-second source clip advanced in milliseconds doesn't blend its
        # complete contents into the prior frame and hide the current action.
        if pet.index > 0:
            pet.activated_at = min(pet.activated_at, now - max(.81, pet.blend_seconds + .01))
        pet.tick()
        app.processEvents()

    def until(predicate, label):
        while not predicate():
            ensure(not pet.quitting, 'Pet exited before ' + label)
            pump()

    def completed(key=None, since=0):
        return [event for event in pet.events[since:]
                if event['event'] == 'complete' and (key is None or event.get('clip') == key)]

    def idle_pose(pose):
        pet.command_pose(pose)
        idle = {'stand_front': 'stand_idle_rest', 'sit_front': 'sit_rest',
                'lie_front': 'lie_quiet'}[pose]
        until(lambda: pet.command_target is None and not pet.waiting
              and pet.key == idle and pet.index >= 2, 'manual pose ' + pose)
        ensure(pet.state == pose, 'Manual pose reached the wrong state')

    def head_point():
        ensure(pet.current is not None, 'No current alpha image for head hit test')
        ys, xs = np.nonzero(pet.current[:, :, 3] > 128)
        ensure(len(xs) > 0, 'Current dog is empty')
        candidates = []
        for y in np.linspace(ys.min(), ys.max(), 20):
            for x in np.linspace(xs.min(), xs.max(), 20):
                point = QPoint(round(x * pet.width() / pet.canvas_size),
                               round(y * pet.height() / pet.canvas_size))
                if pet.head_hit(point):
                    candidates.append(point)
        ensure(bool(candidates), 'No valid head point for ' + pet.state)
        mean = QPoint(round(sum(p.x() for p in candidates) / len(candidates)),
                      round(sum(p.y() for p in candidates) / len(candidates)))
        return min(candidates, key=lambda p: (p - mean).manhattanLength())

    def mouse(kind, point, button, buttons):
        local = pet.mapFromGlobal(point)
        event = QMouseEvent(kind, QPointF(local), QPointF(point), button, buttons,
                            Qt.KeyboardModifier.NoModifier)
        app.sendEvent(pet, event)

    def screenshot(name):
        if pet.current is None:
            return
        frame = np.ascontiguousarray(pet.current)
        image = QImage(frame.data, frame.shape[1], frame.shape[0], frame.strides[0],
                       QImage.Format.Format_RGBA8888_Premultiplied)
        destination = report_path.parent / (report_path.stem + '_' + name + '.png')
        ensure(image.save(str(destination)), 'Could not save alpha screenshot')
        screenshots.append(str(destination))

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        pet.timer.stop()
        pet.stats_timer.stop()
        pet.input_timer.stop()
        pet.set_keyboard_enabled(False)
        pet.cancel_interactions()
        pet.random_mode = False
        pet.set_through(False)
        # Rendering runs in pet.tick; avoiding OS paints keeps the accelerated
        # source-frame check independent of desktop compositor load.
        pet.hide()
        startup = list(pet.config.get('startup', []))
        demo = list(pet.config.get('demo', []))
        sequence = startup + demo
        listed = {pet.assets[key].get('source_id') for key in sequence}
        missing = sorted(demo_required_ids - listed)
        result['missing_demo_source_ids'] = missing
        result['lifecycle_sources_deferred_to_normal_exit'] = [15, 16]
        ensure(not missing, 'Configured startup/demo omit source IDs: ' + str(missing))
        ensure(startup and pet.waiting and pet.pending == startup[0],
               'Self-test must begin immediately after normal Pet construction')
        pet.queue = startup[1:] + demo
        event_start = len(pet.events)
        stage = 'startup and full configured demo'
        until(lambda: len(completed(since=event_start)) >= len(sequence), stage)
        full_demo = completed(since=event_start)[:len(sequence)]
        ensure([event['clip'] for event in full_demo] == sequence,
               'Full demo completed a sequence different from the configuration')
        demo_coverage = {pet.assets[event['clip']].get('source_id') for event in full_demo}
        missing = sorted(demo_required_ids - demo_coverage)
        ensure(not missing, 'Source IDs never completed: ' + str(missing))
        checks.append({'check': 'startup_and_all_actions', 'status': 'passed',
                       'completed_clips': len(sequence),
                       'source_ids': sorted(x for x in demo_coverage if x is not None),
                       'departure_sources_checked_at_normal_exit': [15, 16]})

        stage = 'manual posture commands'
        for pose in ('sit_front', 'lie_front', 'stand_front'):
            idle_pose(pose)
            checks.append({'check': 'manual_pose', 'pose': pose, 'status': 'passed'})
        screenshot('standing')

        stage = 'head double-click gestures'
        for pose, clip in (('stand_front', 'pet_stand'), ('sit_front', 'pet_sit'),
                           ('lie_front', 'pet_paws')):
            idle_pose(pose)
            before = len(pet.events)
            point = pet.mapToGlobal(head_point())
            mouse(QEvent.Type.MouseButtonDblClick, point, Qt.MouseButton.LeftButton,
                  Qt.MouseButton.LeftButton)
            ensure(pet.interaction and pet.interaction['kind'] == 'pet',
                   'Head double-click did not trigger petting: ' + pose)
            until(lambda: bool(completed(clip, before)) and pet.interaction is None
                  and pet.state == pose, 'pet gesture ' + pose)
            checks.append({'check': 'head_double_click', 'pose': pose,
                           'completed_clip': clip, 'status': 'passed'})

        stage = 'left and right Qt drag gestures'
        idle_pose('stand_front')
        point = pet.mapToGlobal(head_point())
        mouse(QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton,
              Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, point - QPoint(45, 0), Qt.MouseButton.NoButton,
              Qt.MouseButton.LeftButton)
        ensure(pet.dragging and pet.interaction and pet.interaction['kind'] == 'walk_left',
               'Left drag did not select walk_left')
        before = len(pet.events)
        until(lambda: bool(completed('walk_left_loop', before)), 'left walking loop')
        mouse(QEvent.Type.MouseMove, pet.drag_last + QPoint(60, 0), Qt.MouseButton.NoButton,
              Qt.MouseButton.LeftButton)
        ensure(pet.interaction and pet.interaction['kind'] == 'walk_right',
               'Right drag did not select walk_right')
        before = len(pet.events)
        until(lambda: bool(completed('walk_right_loop', before)), 'right walking loop')
        mouse(QEvent.Type.MouseButtonRelease, pet.drag_last, Qt.MouseButton.LeftButton,
              Qt.MouseButton.NoButton)
        ensure(not pet.dragging, 'Drag grab remained active after Qt release')
        until(lambda: pet.interaction is None and pet.state == 'stand_front', 'walk return')
        checks.append({'check': 'left_right_drag_and_release', 'status': 'passed'})

        stage = 'free play menu toggle and special-action immunity'
        idle_pose('stand_front')

        def click_free_button():
            pet.open_controls()
            pet.radial.opened=time.perf_counter()-.6
            pet.radial.animate()
            local=pet.radial.centers[3]
            global_point=pet.radial.mapToGlobal(local.toPoint())
            event=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(local),QPointF(global_point),
                             Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            app.sendEvent(pet.radial,event)
            pet.radial.hide()

        click_free_button()
        ensure(pet.free_play and pet.free_play_label()=='取消自由活动', 'Free menu did not become a cancel toggle')
        ensure(pet.free_play_action.isChecked() and pet.free_play_action.text()=='取消自由活动',
               'Tray free-play state/text are not synchronized')
        before=len(pet.events)
        ensure(not pet.start_stream('typing') and not pet.start_stream('walk_left')
               and not pet.start_stream('walk_right'), 'Free play accepted a special stream')
        ensure(not pet.pet_head(head_point()), 'Free play accepted head petting')
        old_keyboard=pet.keyboard_activity
        old_enabled=pet.keyboard_enabled
        reset_calls=[]

        def no_poll(now):
            raise AssertionError('Free play polled native keyboard activity')

        pet.keyboard_activity=SimpleNamespace(reset=lambda:reset_calls.append(True),poll=no_poll)
        pet.keyboard_enabled=True
        try:
            pet.input_cooldown=0
            pet.poll_interactions()
            ensure(reset_calls and pet.keyboard_enabled, 'Free play lost the keyboard-link preference')
        finally:
            pet.keyboard_activity=old_keyboard
            pet.keyboard_enabled=old_enabled
        point=pet.mapToGlobal(head_point())
        position=pet.pos()
        mouse(QEvent.Type.MouseButtonPress,point,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove,point+QPoint(45,0),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease,point+QPoint(45,0),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
        ensure(pet.pos().x()==position.x()+45 and pet.interaction is None and pet.free_play,
               'Free play did not preserve manual repositioning without a special action')
        ensure(not any(row['event']=='interaction_requested' for row in pet.events[before:]),
               'An owner special leaked through free play')
        pet.open_controls()
        pet.radial.opened=time.perf_counter()-.6
        pet.radial.animate()
        menu_image=report_path.parent/(report_path.stem+'_free_play_menu.png')
        ensure(pet.radial.grab().save(str(menu_image)), 'Could not render free-play menu')
        screenshots.append(str(menu_image))
        pet.radial.hide()
        snapshot=(pet.key,pet.state,pet.index)
        click_free_button()
        ensure(not pet.free_play and pet.free_play_label()=='自由活动'
               and not pet.free_play_action.isChecked(), 'Second free menu click did not restore normal mode')
        ensure((pet.key,pet.state,pet.index)==snapshot, 'Cancel free play reset the current animation')
        checks.append({'check':'free_play_toggle_immunity_and_reposition','status':'passed'})

        stage = 'typing cancellation at the current reached posture'
        idle_pose('lie_front')
        until(lambda: not pet.waiting and pet.key == 'paws_idle_rest' and pet.index >= 2,
              'lying head-down setup')
        for action, settled, idle in (('head_up', 'lie_front', 'lie_quiet'),
                                      ('lie_up', 'sit_front', 'sit_rest')):
            ensure(pet.start_stream('typing'), 'Typing request was rejected')
            until(lambda: not pet.waiting and pet.key == action and pet.index >= 25,
                  'typing approach ' + action)
            before = len(pet.events)
            pet.stop_stream()
            until(lambda: pet.interaction is None and pet.state == settled
                  and not pet.waiting and pet.key == idle and pet.index >= 5,
                  'cancel approach into ' + settled)
            forbidden = {'typing_enter', 'sit_up'}
            if action == 'head_up':
                forbidden.add('lie_up')
            leaked = [event['clip'] for event in pet.events[before:]
                      if event['event'] == 'start' and event.get('clip') in forbidden]
            ensure(not leaked, 'Cancelled approach continued into ' + str(leaked))
            checks.append({'check': 'stop_typing_approach', 'stage': action,
                           'settled_pose': settled, 'status': 'passed'})

        stage = 'typing resume, forward loop and stop'
        ensure(pet.start_stream('typing'), 'Typing resume was rejected')
        before = len(pet.events)
        until(lambda: bool(completed('typing_loop', before)), 'typing loop')
        screenshot('typing')
        pet.set_free_play(True)
        until(lambda: pet.interaction is None and pet.state == 'stand_front', 'typing return')
        ensure(pet.free_play, 'Free play was lost while finishing typing')
        checks.append({'check': 'typing_resume_loop_stop', 'status': 'passed'})
        checks.append({'check': 'enable_free_play_during_typing_natural_exit', 'status': 'passed'})

        stage = 'normal animated exit'
        before = len(pet.events)
        pet.request_exit()
        until(lambda: pet.quitting, 'normal animated shutdown')
        exit_completed = [event['clip'] for event in completed(since=before)]
        ensure('turn_exit' in exit_completed and 'exit_effect' in exit_completed,
               'Shutdown did not complete the actual departure clips')
        ensure(any(event['event'] == 'exit_animation_complete' for event in pet.events[before:]),
               'Shutdown did not reach its animation-complete marker')
        runtime_errors = [event for event in pet.events if event['event'] == 'error']
        ensure(not runtime_errors, 'Runtime error events: ' + json.dumps(runtime_errors))
        final_coverage = {pet.assets[event['clip']].get('source_id')
                          for event in completed() if event['clip'] in pet.assets}
        ensure(expected_ids <= final_coverage,
               'Final complete source coverage is missing ' + str(sorted(expected_ids - final_coverage)))
        checks.append({'check': 'normal_animated_exit', 'completed': exit_completed,
                       'status': 'passed'})
        checks.append({'check': 'all_40_source_ids_completed', 'status': 'passed',
                       'source_ids': sorted(expected_ids)})
        result['status'] = 'passed'
        code = 0
    except Exception as exc:
        result['errors'].append({'stage': stage, 'type': type(exc).__name__,
                                 'message': str(exc), 'context': context(),
                                 'traceback': traceback.format_exc()})
        code = 1
        if not pet.quitting:
            try:
                pet.force_close()
            except Exception:
                result['errors'].append({'stage': 'force_close', 'traceback': traceback.format_exc()})
    finally:
        completed_coverage = {pet.assets[event['clip']].get('source_id')
                              for event in completed() if event['clip'] in pet.assets}
        result.update(elapsed_seconds=round(time.perf_counter() - started, 3),
                      source_ids_completed=sorted(x for x in completed_coverage if x is not None),
                      missing_source_ids=sorted(expected_ids - completed_coverage),
                      quitting=bool(pet.quitting), events=pet.events,
                      accelerated_skipped_counter=pet.skipped)
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            # A report write failure must never be reported as a successful
            # independent-package check, even after the animation shut down.
            traceback.print_exc()
            code = 1
    return code
