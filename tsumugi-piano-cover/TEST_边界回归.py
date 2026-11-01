# -*- coding: utf-8 -*-
"""Fast local tests: no model weights, no GPU, no audio downloads."""
from __future__ import annotations
import json
import sys
import types
from pathlib import Path

import numpy as np

root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
from piano_ar.tokenizer import PianoTokenizer, KIND_NOTE, KIND_PEDAL_ON, KIND_PEDAL_OFF
from piano_cover.source import SourceVocab,source_features,TYPE_NOTE,TYPE_KEY,TYPE_CHORD,TYPE_KEY_STATE,TYPE_CHORD_STATE

# Boundary-state test for V2 with an initial key/chord before first generated bar.
class Grid:
    bar_starts=np.array([4,8,12],dtype=np.int64)
    num_bars=2
    def beat_position(self, t): return np.asarray(t,dtype=np.float64)/50.0
rows=np.asarray([
    [0,TYPE_KEY,8,0,0,0],
    [0,TYPE_CHORD,5,4,12,0],
    [250,TYPE_NOTE,60,25,90,1],
    [400,TYPE_CHORD,7,4,12,0],  # at the exact second-bar start
    [450,TYPE_NOTE,67,25,90,1],
],dtype=np.int32)
original=rows.copy()
tokenizer=PianoTokenizer()
vocab=SourceVocab(tokenizer.config)
result=source_features(rows,500,tokenizer,vocab,max_rows=64,grid=Grid(),ticks_per_beat=12)
assert np.array_equal(rows,original),'source_features must not mutate shared input rows'
o=vocab.offset['type']
first=result['features'][0,result['valid'][0],0]-o
second=result['features'][1,result['valid'][1],0]-o
assert TYPE_KEY_STATE in first,'intro key state was lost'
assert TYPE_CHORD_STATE in first,'intro chord state was lost'
assert TYPE_KEY_STATE in second,'ongoing key state was lost'
assert TYPE_CHORD in second,'chord change on exact bar boundary was lost'
assert TYPE_CHORD_STATE not in second,'stale chord was incorrectly injected on change boundary'

# Mock symusic's small constructor API to check how event_to_score closes CC64.
class Note:
    def __init__(self,time,duration,pitch,velocity,ttype='second'):
        self.time=float(time);self.duration=float(duration);self.pitch=pitch;self.velocity=velocity
    def copy(self): return Note(self.time,self.duration,self.pitch,self.velocity)
class ControlChange:
    def __init__(self,time,number,value,ttype='second'):
        self.time=float(time);self.number=number;self.value=value
    def copy(self):return ControlChange(self.time,self.number,self.value)
class EventList(list):
    def sort(self):
        super().sort(key=lambda event: event.time)
class Track:
    def __init__(self,name='',program=0,ttype='second'):
        self.notes=EventList();self.controls=EventList()
        self.pitch_bends=EventList();self.pedals=EventList();self.lyrics=EventList()
    def sort(self):
        self.notes.sort()
class Score:
    def __init__(self,ttype='second'):
        self.tracks=[]
    def sort(self):
        for t in self.tracks:
            t.sort()
            t.controls.sort()
    def end(self):
        return max((n.time+n.duration for t in self.tracks for n in t.notes),default=0.)
symusic=types.SimpleNamespace(ControlChange=ControlChange,Note=Note,Score=Score,Track=Track)
sys.modules['symusic']=symusic

events=np.asarray([
    [0,KIND_PEDAL_ON,0,0,0],
    [0,KIND_NOTE,60,100,88],
    [10,KIND_PEDAL_OFF,0,0,0],
    [15,KIND_PEDAL_ON,0,0,0],
    [30,KIND_NOTE,64,70,88],
],dtype=np.int32)
s=tokenizer.events_to_score(events)
ped=s.tracks[0].controls
assert ped[-1].value==0 and ped[-1].time > 1.0,'final CC64 release not inserted'
assert len(s.tracks[0].notes)==2,'pedal correction altered generated notes'

print('PASS 2 boundary regression cases: state history and pedal release')
