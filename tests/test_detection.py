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
def labels(**over): time.sleep(0.25); return sorted(set(gy.scan_games(dict(gy.DEFAULTS, **over)).values()))
gy._label_cache.clear(); (cfgdir / "shortcuts.vdf").write_bytes(shortcuts + b"\x08")
p = [spawn("reaper", "SteamLaunch", "AppId=1091500", "--", "x")]
assert labels() == ["Cyberpunk 2077 (AppId 1091500)"]
p[0].kill(); p[0].wait()
p = [spawn("/opt/wine/bin/wineserver"), spawn(r"Z:\games\Hades2\Hades2.exe"), spawn(r"C:\windows\system32\services.exe")]
assert labels() == ["Hades2.exe (Wine, outside Steam)"]; ok("Wine game outside Steam is named after its .exe, ignoring Wine helpers")
assert labels(detect_wine=False, extra_processes=["HADES2"]) == ["Hades2.exe"]
for x in p: x.kill(); x.wait()
q = [spawn("/home/u/games/factorio/bin/x64/factorio")]
assert labels(extra_processes=["factorio"]) == ["factorio"] and labels() == []
q[0].kill(); q[0].wait()
ok("names: Steam manifests in any library, non-Steam shortcuts (all id forms), .exe fallback")

# Wine games outside Steam, and how they interact with Steam's own Proton games
w = spawn("/opt/proton/files/bin/wineserver")
assert labels() == ["Wine/Proton game (outside Steam)"]
assert labels(detect_wine=False) == []
r = spawn("reaper", "SteamLaunch", "AppId=1820000", "--", "game")
assert labels() == ["The Riftbreaker (AppId 1820000)"]
assert labels(ignore_appids=["1820000"]) == [], "an ignored Steam Proton game must not count via wineserver"
for p in (r, w):
    p.kill(); p.wait()
assert labels() == []
ok("wineserver counts only when no Steam game is running; ignore_appids still works")

# extra_processes also matches the short name `ps -e` shows (15 chars)
import shutil, subprocess
exe = home / "averyverylongnativegame"; shutil.copy("/bin/sleep", exe)
long_name = subprocess.Popen([str(exe), "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
assert labels(extra_processes=["averyverylongna"]) == ["averyverylongnativegame"]
long_name.kill(); long_name.wait()
ok("extra_processes matches the truncated name ps shows")
print("detection tests passed")
