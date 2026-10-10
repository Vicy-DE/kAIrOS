"""kAIrOS bench — qtile 0.31 as window manager only (candidate 3a): floating windows,
no decorations, no bar, no key/mouse bindings, no menus [R28]. Every window floats;
the kernel-side driver (bench/proto/wmproto.py) does all window ops over qtile's IPC."""
import re

from libqtile import hook, layout
from libqtile.config import Group, Match, Screen

keys = []
mouse = []
groups = [Group("a")]
layouts = [layout.Max()]
floating_layout = layout.Floating(border_width=0, float_rules=[Match(title=re.compile(".*"))])
screens = [Screen(), Screen()]        # one per monitor; extra monitors get default Screens
bring_front_click = True
follow_mouse_focus = False
cursor_warp = False
focus_on_window_activation = "focus"
auto_minimize = False
auto_fullscreen = False
reconfigure_screens = True
wmname = "qtile"


@hook.subscribe.client_new
def _float_everything(window):
    window.floating = True
