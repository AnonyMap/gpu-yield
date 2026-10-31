# gpu-yield

Keeps a local LLM loaded on the living-room PC while it sits in Steam's Gaming Mode, and gets it out of the GPU's way the moment a game starts.

| Situation | What happens |
|---|---|
| No game running | Ollama runs with the model preloaded in VRAM, reachable from your LAN |
| Game starts | Ollama is stopped within ~2 s, freeing all of its VRAM. A stand-in takes over port 11434 and answers every request with `503` and a plain-English reason |
| Game closes | After a 45 s cooldown (so launchers and quick game switches don't cause reload churn), Ollama starts and the model is reloaded |
| PC wakes from sleep | Ollama is restarted, since GPU compute can be unreliable after resume |

Games are detected through Steam's `reaper` helper. Steam launches every game in your library through it, non-Steam shortcuts included, and it lives exactly as long as the game. Windows games started outside Steam, such as from Heroic, are caught by Wine's `wineserver` process. No per-game launch options are needed.

## Install

On the living-room PC, in a terminal (over SSH or in Desktop Mode):

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o ~/gpu-yield
python3 ~/gpu-yield setup
```

The same two commands upgrade an install from before `gpu-yield update` existed (1.0.x). Setup keeps your existing config.

Setup runs as your normal user and asks for `sudo` only for the firewall rule. It:

1. writes `~/.config/gpu-yield/config.toml`
2. offers to disable any other Ollama service (it would hold the port and the GPU during games)
3. installs Ollama into `~/.local/opt/ollama` with the ROCm backend (no system changes, nothing layered with rpm-ostree)
4. installs itself to `~/.local/bin/gpu-yield` and enables a systemd **user** service, with linger so it starts at boot
5. offers a firewall rule that opens port 11434 to your local subnet only
6. pulls the model and loads it into VRAM
7. prints the Hermes config to paste on the other computer

Options: `--model`, `--context`, `--backend vulkan`, `--reinstall`, `--skip-pull`, `--no-firewall`, `-y`.

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

Before replacing anything, an update runs the new file's own self-test, and a version that fails it is refused, leaving the working one in place. After swapping in, the new version rewrites its service files and restarts the watcher, so the model reloads once. Updates come from the `main` branch of `AnonyMap/gpu-yield`; to use a fork, set `update_repo` in the config.

## Model choice

The PC's 32 GB of system RAM shapes these picks. Ollama (0.30+) handles a mixture-of-experts model that doesn't fit in VRAM by moving expert weights into system RAM first and keeping attention on the GPU. Each token only reads a few experts, so going a little over VRAM costs some speed rather than failing or collapsing to a crawl. Stopping Ollama for a game frees that RAM along with the VRAM.

**Default: gpt-oss:20b at a 64K context.**

- It has native, well-trained tool calling, and Ollama has dedicated support for its format. That makes it the most likely to work with Hermes on the first try.
- It is mixture-of-experts with about 3.6B parameters active per token, so it generates quickly.
- At around 13 GB plus 64K of context it sits at or slightly over 16 GB. Any overflow goes to RAM as expert weights. Hermes's docs call 32K the minimum and recommend 64K, and the RAM makes the larger size cheap to take.

**Step up: qwen3.6:35b-a3b (~23 GB).** It's a stronger agent model, with roughly 10 GB of experts living in RAM. Generation stays usable since only about 3B parameters are active. The cost is prompt processing: experts held in RAM have to cross the PCIe bus for every batch, and Hermes's prompts are large because of its tool definitions. Expect the first turn of a session to take several times longer than with gpt-oss; later turns reuse the cached prefix. It isn't the default because its tool calling through Ollama is less proven (the previous Qwen generation had parsing problems there). Try it once gpt-oss is working:

```bash
gpu-yield setup --model qwen3.6:35b-a3b
```

**Small and fast: qwen3.5:9b (6.6 GB).** It fits entirely in VRAM with plenty of room, accepts images, and responds fastest, but has a clearly lower ceiling. There have been reports of it printing tool calls as text instead of executing them through Ollama, so test it with Hermes before relying on it.

```bash
gpu-yield setup --model qwen3.5:9b
```

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

Set the context numbers to match `context_length` in gpu-yield's config. If Hermes asks for a different size than the one preloaded, Ollama reloads the model on the first request. If they are left unset, Hermes has been known to request the model's full 256K window, which won't fit.

If you configure a cloud fallback in Hermes, the stand-in's 503 is what triggers it, so mid-game requests can fail over instead of just failing.

## Everyday commands

```bash
gpu-yield status        # what it's doing right now
gpu-yield pause         # keep the AI off until resume (survives reboots)
gpu-yield resume
gpu-yield doctor        # end-to-end check
gpu-yield update        # see "Updating"
journalctl --user -u gpu-yield -u gpu-yield-ollama -f    # live log
```

Edit `~/.config/gpu-yield/config.toml`, then run `systemctl --user restart gpu-yield`. The Ollama service is regenerated from the config, so don't edit it directly.

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
- `update` and `rollback`
- Wine/Proton detection for games launched outside Steam

After installing on new hardware, run `gpu-yield doctor`, then launch a game and check `gpu-yield status`.

## Uninstall

```bash
gpu-yield uninstall            # services only
gpu-yield uninstall --purge    # also Ollama, config and state
```

Downloaded models stay in `~/.ollama/models` until you delete them.
