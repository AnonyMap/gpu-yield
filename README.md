# gpu-yield

[![tests](https://github.com/AnonyMap/gpu-yield/actions/workflows/tests.yml/badge.svg)](https://github.com/AnonyMap/gpu-yield/actions/workflows/tests.yml)

Turn a Linux gaming PC into a home AI server that gets out of the way when you play. While the PC sits idle in Steam's Gaming Mode, gpu-yield keeps a local LLM loaded in Ollama and available to the rest of your network. The moment a game starts, it shuts the AI down and frees the GPU for the game. When the game ends, the model comes back on its own.

It's a single Python file with no dependencies, built for [Bazzite](https://bazzite.gg) and similar Steam-based setups.

| Situation | What happens |
|---|---|
| No game running | Ollama runs with your model preloaded in VRAM, reachable from your local network |
| Game starts | Ollama is stopped within about 2 s, freeing all of its VRAM and RAM. A stand-in answers on Ollama's port with `503` and a plain-English reason |
| Game closes | After a cooldown (45 s by default, so launchers and quick game switches don't cause reload churn), Ollama starts and the model is reloaded |
| PC wakes from sleep | Ollama is restarted, since GPU compute can be unreliable after resume |
| Ollama crashes, hangs or drops the model | It is restarted and the model reloaded; checked every 15 s |
| The config file has a mistake | The AI stays off (a game could start at any time) and clients are told why, until the file is fixed |

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Choosing a model](#choosing-a-model)
- [Connecting clients](#connecting-clients)
- [Checking on it](#checking-on-it)
- [Controls on the status page](#controls-on-the-status-page)
- [From other computers](#from-other-computers)
- [Sleep and Wake-on-LAN](#sleep-and-wake-on-lan)
- [BC-250 and other shared-memory chips](#bc-250-and-other-shared-memory-chips)
- [Game detection](#game-detection)
- [Settings](#settings)
- [Updating](#updating)
- [Command reference](#command-reference)
- [Files it creates](#files-it-creates)
- [Limits and security](#limits-and-security)
- [Example setup](#example-setup)
- [Testing](#testing)
- [Uninstall](#uninstall)

## Requirements

- **Linux with systemd**, where games are launched through Steam. Built and tested on Bazzite in Gaming Mode; other Fedora Atomic images and SteamOS-like setups should work the same way.
- **Python 3.11 or newer** (included with Bazzite).
- **An AMD GPU** for the managed Ollama install. gpu-yield picks ROCm or Vulkan for your card automatically. Other GPUs are untested.
- **A wired network connection** for reliable Wake-on-LAN. Some Wi-Fi cards can wake the PC too ([Sleep and Wake-on-LAN](#sleep-and-wake-on-lan)).

Nothing is layered onto the system image: everything installs into your home folder and runs as your user.

## Install

On the gaming PC, in a terminal (over SSH or in Desktop Mode):

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o /tmp/gpu-yield
python3 /tmp/gpu-yield setup
```

Setup copies itself into place, so the downloaded file is only a one-time installer. Run setup as your normal user; it asks for `sudo` only for the firewall and Wake-on-LAN steps. It:

1. asks which model to keep loaded, from a short list sized for your hardware; you can type any Ollama model name instead, or choose none and pick later
2. writes `~/.config/gpu-yield/config.toml`, and offers to disable any other Ollama service (it would hold the port and the GPU during games)
3. downloads Ollama into `~/.local/opt/ollama`, with the GPU backend chosen for your card (see [Settings](#settings))
4. installs itself to `~/.local/bin/gpu-yield` and enables a systemd user service, with linger, so it starts at boot without anyone logging in
5. offers a firewall rule that opens the AI port (11434) and the status page (11435) to your local network only
6. offers to turn on Wake-on-LAN, so other computers can wake this PC
7. downloads the model you chose and loads it
8. prints the address and settings to give your AI clients

Setup options: `--model NAME` (skips the question), `--context TOKENS`, `--backend auto|rocm|vulkan`, `--reinstall` (re-download Ollama), `--skip-pull`, `--no-firewall`, `-y` (accept all prompts). Without a terminal to ask in, or with `-y`, setup installs no model rather than guessing.

Re-running setup is safe: it keeps your config, and it's also how to upgrade an install from before `gpu-yield update` existed (1.0.x).

## Choosing a model

```bash
gpu-yield model              # pick from the list
gpu-yield model qwen3.5:9b   # or name any Ollama model directly
```

This downloads the model, loads it, saves it to the config and restarts the watcher. If you switch, it tells you how to delete the old one. Apps set to the model name `gpu-yield` follow the switch automatically ([Connecting clients](#connecting-clients)).

The list setup offers:

| Model | Download | At 64K context | Notes |
|---|---|---|---|
| `qwen3.5:9b` | ~6.6 GB | ~10.5 GB | Recommended. Fits a 12 GB card with an agent's 64K context, fast, accepts images |
| `gemma4:12b-it-qat` | ~7.2 GB | ~12 GB | Alternative with built-in tool calling and image input; needs a 16 GB card at 64K |
| `qwen3.5:4b` | ~3.4 GB | ~5 GB | Smallest. Fine for quick questions; weak at multi-step tool use |

**Fitting a model to your hardware.** What has to fit is the model *plus its context*, the conversation memory, which grows with `context_length`. Agents like Hermes need 64K, and depending on the model's design that costs anywhere from 2 GB (Qwen 3.5, which uses full attention in only a quarter of its layers) to 10 GB or more (ordinary dense models such as Ministral 14B or Devstral 24B). So a download that looks small enough can still be far too big. Gaming Mode's own interface also holds between a few hundred MB and about 2 GB, and that can grow after the model has loaded, so leave at least 1.5 GB free. As a rough guide:

| GPU memory | Good starting point at 64K context |
|---|---|
| 8 GB | `qwen3.5:4b`, or `qwen3.5:9b` with `context_length` 32768 |
| 12 GB | `qwen3.5:9b`. If the card also drives a desktop, use `context_length` 32768 for headroom |
| 16 GB | `qwen3.5:9b` with several GB to spare, or `gemma4:12b-it-qat` |
| 24 GB or more | Room for bigger models, such as `qwen3.6:27b` |

**Models I'd avoid on a 16 GB card:**
- `gpt-oss:20b`: about 15 GB at 64K, so it can run out of GPU memory when Steam's interface grows. On the example setup it also did poorly on real agent tasks.
- Anything whose download is close to or bigger than the card, such as `qwen3.6:35b-a3b`, `gemma4:26b`, `devstral-small-2` or `glm-4.7-flash`.

**On ROCm, models must fit on the card.** Ollama can run part of a model from system RAM. On Vulkan that only costs speed, but on ROCm the example PC's driver stalled doing it, and the PC shut down twice (see [Example setup](#example-setup)). So on ROCm, gpu-yield checks every model as it loads. If any of it lands outside the card's memory, gpu-yield unloads it straight away and goes back to the last model and context that fit, with an entry in the recent events saying why. That includes a context change that makes the current model too big, and `gpu-yield model` refuses such a model without switching to it.

An app can also load a model, or a context size, that doesn't fit, for example Hermes set to a bigger context than gpu-yield's. Ollama finishes that one request before it can be unloaded, so gpu-yield unloads it afterwards and reloads its own model. If the app loads it again, gpu-yield pauses the AI. The stand-in and the status page say why, and the AI stays off until you fix the app's setting and run `gpu-yield resume`.

To allow models that don't fit anyway, set `allow_partial_gpu` to `true`. On Vulkan you only get a warning. A model running entirely on the CPU isn't treated as one that doesn't fit: that means the GPU backend is broken, and `doctor` says so.

**System RAM.** On ROCm, Ollama's service is limited to a third of the PC's RAM by default (`ollama_ram_limit`; about 10 GB on a 32 GB PC). Elsewhere `"auto"` sets no limit, because part of a model may run from RAM on purpose there; set a size to limit it anyway. Ollama is always kept out of swap. If it goes over, Linux stops Ollama alone instead of the whole PC slowing to a crawl, and gpu-yield reloads the model and says what happened. If that repeats within 10 minutes, gpu-yield stops reloading and tells you instead. If RAM runs out for any other reason, Linux stops Ollama before Steam, Gaming Mode or a game. Memory the graphics driver sets aside for the card may not count toward the limit, which is one more reason for the ROCm check above.

**Comparing models.** The status page's speed test (or `gpu-yield speed`) measures how fast the loaded model *reads* a 2,000-token prompt and *writes* an answer, and keeps the results so models can be compared side by side. Reading speed matters most for agents like Hermes, which send long prompts. Run it while nothing else is using the AI: requests wait behind the test, and Ollama's cached conversation is replaced, so the next long request is read again from the start. The page asks first if the AI was used in the last two minutes; `gpu-yield speed` needs `--force` then.

`gpu-yield doctor` shows how much of the loaded model is on the GPU and says whether that's expected. If it reports the model running entirely on the CPU, the GPU backend isn't working; doctor suggests the fix.

To make context use less memory, at a small quality cost: `gpu-yield config env.OLLAMA_KV_CACHE_TYPE q8_0`. It only works when Ollama uses flash attention, and helps most with dense models; Qwen 3.5's context is already small.

## Connecting clients

gpu-yield runs a normal Ollama server on port 11434, so anything that talks to Ollama, or to an OpenAI-compatible API, can use it:

- Ollama API: `http://<gaming-pc>:11434`
- OpenAI-compatible API: `http://<gaming-pc>:11434/v1` (no API key needed)

**Use the model name `gpu-yield`.** gpu-yield keeps that name pointing at whichever model is loaded, so an app set to use it follows every model switch without being reconfigured. It's an Ollama copy of the model, which shares the model's files, so it takes no extra download or disk space. Change the name, or turn it off, with the `alias` setting; the old copy is removed. gpu-yield never overwrites a model it didn't create: if you already have a model with that name, status shows a note asking you to pick another.

**Match the context length.** Configure your client to use the same context length as gpu-yield (`gpu-yield config context_length` prints it; 65536 by default). If a client asks for a different size, Ollama reloads the model to suit it, which is slow and may not fit. When a client loads a different model or context, `gpu-yield status` and the status page show a note saying what to change.

**While a game is running**, every request gets HTTP `503` with a `Retry-After` header and a reason, for example *"The AI server on gaming-pc is paused because a game is running. It comes back automatically about 45s after the game closes."* Requests to `/v1/...` get an OpenAI-style error body (`"code": "gpu_busy"`); others get Ollama's `{"error": "..."}`. A client with a fallback provider configured can use the 503 to fail over.

**Example: [Hermes Agent](https://github.com/NousResearch/hermes-agent)** on another computer, in `~/.hermes/config.yaml` (setup prints this with your address filled in):

```yaml
model:
  default: gpu-yield
  provider: custom
  base_url: http://<gaming-pc>:11434/v1
  context_length: 65536
  ollama_num_ctx: 65536
```

Hermes needs at least 32K of context and works better with 64K. Without the two context lines, it has been known to request a model's full advertised window, which won't fit.

## Checking on it

```bash
gpu-yield status     # state, running game, model, real memory use, recent events
gpu-yield doctor     # end-to-end check with fixes for anything wrong
journalctl --user -u gpu-yield -u gpu-yield-ollama -f    # live log
```

**If the model won't load because the GPU is out of memory**, run `gpu-yield restart` (or *Restart the AI* on the status page). It unloads the model, stops Ollama, waits until the driver reports the memory free again, and loads the model again. It also lists any other program still holding GPU memory, such as `gamescope-wl 900 MB, someapp 2.1 GB`. Steam's own interface (`gamescope`) normally holds a few hundred MB to about 2 GB. Anything big beyond that is what's crowding the model out, and a restart can't free it: close that program, or choose a smaller context. gpu-yield also does one restart by itself when a load runs out of memory, and if that doesn't help, it says what's holding the memory instead of retrying. `gpu-yield doctor` lists the programs using GPU memory at any time.

`status` shows games by name, for example `The Riftbreaker (AppId 1820000)`, including non-Steam shortcuts.

**Status page.** Open `http://<gaming-pc>:11435` in any browser, including a phone. It shows whether the AI is ready, which game is running, when the AI was last used, and anything worth fixing, and updates live. With a control password set, it can also change things ([Controls on the status page](#controls-on-the-status-page)).

Don't want it? `gpu-yield config status_page false` turns it off within a few seconds, without interrupting the AI; `true` brings it back. `gpu-yield status` still works on the PC itself, but `status --host` and `wake` from other computers need the page.

The same data is available as JSON at `/status`, for scripts or for an agent that wants to check before calling. Websites can't read it from your browser, since it names the PC and its network card:

| Field | Meaning |
|---|---|
| `ai_available` | `true` when the model is loaded and requests will be answered |
| `state` | `ai`, `gaming`, `cooldown` (game just closed) or `paused` |
| `retry_after` | Seconds to wait before trying again (0 when available) |
| `games` | Running games, by name |
| `model`, `context_length`, `model_status` | The configured model and its load state |
| `in_use`, `last_used` | Whether another machine is talking to the AI right now, and when it last did (Unix time) |
| `notice` | A problem worth fixing, such as a client loading a different context |
| `cooldown_remaining`, `since` | Seconds until the model reloads after a game; when the current state began |
| `alias`, `loaded_name` | The fixed model name for apps, and the name the model is loaded under |
| `memory` | Actual use from the driver and kernel, in bytes: `vram_used`, `vram_total`, `gtt_used` (system RAM mapped for the GPU), `ram_used`, `ram_total` |
| `events` | Recent events, newest first: `at` (Unix time), `text`, `kind` (`info`, `warn`, `error`) |
| `speed_results` | Recent speed tests: `model`, `read_tps`, `write_tps` (tokens per second), `context_length`, `at` |
| `host`, `version`, `port`, `mac` | The PC's name, gpu-yield version, AI port, and network card address (used by `wake`) |

## Controls on the status page

With a control password set, the status page can also manage the AI, from any browser on your network, including a phone:

- **Switch models** among the ones downloaded on the PC. Each shows its size, and models bigger than the GPU's memory are marked.
- **Change the context length** (8K to 128K). Ollama restarts with the new size and the model reloads.
- **Download a model** by name, with live progress and a Cancel button, and optionally switch to it when it finishes. You can type or paste:
  - an Ollama model name, such as `qwen3.5:9b`, or its ollama.com link
  - a Hugging Face GGUF repo, such as `hf.co/user/repo` or `hf.co/user/repo:Q4_K_M` to choose the quantization, or its huggingface.co link
- **Delete models** you no longer need, except the one in use. The page shows the free space on the drive that holds the models (`~/.ollama/models`, or `OLLAMA_MODELS` if you've moved it); other drives in the PC aren't counted, since Ollama doesn't use them.
- **Pause and resume** the AI.
- **Restart the AI:** unload the model, free the GPU memory and load it again, with a note of anything else holding GPU memory.
- **Run a speed test** of the loaded model, and compare it with earlier tests of other models.

The controls are off until you set a password on the gaming PC:

```bash
gpu-yield config control_password -
```

It asks for the password without showing it, so it stays out of your shell history. Press Enter at the prompt and it makes up a strong one and shows it once. Use at least 8 characters, and a password you don't use anywhere else: the page is plain HTTP, so it crosses your local network unencrypted. The page asks for it once per browser and remembers it; *Lock controls on this device* forgets it. Without a password the page stays read-only.

After 10 wrong passwords, the device they came from is locked out for a minute, then two, four, and so on up to an hour if it keeps guessing. Other devices aren't affected, so a guessing device can't lock you out of your own page. Open the page by the PC's name, alone or with a local-network ending (`gaming-pc`, `gaming-pc.local`, `.lan`, `.home`, `.home.arpa`, `.internal`, or your router's own domain), or by its IP address. The controls refuse requests addressed to any other name, which blocks websites from reaching them through your browser.

The controls cost the PC almost nothing. The page's server sits idle until a browser asks for something, and Ollama does the actual downloading and loading. Every change goes through the watcher, so a change made from the page can't collide with a game starting. Downloads and deletes need the AI running, so they're refused during a game with a message saying why. Model and context changes made during a game are saved and take effect when the AI comes back.

After switching models or context, set your AI apps to match ([Connecting clients](#connecting-clients)).

## From other computers

The remote commands need only the script, not an install, and should also work on macOS and Windows with Python 3.11+.

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o ~/.local/bin/gpu-yield
chmod +x ~/.local/bin/gpu-yield

gpu-yield status --host gaming-pc.local   # that PC's status
gpu-yield wake gaming-pc.local            # wake it up and wait until the AI answers
```

`--host` accepts a name, an IP address, `name:port`, or the status page URL pasted as-is. The page listens on IPv4 only, because `gpu-yield firewall` limits IPv4 to your local network, while IPv6 addresses are often reachable from the internet.

For anything else, use SSH, for example `ssh gaming-pc.local gpu-yield update`. Setting up an SSH key (`ssh-copy-id`) saves typing a password each time.

## Sleep and Wake-on-LAN

If the gaming PC goes to sleep, nothing on the network can reach the AI. There are two ways to handle that.

**Wake it when needed.** Run `gpu-yield wol` on the gaming PC once (setup offers this too). It turns on Wake-on-LAN for the network card, through NetworkManager so it survives reboots, and prints the card's address. Then, from another computer:

```bash
gpu-yield wake gaming-pc.local
```

This sends the wake-up signal and waits up to 2 minutes for the PC, then up to 3 more for the model to load. It exits with 0 once the AI is ready, and 1 if the PC is up but the AI isn't available (a game is running or it's paused), so `gpu-yield wake gaming-pc.local && my-script` only runs the script when the AI can answer. `wake` needs to know the PC's network card address. It learns that automatically the first time you run `gpu-yield status --host` against the PC while it's awake. You can also pass the address directly, for example `gpu-yield wake 1c:2b:3a:4d:5e:6f`.

Wake-on-LAN is most reliable over a wired connection. Some PCs also need it enabled in the BIOS/UEFI, often called *Wake on LAN*, *Power on by PCI-E* or *Resume by LAN*.

**On Wi-Fi**, some cards support *Wake on Wireless LAN*. `gpu-yield wol` checks whether yours does (using the `iw` tool) and turns it on if so. It's less dependable than wired: it wakes the PC from sleep but not from shut down, needs the router to stay on, and some Wi-Fi drivers don't honor it. Try `gpu-yield wake` once to see whether it works on your PC. If the card doesn't support it, the dependable option is to stop the PC from sleeping, below.

**Or don't let it sleep.** Set Steam's sleep timer to *Never* (Steam → Settings → Power). gpu-yield can't keep the PC awake itself: Linux lets a user override their own sleep blocks, and gpu-yield runs as the same user as Steam, so Steam's sleep would ignore a block from gpu-yield.

## BC-250 and other shared-memory chips

Some chips have no graphics memory of their own: the GPU borrows system memory instead. The AMD BC-250 board is one, along with most APUs. gpu-yield notices when a chip has only a small fixed slice of graphics memory (under 2 GB), and adjusts:

- **Readings.** The status page, `status` and `doctor` show the shared pool and how much of it the GPU is using. A model living in "system RAM" is how these chips work, so gpu-yield doesn't warn about it.
- **A fit check for the shared pool.** Model and system draw on the same memory, and running it out freezes the whole board. So gpu-yield refuses a model whose file is bigger than the pool minus about 3 GB, before loading it; that's about 12.5 GB on a 16 GB BC-250. It also unloads a model that leaves the rest of the PC less than 0.75 GB once loaded. Either way it goes back to the last model that fit. While a model runs, two low readings in a row (15 s apart) also unload it. Only an app's load that needs more memory than gpu-yield's own model and context gets blamed, and the AI is paused only if that app keeps doing it. If something else used the memory, gpu-yield says what, and tries again in 5 minutes. `allow_partial_gpu = true` turns this off.
- **Restart reports** count each program's borrowed memory, so they name what's really using it.
- **The model picker** measures models against the shared pool.
- **The model runner's RAM use is kept small.** llama-server, the program Ollama runs each model in, keeps a prompt cache in RAM of up to 8 GiB by default. On these chips that RAM is the GPU's memory too, so gpu-yield caps the cache at 512 MiB, one Muse Glimmer conversation at 64K (`env.LLAMA_ARG_CACHE_RAM` changes it), keeps 4 context snapshots, and loads model files with direct I/O.
- **Ollama is allowed to use the GPU.** These chips report themselves as integrated GPUs, which Ollama ignores by default and runs everything on the processor instead. gpu-yield sets `OLLAMA_IGPU_ENABLE=1` when it's the PC's only GPU (on a BC-250, always). On a PC that also has a graphics card it doesn't, so Ollama won't split models between the card and the processor's own graphics.

On a **BC-250** (detected by its chip ID), two more things happen:

- **Context storage at 8 bits** (`OLLAMA_KV_CACHE_TYPE=q8_0`) unless you set it yourself. It halves context memory in the shared pool. It also protects models with few KV heads, such as Muse Glimmer with 2, which 4-bit context storage can wreck. It needs Mesa 25.1 or newer for flash attention; on older Mesa, Ollama ignores it.
- **`doctor` checks the board's known pitfalls:** kernel versions with BC-250 bugs, the GPU memory limit (`ttm.pages_limit`, and conflicting places it's set), the BIOS memory split, IOMMU, a risky `HSA_OVERRIDE_GFX_VERSION`, ROCm being installed, the Mesa version, the GPU clock governor and its voltage settings, swap and zram, whether crash logs survive a reboot, and 4-bit context storage with a few-head model. It only reports; it never changes kernel, BIOS or governor settings. For crash history and temperatures, use the community BC-250 health check script.

gpu-yield uses Vulkan on these chips, chosen automatically. The board works the same whether or not you play games on it. Without games, gpu-yield is simply a model manager with a status page.

## Game detection

You don't need to set anything up per game.

- **Everything launched through Steam,** including non-Steam games added to your library. Steam starts each one through a small helper called `reaper`, which lives exactly as long as the game.
- **Windows games launched outside Steam,** such as from Heroic or Lutris, are caught through Wine. A Windows program counts as a game when it is using the GPU and has kept running for 5 seconds, so Wine helpers, installers and Steam's short background tasks don't stop the AI. This also covers Heroic's *Add to Steam* shortcuts, which can hand the game to an already-open Heroic and exit early. Turn it off with `gpu-yield config detect_wine false`.
- **Native Linux games launched outside Steam** aren't detected automatically. Add their process name: `gpu-yield config extra_processes "factorio,Game.exe"`. Names are matched against the program's file name or the name `ps -e` shows; case doesn't matter and `.exe` is optional.
- **To let a particular Steam game run alongside the AI**, add its AppId (shown by `gpu-yield status` while it runs): `gpu-yield config ignore_appids "1820000"`.

## Settings

```bash
gpu-yield config                          # list every setting and its current value
gpu-yield config context_length 32768     # change one: it's checked, saved and applied
gpu-yield config extra_processes "a,b"    # lists take commas
gpu-yield config env.OLLAMA_KV_CACHE_TYPE q8_0   # an Ollama environment variable ("" removes it)
```

An invalid value is refused and the file is left as it was. The file is kept readable only by you, since it can hold the control password and a GitHub token. You can also edit `~/.config/gpu-yield/config.toml` directly; the file explains each setting. Apply hand edits with `systemctl --user restart gpu-yield`.

| Setting | Default | What it does |
|---|---|---|
| `model` | none | Model kept loaded. Change it with `gpu-yield model`, which also downloads it |
| `context_length` | `65536` | Context window in tokens. Keep your clients set to the same number |
| `backend` | `"auto"` | `auto`, `rocm` or `vulkan`. `auto` picks ROCm on cards ROCm supports (RX 7000 series, RX 6800/6900, RX 9000, Radeon VII, Ryzen AI Max) and Vulkan on the rest (such as the RX 6700 XT or 6600). A change takes effect after `gpu-yield setup --reinstall` |
| `port` | `11434` | Port the AI listens on |
| `status_page` | `true` | Serve the status page. `false` turns it off; takes effect without restarting the AI |
| `status_port` | `11435` | Port of the status page |
| `cooldown_seconds` | `45` | Wait after a game closes before reloading the model |
| `poll_seconds` | `2` | How often to check for games (0.5 to 60) |
| `restart_after_suspend` | `true` | Restart Ollama after the PC wakes from sleep |
| `extra_processes` | `[]` | More programs that count as a game ([Game detection](#game-detection)) |
| `ignore_appids` | `[]` | Steam AppIds that should not stop the AI |
| `detect_wine` | `true` | Treat Windows programs outside Steam that are using the GPU as games |
| `auto_update` | `"off"` | `off`, `gpu-yield`, or `all` (gpu-yield and Ollama). See [Updating](#updating) |
| `alias` | `"gpu-yield"` | A fixed model name for your apps that always points at the loaded model; empty turns it off |
| `ollama_ram_limit` | `"auto"` | Most system RAM Ollama may use: `"auto"` (a third of the PC's RAM on ROCm, none elsewhere), `"off"`, or a size in GB like `"12"` |
| `allow_partial_gpu` | `false` | On ROCm, allow a model that doesn't fit entirely in the card's memory instead of unloading it. Not recommended |
| `control_password` | none | Turns on the [controls on the status page](#controls-on-the-status-page); at least 8 characters. `gpu-yield config` shows it only as "(set)" |
| `update_repo`, `update_branch` | this repo, `main` | Where updates come from, for forks |
| `github_token` | none | Read-only token, only needed if `update_repo` is private |
| `ollama_unit` | managed | Leave as is, unless you run Ollama yourself under another systemd user unit |
| `[ollama_env]` | none | Extra environment variables for Ollama, set with `gpu-yield config env.NAME VALUE` |

Settings written by older versions keep working: values in the wrong form, such as numbers in quotes, are read with a warning instead of stopping the AI.

## Updating

```bash
gpu-yield update --check   # what's available, and what changed
gpu-yield update           # install the latest gpu-yield from this repo
gpu-yield update --ollama  # also update Ollama, if a newer release is out
gpu-yield rollback         # back to the version before the last update; run again to redo
```

`update` shows the [changelog](CHANGELOG.md) entries between your version and the new one. To install a copy you have locally, such as your own edit, use `gpu-yield update --file PATH`. It also accepts older or same-numbered versions, with a note.

**Automatic updates** are off by default:

```bash
gpu-yield config auto_update gpu-yield   # or "all" to include Ollama
```

The watcher checks once a day (time asleep counts) and installs only while no game is running, the AI hasn't been used for 15 minutes, and no download or speed test from the status page is in progress, so an update never interrupts a game or a conversation. It checks for a game again right before installing. After a `rollback`, automatic updates skip the version you left until a newer one comes out or you run `gpu-yield update` yourself. The log is in `journalctl --user -u gpu-yield-autoupdate`.

**What protects an update:**

- The new file runs its own self-test first, and a version that fails is refused, leaving the working one in place.
- Ollama updates download and unpack alongside the current install and are only swapped in once they run, so a dropped connection never leaves you without Ollama.
- After swapping in, the new version updates its own service files and restarts, so changes take effect without re-running setup. The model reloads once.

## Command reference

| Command | What it does |
|---|---|
| `gpu-yield setup` | Install or repair everything ([Install](#install)) |
| `gpu-yield status [--host HOST]` | Current state, here or on another PC |
| `gpu-yield doctor` | Check the whole setup and suggest fixes |
| `gpu-yield pause` / `gpu-yield resume` | Keep the AI off until resumed; a pause survives reboots |
| `gpu-yield restart` | Unload the model, free GPU memory, and load it again, showing what else holds GPU memory |
| `gpu-yield model [NAME]` | Choose or change the model |
| `gpu-yield config [KEY [VALUE]]` | Show or change settings |
| `gpu-yield update [--check] [--ollama] [--file PATH] [--force]` | Update gpu-yield, and optionally Ollama |
| `gpu-yield rollback` | Undo the last update |
| `gpu-yield speed` | Measure how fast the loaded model reads and writes |
| `gpu-yield wol` | Show or turn on Wake-on-LAN for this PC |
| `gpu-yield wake [HOST\|MAC] [--no-wait]` | Wake a sleeping PC and wait until the AI answers |
| `gpu-yield firewall` | Open the AI and status ports to the local network |
| `gpu-yield uninstall [--purge]` | Remove it ([Uninstall](#uninstall)) |
| `gpu-yield run` | The watcher itself; systemd runs this |

## Files it creates

| Path | What |
|---|---|
| `~/.local/bin/gpu-yield` | The installed tool |
| `~/.local/opt/ollama/` | Ollama |
| `~/.ollama/models/` | Downloaded models (Ollama's usual location) |
| `~/.config/gpu-yield/config.toml` | Settings |
| `~/.config/systemd/user/gpu-yield.service` | The watcher service |
| `~/.config/systemd/user/gpu-yield-ollama.service` | Ollama's service, generated from the config (don't edit it) |
| `~/.local/state/gpu-yield/` | Pause flag, the previous version for rollback, the auto-update hold |
| `/run/user/<uid>/gpu-yield/status.json` | Live status (in memory, cleared on reboot) |
| `~/.cache/gpu-yield/hosts.json` | On *other* computers: PCs that `wake` knows how to reach |

## Limits and security

- **Ollama has no authentication.** Anyone who can reach port 11434 can use the AI. Setup only opens it to your local subnet; don't forward it on your router. The status page shows no conversation data, and is read-only unless you set a control password. That password crosses your network unencrypted, so don't reuse an important one.
- **There's a short gap at each handoff.** The stand-in exists only while Ollama is stopped, so connections are refused for under a second whenever the AI stops or starts.
- **Native Linux games outside Steam** need to be added to `extra_processes`.
- **gpu-yield can't keep the PC from sleeping;** see [Sleep and Wake-on-LAN](#sleep-and-wake-on-lan).
- **Not tested on NVIDIA or Intel GPUs.** The BC-250 support is tested against simulated readings, not yet on a real board.

## Example setup

The setup gpu-yield was built for:

- **Gaming PC:** a living-room PC running Bazzite in Steam Gaming Mode, with a Radeon RX 7800 XT (16 GB) and 32 GB of RAM, connected over Wi-Fi. Hermes uses a 64K context. `backend` resolves to ROCm (the RX 7800 XT is `gfx1101`).
- **Clients:** Hermes Agent on a desktop on the same network, set to the model name `gpu-yield` as in [Connecting clients](#connecting-clients), plus the status page on a phone.
- **Models that didn't work out:**
  - `qwen3.6:35b-a3b` (about 23 GB) loaded on this 16 GB card, but on ROCm the GPU read part of it from system RAM through the graphics driver. The driver logged repeated stalls, and the PC later turned off.
  - `gpt-oss:20b` fit, but at 64K it used about 15 GB. One reload ran out of GPU memory, and in practice it did poorly on Hermes's tasks.
  - `qwen3.5:9b` uses about 10.5 GB at 64K, leaving several GB free.
- **Day to day:** games are launched from Big Picture, and updates can be run from the desktop with `ssh <gaming-pc> gpu-yield update`. Models and context can be changed from the status page on any device.
- **Sleep:** the PC stays on all the time, so the AI is always reachable and Wake-on-LAN isn't used.

On a card with less memory, such as a desktop with an RX 6700 XT (12 GB) and 32 GB of RAM, the same install picks Vulkan automatically. `qwen3.5:9b` fits there, with `context_length` 32768 for headroom when the card also drives the desktop; `qwen3.5:4b` is the light option.

## Testing

**Tested on real hardware** (the [example setup](#example-setup)):

- installing with `setup`, and upgrading an earlier install by re-running it
- detecting a game launched from Big Picture and freeing the GPU
- reloading the model after the game closed

**Tested in simulation**, against fake processes, a fake Ollama server and a fake GitHub:

- the 503 stand-in, pause and resume, restart after sleep
- Wine/Proton detection, game names (Steam manifests, non-Steam shortcuts), `extra_processes` matching
- `update`, `rollback`, automatic updates and Ollama updates, including failed and interrupted downloads
- recovery from a crashed, hung or emptied Ollama, and from a broken config
- the status page, `status --host`, `wake`, the `config` command, and backend choice
- the status page's controls: password lock, model switching, context changes, downloads with progress and cancel, deletes, and pause, including driving the page in a real browser

Wake-on-LAN and Wake on Wireless LAN themselves (`nmcli`, `ethtool`, `iw`, the BIOS) can't be simulated; check it with `gpu-yield wol` and a real `gpu-yield wake`. After installing on new hardware, run `gpu-yield doctor`, then start a game and check `gpu-yield status`.

The tests run on GitHub for every change. To run them yourself you need only Python 3.11+, plus `zstd` for the Ollama install tests. They run in a throwaway home folder and don't touch a real install:

```bash
python3 tests/run_tests.py
```

## Uninstall

```bash
gpu-yield uninstall            # remove the services and the installed tool
gpu-yield uninstall --purge    # also remove Ollama, the config and state
```

Downloaded models stay in `~/.ollama/models` until you delete them. If setup opened the firewall, `uninstall` prints how to remove those rules.
