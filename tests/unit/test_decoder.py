import isolation  # noqa: F401  (must precede deckforge imports)
import unittest

from deckforge.keyboard import (MAC_RUN_SLOTS, WINDOWS_RUN_SLOTS, KeyboardMacroDecoder, _TEXT_BY_CODE)

CODE_FOR_CHAR = {char.lower(): code for code, char in _TEXT_BY_CODE.items()}
SUPER, R, ENTER, CTRL, SPACE, ESC = 125, 19, 28, 29, 57, 1

# Tokens captured on this unit with `deckforge listen --keyboard --grab`, in the
# physical order the keys were pressed (row by row).
CAPTURED_ORDER = ["calc", "cmd", "control", "devmgmt.msc", "explorer",
                  "wordpad", "notepad", "perfmon", "powershell", "taskmgr"]
EXPECTED_SLOTS = ["0_0", "0_1", "0_2", "0_3", "0_4", "1_0", "1_1", "1_2", "1_3", "1_4"]


def tap(code, t):
    return [{"code": code, "value": 1, "time": t}, {"code": code, "value": 0, "time": t + .01}]


def run_macro(token, *, prefix="super_r", start=0.0):
    """Key events of a firmware launcher macro, including releases."""
    t = start
    events = []
    if prefix == "super_r":
        events += [{"code": SUPER, "value": 1, "time": t}, {"code": R, "value": 1, "time": t + .01},
                   {"code": R, "value": 0, "time": t + .02}, {"code": SUPER, "value": 0, "time": t + .03}]
    elif prefix == "command_space":
        events += [{"code": SUPER, "value": 1, "time": t}, {"code": SPACE, "value": 1, "time": t + .01},
                   {"code": SPACE, "value": 0, "time": t + .02}, {"code": SUPER, "value": 0, "time": t + .03}]
    elif prefix == "plain_r":
        events += tap(R, t)
    t += .1
    for char in token:
        events += tap(CODE_FOR_CHAR[char], t)
        t += .03
    events += tap(ENTER, t)
    return events


def feed_all(decoder, events):
    return [slot for slot in (decoder.feed(e) for e in events) if slot]


class CapturedWindowsMacros(unittest.TestCase):
    def test_every_captured_token_maps_to_its_physical_position(self):
        decoder = KeyboardMacroDecoder()
        slots = []
        for index, token in enumerate(CAPTURED_ORDER):
            slots += feed_all(decoder, run_macro(token, start=index * 2.0))
        self.assertEqual(slots, EXPECTED_SLOTS)

    def test_plain_text_variants_without_super_map_too(self):
        for token, slot in WINDOWS_RUN_SLOTS.items():
            for prefix in ("plain_r", None):
                with self.subTest(token=token, prefix=prefix):
                    self.assertEqual(feed_all(KeyboardMacroDecoder(), run_macro(token, prefix=prefix)), [slot])

    def test_repeated_letters_and_dot_are_kept(self):
        # "powershell" has a doubled l; "devmgmt.msc" has a dot.
        decoder = KeyboardMacroDecoder()
        self.assertEqual(feed_all(decoder, run_macro("powershell")), ["1_3"])
        self.assertEqual(feed_all(decoder, run_macro("devmgmt.msc", start=5)), ["0_3"])

    def test_last_match_describes_the_macro(self):
        decoder = KeyboardMacroDecoder()
        feed_all(decoder, run_macro("calc"))
        self.assertEqual(decoder.last_match, "macro calc")

    def test_macro_missing_its_super_r_prefix_still_maps(self):
        # Seen in the logs right after a reconnect: only "calc" + Enter arrived.
        self.assertEqual(feed_all(KeyboardMacroDecoder(), run_macro("calc", prefix=None)), ["0_0"])

    def test_stray_enter_and_autorepeat_do_not_trigger(self):
        decoder = KeyboardMacroDecoder()
        self.assertEqual(feed_all(decoder, tap(ENTER, 0)), [])
        events = run_macro("calc")
        events.insert(6, {"code": CODE_FOR_CHAR["c"], "value": 2, "time": .2})  # autorepeat
        self.assertEqual(feed_all(decoder, events), ["0_0"])

    def test_unknown_token_is_reported_not_mapped(self):
        decoder = KeyboardMacroDecoder()
        self.assertEqual(feed_all(decoder, run_macro("regedit")), [])
        self.assertEqual(decoder.last_unmatched_command, "regedit")

    def test_escape_cancels_pending_macro(self):
        decoder = KeyboardMacroDecoder()
        events = run_macro("calc")
        enter_index = len(events) - 2
        events[enter_index:enter_index] = tap(ESC, 1.0)
        self.assertEqual(feed_all(decoder, events), [])


class OtherLayers(unittest.TestCase):
    def test_mac_spotlight_tokens(self):
        for token, slot in MAC_RUN_SLOTS.items():
            with self.subTest(token=token):
                self.assertEqual(feed_all(KeyboardMacroDecoder(), run_macro(token, prefix="command_space")), [slot])

    def test_bottom_shortcuts_with_releases(self):
        for letter, expected in (("s", "2_0"), ("z", "2_1"), ("c", "2_2")):
            for modifier, label in ((CTRL, "Ctrl"), (SUPER, "Command")):
                with self.subTest(letter=letter, modifier=modifier):
                    decoder = KeyboardMacroDecoder()
                    events = [{"code": modifier, "value": 1}, {"code": CODE_FOR_CHAR[letter], "value": 1},
                              {"code": CODE_FOR_CHAR[letter], "value": 0}, {"code": modifier, "value": 0}]
                    self.assertEqual(feed_all(decoder, events), [expected])
                    self.assertEqual(decoder.last_match, f"atalho {label}+{letter.upper()}")

    def test_shortcut_after_unfinished_macro_still_maps(self):
        decoder = KeyboardMacroDecoder()
        partial = run_macro("calc")[:-2]  # Enter never arrived
        events = partial + [{"code": CTRL, "value": 1}, {"code": CODE_FOR_CHAR["s"], "value": 1}]
        self.assertEqual(feed_all(decoder, events), ["2_0"])


class Robustness(unittest.TestCase):
    def test_dropped_events_reset_stuck_modifiers(self):
        decoder = KeyboardMacroDecoder()
        decoder.feed({"code": SUPER, "value": 1})  # release lost with SYN_DROPPED
        decoder.feed({"dropped": True, "code": -1, "value": -1})
        self.assertEqual(feed_all(decoder, run_macro("cmd", prefix=None)), ["0_1"])

    def test_stale_plain_text_is_discarded_after_a_long_pause(self):
        decoder = KeyboardMacroDecoder()
        feed_all(decoder, tap(CODE_FOR_CHAR["x"], 0.0))  # junk typed long before
        self.assertEqual(feed_all(decoder, run_macro("calc", prefix=None, start=30.0)), ["0_0"])


if __name__ == "__main__":
    unittest.main()
