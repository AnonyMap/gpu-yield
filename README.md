# gpu-yield

[![tests](https://github.com/AnonyMap/gpu-yield/actions/workflows/tests.yml/badge.svg)](https://github.com/AnonyMap/gpu-yield/actions/workflows/tests.yml)

Keeps a local LLM loaded on the living-room PC while it sits in Steam's Gaming Mode, and gets it out of the GPU's way the moment a game starts.

| Situation | What happens |
|---|---|
| No game running | Ollama runs with the model preloaded in VRAM, reachable from your LAN |
| Game starts | Ollama is stopped within ~2 s, freeing all of its VRAM. A stand-in takes over port 11434 and answers every request with `503` and a plain-English reason |
| Game closes | After a 45 s cooldown (so launchers and quick game switches don't cause reload churn), Ollama starts and the model is reloaded |
| PC wakes from sleep | Ollama is restarted, since GPU compute can be unreliable after resume |
| Ollama crashes, hangs or drops the model | It is restarted and the model reloaded, checked every 15 s |
| The config file has a mistake | The AI stays off (a game could start at any time) and clients are told why, until the file is fixed |

Games are detected through Steam's `reaper` helper. Steam launches every game in your library through it, non-Steam shortcuts included, and it lives exactly as long as the game. Windows games started outside Steam, such as from Heroic, are caught by Wine's `wineserver` process. No per-game launch options are needed.

## Install

On the living-room PC, in a terminal (over SSH or in Desktop Mode):

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o /tmp/gpu-yield
python3 /tmp/gpu-yield setup
```

Setup copies itself into place, so the downloaded file is just a one-time installer (`/tmp` is cleared on reboot). The same two commands upgrade an install from before `gpu-yield update` existed (1.0.x). Setup keeps your existing config.

Setup runs as your normal user and asks for `sudo` only for the firewall rule and Wake-on-LAN. It:

1. asks which model to use, from a short list sized for your hardware; you can also type any Ollama model name, or choose none and pick later
2. writes `~/.config/gpu-yield/config.toml`, then offers to disable any other Ollama service (it would hold the port and the GPU during games)
3. installs Ollama into `~/.local/opt/ollama` (no system changes, nothing layered with rpm-ostree), with the GPU backend picked for your card: ROCm on the RX 7000 series, RX 6800/6900 and RX 9000; Vulkan on others such as the RX 6700 XT
4. installs itself to `~/.local/bin/gpu-yield` and enables a systemd **user** service, with linger so it starts at boot
5. offers a firewall rule that opens the AI port (11434) and the status page (11435) to your local subnet only
6. offers to turn on Wake-on-LAN, so other computers can wake this PC
7. downloads the model you chose and loads it into VRAM
8. prints the Hermes config to paste on the other computer

Options: `--model` (skips the question), `--context`, `--backend auto|rocm|vulkan`, `--reinstall`, `--skip-pull`, `--no-firewall`, `-y`. Without a terminal to ask in, or with `-y`, setup installs no model rather than guessing.

To choose or change the model later:

```bash
gpu-yield model              # pick from the list
gpu-yield model qwen3.5:9b   # or name it directly
```

This downloads the model, loads it, saves it in the config and restarts the watcher. Update `model.default` in Hermes's config to match.

## Updating

From any computer that can SSH to the living-room PC:

```bash
ssh you@livingroom-pc gpu-yield update
```

| Command | What it does |
|---|---|
| `gpu-yield update --check` | Shows the installed and latest versions of gpu-yield and Ollama |
| `gpu-yield update` | Downloads the latest gpu-yield from this repo and installs it |
| `gpu-yield update --file PATH` | Installs a local copy instead of the GitHub version (one downloaded from a chat, or your own edit). Older or same-numbered files are allowed, with a note |
| `gpu-yield update --ollama` | Also updates Ollama, if a newer release is out |
| `gpu-yield rollback` | Returns to the version before the last update; run it again to redo |

To push a file from your desktop without going through GitHub:

```bash
scp gpu-yield you@livingroom-pc:~/gpu-yield-new
ssh you@livingroom-pc gpu-yield update --file ~/gpu-yield-new
```

`update` and `update --check` show what changed between your version and the new one, from [CHANGELOG.md](CHANGELOG.md).

**Automatic updates** are off by default. To turn them on:

```bash
gpu-yield config auto_update gpu-yield   # or "all" to update Ollama too
```

The watcher checks once a day and installs only while no game is running and nobody has used the AI for 15 minutes, so an update never interrupts a game or a conversation. Its log is in `journalctl --user -u gpu-yield-autoupdate`, and `gpu-yield rollback` undoes one. After a rollback, automatic updates skip the version you left until a newer one comes out or you run `gpu-yield update` yourself.

Before replacing anything, an update runs the new file's own self-test, and a version that fails it is refused, leaving the working one in place. Ollama updates work the same way: the new version is downloaded and unpacked alongside the old one, and only swapped in once it runs, so a dropped connection never leaves you without Ollama. After swapping in, the new version rewrites its service files and restarts the watcher, so the model reloads once. Updates come from the `main` branch of `AnonyMap/gpu-yield`; to use a fork, set `update_repo` in the config.

## Model choice

The PC's 32 GB of system RAM shapes these picks. Ollama (0.30+) handles a mixture-of-experts model that doesn't fit in VRAM by moving expert weights into system RAM first and keeping attention on the GPU. Each token only reads a few experts, so going a little over VRAM costs some speed rather than failing or collapsing to a crawl. Stopping Ollama for a game frees that RAM along with the VRAM.

These are the options setup offers. The context length defaults to 64K for all of them.

**Recommended: gpt-oss:20b.**

- It has native, well-trained tool calling, and Ollama has dedicated support for its format. That makes it the most likely to work with Hermes on the first try.
- It is mixture-of-experts with about 3.6B parameters active per token, so it generates quickly.
- At around 13 GB plus 64K of context it sits at or slightly over 16 GB. Any overflow goes to RAM as expert weights. Hermes's docs call 32K the minimum and recommend 64K, and the RAM makes the larger size cheap to take.

**Step up: qwen3.6:35b-a3b (~23 GB).** It's a stronger agent model, with roughly 10 GB of experts living in RAM. Generation stays usable since only about 3B parameters are active. The cost is prompt processing: experts held in RAM have to cross the PCIe bus for every batch, and Hermes's prompts are large because of its tool definitions. Expect the first turn of a session to take several times longer than with gpt-oss; later turns reuse the cached prefix. It isn't the recommendation because its tool calling through Ollama is less proven (the previous Qwen generation had parsing problems there). Try it once gpt-oss is working:

```bash
gpu-yield model qwen3.6:35b-a3b
```

**Small and fast: qwen3.5:9b (6.6 GB).** It fits entirely in VRAM with plenty of room, accepts images, and responds fastest, but has a clearly lower ceiling. There have been reports of it printing tool calls as text instead of executing them through Ollama, so test it with Hermes before relying on it.

```bash
gpu-yield model qwen3.5:9b
```

**Smallest: qwen3.5:4b (3.4 GB).** Fine for quick questions and light chat; expect it to struggle with Hermes's multi-step tool use.

`gpu-yield doctor` shows how much of the model is on the GPU. For a dense model, anything under 100% means layers spilled into RAM and it will be slow; lower `context_length` or add `OLLAMA_KV_CACHE_TYPE = "q8_0"` under `[ollama_env]`. For a mixture-of-experts model, partial placement is expected and doctor says so. Gaming Mode's own UI holds some VRAM, so a model that barely fits on paper may not.

## Hermes config

On the other computer, in `~/.hermes/config.yaml` (setup prints this with your IP filled in):

```yaml
model:
  default: gpt-oss:20b
  provider: custom
  base_url: http://<this-pc-ip>:11434/v1
  context_length: 65536
  ollama_num_ctx: 65536
```

Set the context numbers to match `context_length` in gpu-yield's config. If Hermes asks for a different size than the one preloaded, Ollama reloads the model on the first request. If they are left unset, Hermes has been known to request the model's full 256K window, which won't fit. When a client loads a different model or context than gpu-yield's, `gpu-yield status` shows a note saying what to change.

If you configure a cloud fallback in Hermes, the stand-in's 503 is what triggers it, so mid-game requests can fail over instead of just failing.

## From your other computers

**Status page.** Open `http://<living-room-pc>:11435` in any browser, including your phone. It shows whether the AI is ready, which game is running, when the AI was last used, and any problem worth fixing. It refreshes every 5 seconds. The same data is at `/status` as JSON, for scripts or for an agent that wants to check before calling. The page is read-only.

**gpu-yield on your desktop.** The remote commands need only the script, not an install:

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o ~/.local/bin/gpu-yield
chmod +x ~/.local/bin/gpu-yield
gpu-yield status --host avignon.home    # the living-room PC's status
gpu-yield wake avignon.home             # wake it up, wait until the AI answers
```

**Waking the PC.** If the living-room PC goes to sleep, Hermes can't reach it. Run `gpu-yield wol` on the living-room PC once to turn on Wake-on-LAN (setup offers this too). Then `gpu-yield wake <pc>` from another computer wakes it and waits until it answers. `wake` needs to have seen the PC awake once, through `status --host` or an earlier `wake`, to learn its network card's address; or pass that address directly, as `gpu-yield wol` prints it. Wake-on-LAN needs a wired connection, and some PCs also need it enabled in the BIOS/UEFI, often called "Wake on LAN" or "Power on by PCI-E".

If you'd rather the PC didn't sleep at all, set Steam's own sleep timer to *Never* (Steam → Settings → Power). gpu-yield can't keep the PC awake itself: Linux lets a user override their own sleep blocks, and gpu-yield runs as the same user as Steam, so Steam's sleep would ignore it.

## Changing settings

```bash
gpu-yield config                          # list all settings
gpu-yield config context_length 32768     # change one; it's checked, saved and applied
gpu-yield config extra_processes "factorio,Game.exe"
gpu-yield config env.OLLAMA_KV_CACHE_TYPE q8_0   # Ollama environment variables ("" removes)
```

An invalid value is refused and the file is left as it was. You can still edit `~/.config/gpu-yield/config.toml` by hand; apply that with `systemctl --user restart gpu-yield`.

## Everyday commands

```bash
gpu-yield status        # what it's doing right now, with the running game's name
gpu-yield pause         # keep the AI off until resume (survives reboots)
gpu-yield resume
gpu-yield doctor        # end-to-end check
gpu-yield model         # choose or change the model
gpu-yield config        # see "Changing settings"
gpu-yield update        # see "Updating"
gpu-yield wol           # Wake-on-LAN for this PC
gpu-yield firewall      # open the AI and status ports to the local network
journalctl --user -u gpu-yield -u gpu-yield-ollama -f    # live log
```

The Ollama service is regenerated from the config, so don't edit it directly.

## Limits

- Games launched through Steam are detected by Steam's `reaper`. Windows games launched any other way (Heroic, Lutris) are detected by Wine's `wineserver` process; this also covers Heroic's "Add to Steam" shortcuts, which can hand the game to an already-open Heroic and exit early. Native Linux games launched outside Steam aren't detected; add their process name to `extra_processes`. Set `detect_wine = false` if you run Windows utilities through Wine that shouldn't stop the AI.
- The stand-in only exists while Ollama is stopped. There's a gap of under a second during each handoff where connections are refused.
- Ollama has no authentication. Setup only opens the port to your subnet; don't forward it on your router.

## Testing

**Tested on Bazzite** in Steam Gaming Mode, on a PC with a Radeon RX 7800 XT (16 GB) and 32 GB of RAM, running gpt-oss:20b at a 64K context:

- install with `setup`, and upgrading an earlier install by re-running it
- detecting a Steam game launched from Big Picture and stopping Ollama to free the GPU
- reloading the model after the game closed and the cooldown ended

**Tested only in simulation**, against fake processes and a fake Ollama server:

- the 503 stand-in replies
- pause and resume
- restart after sleep
- `update`, `rollback` and Ollama updates, including failed downloads
- Wine/Proton detection for games launched outside Steam
- game names (Steam manifests, non-Steam shortcuts)
- recovering from a crashed, hung or emptied Ollama, and from a broken config
- the status page, `status --host`, `wake`, automatic updates, the `config` command, and backend choice

After installing on new hardware, run `gpu-yield doctor`, then launch a game and check `gpu-yield status`.

Wake-on-LAN setup (`nmcli`, `ethtool`, BIOS) can't be simulated; check it with `gpu-yield wol` and a real `gpu-yield wake`.

The automated tests run on GitHub for every change. They need only Python 3.11+ (and `zstd` for the Ollama install tests). They run in a throwaway home folder and don't touch a real install:

```bash
python3 tests/run_tests.py
```

## Uninstall

```bash
gpu-yield uninstall            # services only
gpu-yield uninstall --purge    # also Ollama, config and state
```

Downloaded models stay in `~/.ollama/models` until you delete them.
