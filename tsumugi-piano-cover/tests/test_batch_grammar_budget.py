from __future__ import annotations

import torch

from piano_ar.beat_tokenizer import BeatTokenizer
from piano_ar.config import BeatTokenizerConfig
from piano_ar.tokenizer import BatchGrammar, EOP, PEDAL_OFF, PEDAL_ON


def make_grammar():
    tokenizer = BeatTokenizer(BeatTokenizerConfig(max_patch_tokens=8))
    grammar = BatchGrammar(tokenizer, rows=1, device="cpu")
    grammar.reset(torch.tensor([False]), time_limit=tokenizer.config.max_bar_beats * tokenizer.ticks_per_beat)
    grammar.stage.fill_(grammar.EVENT_START)
    grammar.onset.fill_(0)
    return tokenizer, grammar


def test_same_onset_note_fits_when_three_event_tokens_and_terminator_remain():
    tokenizer, grammar = make_grammar()
    grammar.length.fill_(4)  # 3 note tokens + EOP fit exactly in 8 tokens.

    allowed = grammar.allowed()[0]
    assert allowed[tokenizer.pitch_offset]
    assert not allowed[tokenizer.time_offset + 1]
    assert allowed[EOP]


def test_same_onset_pedal_change_uses_its_one_token_budget():
    tokenizer, grammar = make_grammar()
    grammar.length.fill_(6)  # 1 pedal token + EOP fit exactly in 8 tokens.

    allowed = grammar.allowed()[0]
    assert allowed[PEDAL_ON]
    assert not allowed[tokenizer.pitch_offset]
    assert not allowed[tokenizer.time_offset + 1]
    assert allowed[EOP]


def test_pedal_release_is_not_dropped_when_only_two_slots_remain():
    tokenizer, grammar = make_grammar()
    grammar.pedal_down.fill_(True)
    grammar.length.fill_(6)

    allowed = grammar.allowed()[0]
    assert allowed[PEDAL_OFF]
    assert not allowed[PEDAL_ON]


def test_no_event_is_allowed_when_only_the_terminator_slot_remains():
    tokenizer, grammar = make_grammar()
    grammar.length.fill_(7)

    allowed = grammar.allowed()[0]
    assert allowed[EOP]
    assert not allowed[PEDAL_ON]
    assert not allowed[PEDAL_OFF]
    assert not allowed[tokenizer.pitch_offset]
