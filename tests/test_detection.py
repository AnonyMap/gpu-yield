"""Game detection: Steam reaper, game names, Wine games outside Steam, extra_processes."""
import os, struct, time
from pathlib import Path
from _util import fake_process, load, ok
gy = load()
home = Path(os.environ["HOME"])
steam = home / ".local/share/Steam"; (steam / "steamapps").mkdir(parents=True)
lib2 = home / "games2"; (lib2 / "steamapps").mkdir(parents=True)
(steam / "steamapps/libraryfolders.vdf").write_text(f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{steam}"\n\t}}\n\t"1"\n\t{{\n\t\t"path"\t\t"{lib2}"\n\t}}\n}}\n')
(steam / "steamapps/appmanifest_1091500.acf").write_text('"AppState"\n{\n\t"appid"\t\t"1091500"\n\t"name"\t\t"Cyberpunk 2077"\n}\n')
(lib2 / "steamapps/appmanifest_1820000.acf").write_text('"AppState"\n{\n\t"appid"\t\t"1820000"\n\t"name"\t\t"The Riftbreaker"\n}\n')
(home / ".steam").mkdir(); (home / ".steam/steam").symlink_to(steam)     # same root twice
def vstr(k, v): return b"\x01" + k.encode() + b"\0" + v.encode() + b"\0"
def vint(k, v): return b"\x02" + k.encode() + b"\0" + struct.pack("<I", v)
def vmap(k, body): return b"\x00" + k.encode() + b"\0" + body + b"\x08"
SC = 3456789012
shortcuts = vmap("shortcuts", vmap("0", vint("appid", SC) + vstr("AppName", "Hades II (Heroic)") + vstr("Exe", '"/usr/bin/flatpak"') + vmap("tags", b"")))
cfgdir = steam / "userdata/12345/config"; cfgdir.mkdir(parents=True); (cfgdir / "shortcuts.vdf").write_bytes(shortcuts + b"\x08")
assert gy.parse_binary_vdf(shortcuts + b"\x08")["shortcuts"]["0"]["AppName"] == "Hades II (Heroic)"
checks = [
 (("1091500", []), "Cyberpunk 2077 (AppId 1091500)"),
 (("1820000", []), "The Riftbreaker (AppId 1820000)"),
 ((str(SC), []), f"Hades II (Heroic) (AppId {SC})"),
 ((str(SC - 2**32), []), f"Hades II (Heroic) (AppId {SC - 2**32})"),
 ((str((SC << 32) | 0x02000000), []), f"Hades II (Heroic) (AppId {(SC << 32) | 0x02000000})"),
 (("999", [b"reaper", b"SteamLaunch", b"AppId=999", b"--", b"/x/proton", b"waitforexitandrun", b"/games/Elden Ring/eldenring.exe"]), "eldenring (AppId 999)"),
 (("998", [b"reaper", b"--", b"/usr/bin/native-game"]), "Steam AppId 998"),
]
for (appid, argv), want in checks:
    got = gy.game_label(appid, argv); assert got == want, (got, want)
# corrupt shortcuts file must not break anything
(cfgdir / "shortcuts.vdf").write_bytes(b"\x00shortcuts\0\x09junk"); gy._label_cache.clear()
assert gy.game_label("777", []) == "Steam AppId 777"

spawn = fake_process
import subprocess, sys
gpu_dir = home / "dri"; gpu_dir.mkdir(); (gpu_dir / "renderD128").write_text("")
gy.GPU_DEVICE_DIR = str(gpu_dir) + "/"


def spawn_gpu(argv0, env=None):
    """A fake Windows program that has the (fake) GPU device open, like a real game."""
    script = f"f = open({str(gpu_dir / 'renderD128')!r}); import time; time.sleep(120)"
    return subprocess.Popen([argv0, "-c", script], executable=sys.executable,
                            env=dict(os.environ, **(env or {})),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def labels(**over): time.sleep(0.25); return sorted(set(gy.scan_games(dict(gy.DEFAULTS, **over)).values()))
gy._label_cache.clear(); (cfgdir / "shortcuts.vdf").write_bytes(shortcuts + b"\x08")
p = [spawn("reaper", "SteamLaunch", "AppId=1091500", "--", "x")]
assert labels() == ["Cyberpunk 2077 (AppId 1091500)"]
p[0].kill(); p[0].wait()
p = [spawn("/opt/wine/bin/wineserver"), spawn_gpu(r"Z:\games\Hades2\Hades2.exe"), spawn_gpu(r"C:\windows\system32\services.exe")]
assert labels() == ["Hades2.exe (Wine, outside Steam)"]; ok("Wine game outside Steam is named after its .exe, ignoring Wine helpers")
assert labels(detect_wine=False, extra_processes=["HADES2"]) == ["Hades2.exe"]
for x in p: x.kill(); x.wait()
q = [spawn("/home/u/games/factorio/bin/x64/factorio")]
assert labels(extra_processes=["factorio"]) == ["factorio"] and labels() == []
q[0].kill(); q[0].wait()
ok("names: Steam manifests in any library, non-Steam shortcuts (all id forms), .exe fallback")

# Wine running briefly for something that isn't a game must not count (1.6.0 bug:
# Proton's xalia.exe helper and Steam background tasks stopped the AI)
w = spawn("/opt/proton/files/bin/wineserver")
assert labels() == [], "Wine's background process alone is not a game"
others = [spawn(r"C:\windows\system32\xalia.exe"), spawn_gpu(r"C:\Program Files\Xalia\xalia.exe"),
          spawn(r"C:\Program Files (x86)\Steam\bin\iscriptevaluator.exe"),
          spawn_gpu(r"C:\_CommonRedist\vcredist\2022\VC_redist.x64.exe"),
          spawn(r"Z:\games\SomeTool\SomeTool.exe")]
assert labels() == [], labels()
ok("Wine helpers, installers, and programs not using the GPU don't count as games")
g = spawn_gpu(r"Z:\games\Hades2\Hades2.exe")
assert labels() == ["Hades2.exe (Wine, outside Steam)"]
assert labels(detect_wine=False) == []
g.kill(); g.wait()
r = spawn("reaper", "SteamLaunch", "AppId=1820000", "--", "game")
pg = spawn_gpu(r"Z:\games\Riftbreaker\Riftbreaker.exe", env={"STEAM_COMPAT_APP_ID": "1820000", "SteamAppId": "1820000"})
assert labels() == ["The Riftbreaker (AppId 1820000)"]
assert labels(ignore_appids=["1820000"]) == [], "an ignored Steam Proton game must not count via Wine"
# a Heroic/Lutris game beside it: umu marks its own (often 0), and a game started from an
# ignored Heroic shortcut inherits the shortcut's SteamAppId. Neither hides it.
g = spawn_gpu(r"Z:\games\Hades2\Hades2.exe", env={"STEAM_COMPAT_APP_ID": "0", "SteamAppId": "1820000"})
assert labels(ignore_appids=["1820000"]) == ["Hades2.exe (Wine, outside Steam)"], \
    "an ignored Steam app (Heroic as a shortcut, say) mustn't hide Heroic's games"
z = spawn("reaper", "SteamLaunch", "AppId=0", "--", "x")
assert labels(ignore_appids=["1820000"]) == ["Hades2.exe (Wine, outside Steam)"]
for p in (r, w, g, pg, z, *others):
    p.kill(); p.wait()
assert labels() == []
ok("a Windows game using the GPU counts, only when no Steam game is running; ignore_appids still works")

# The watcher waits 5 s before acting on a Windows program outside Steam
wt = gy.Watcher(dict(gy.DEFAULTS, port=1))
wt.state = "ai"                                  # the wait only applies while the AI is on
real_age = gy.process_age
gy.process_age = lambda pid: 0.0                 # a program that has just started
wine_game = {1: "Hades2.exe" + gy.WINE_SUFFIX}
steam_game = {2: "The Riftbreaker (AppId 1820000)"}
assert wt.confirm_wine(steam_game, 100.0) == steam_game, "Steam games count at once"
assert wt.confirm_wine(wine_game, 100.0) == {}
assert wt.confirm_wine(wine_game, 104.0) == {}
assert wt.confirm_wine(wine_game, 105.5) == wine_game
assert wt.confirm_wine({}, 106.0) == {} and wt.wine_since is None
assert wt.confirm_wine(wine_game, 107.0) == {}, "a new Wine run starts the wait again"
gy.process_age = lambda pid: 60.0 if pid == 77 else 0.0   # the .exe (pid 77) has run a minute
gy._GPU_EXES[:] = [77]
wt.wine_since = None
assert wt.confirm_wine(wine_game, 200.0) == wine_game, "a game that was already running counts at once"
gy._GPU_EXES[:] = [78]                           # an old wineserver doesn't make a new .exe old
wt.wine_since = None
assert wt.confirm_wine(wine_game, 201.0) == {}, "aged by the Windows program, not by wineserver"
gy._GPU_EXES.clear()
gy.process_age = lambda pid: 0.0
wt.wine_since = None
for state in (None, "paused", "cooldown", "gaming"):
    wt.state = state
    assert wt.confirm_wine(wine_game, 300.0) == wine_game, f"no wait while the AI is off ({state})"
gy.process_age = real_age
import os
assert 0 <= gy.process_age(os.getpid()) < 600 and gy.process_age(999999999) == 0.0
ok("a Windows program outside Steam must keep running 5 s before the AI stops (only while it's on)")

# extra_processes also matches the short name `ps -e` shows (15 chars)
import subprocess, sys
# A link to this Python, not a copy of /bin/sleep: where coreutils is one program that picks
# the tool by its own name (uutils, as on newer Ubuntu), a renamed copy exits at once. The
# kernel names the process after the link, and Python finds its library through it.
exe = home / "averyverylongnativegame"; exe.symlink_to(Path(sys.executable).resolve())
long_name = subprocess.Popen([str(exe), "-c", "import time; time.sleep(60)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
assert labels(extra_processes=["averyverylongna"]) == ["averyverylongnativegame"]
long_name.kill(); long_name.wait()
ok("extra_processes matches the truncated name ps shows")
print("detection tests passed")
