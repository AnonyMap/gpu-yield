# gpu-yield

[![tests](https://github.com/AnonyMap/gpu-yield/actions/workflows/tests.yml/badge.svg)](https://github.com/AnonyMap/gpu-yield/actions/workflows/tests.yml)

**Use your Linux gaming PC as a home AI server, without it ever getting in the way of a game.**

While the PC sits idle, gpu-yield keeps a local AI model loaded in [Ollama](https://ollama.com) and available to other computers on your network. The moment a game starts, it shuts the AI down so the game gets the whole GPU. When the game ends, the AI comes back on its own. You never have to switch anything by hand.

It's a single Python file with no dependencies, built for [Bazzite](https://bazzite.gg) and similar Steam-based setups.

> **About this project:** gpu-yield was written with AI (Claude, an AI coding assistant) by someone who is not a software developer. Each change goes through automated tests and a separate AI review, but no professional programmer has checked it, and you should know that before you install it. The code is fully open under the [MIT license](LICENSE): anyone can use it, change it, and read it to check what it does.

## Install

You need:
- **Linux with systemd**, launching games through Steam. Built on Bazzite in Gaming Mode.
- **Python 3.11 or newer**, which Bazzite already has.
- **An AMD GPU.** Other GPUs are untested.

On the gaming PC, in a terminal (in Desktop Mode or over SSH):

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o /tmp/gpu-yield
python3 /tmp/gpu-yield setup
```

Setup asks a few questions, then installs everything into your home folder. Nothing is added to the system image. It:

1. offers a short list of models that fit your GPU (or name any Ollama model, or pick later)
2. downloads Ollama, set up for your card
3. installs gpu-yield as a background service that starts at boot
4. offers to open the AI to your local network only, and to turn on Wake-on-LAN
5. downloads the model, loads it, and prints the address for your AI apps

Run it as your normal user; it asks for `sudo` only for the firewall and Wake-on-LAN steps. Running setup again is safe and keeps your settings.

**Then point your AI apps at it.** gpu-yield runs a normal Ollama server, so anything that talks to Ollama or to an OpenAI-compatible API works:

- Ollama: `http://<gaming-pc>:11434`
- OpenAI-compatible: `http://<gaming-pc>:11434/v1` (no API key)
- Model name: `gpu-yield`, which always points at whichever model is loaded
- Context length: the same as gpu-yield's, 65536 by default

## Features

- **Automatic game detection.** It covers everything launched through Steam, including non-Steam shortcuts, and Windows games from Heroic or Lutris. There's no per-game setup.
- **A full handoff.** Ollama is stopped within about 2 seconds of a game starting, which frees all of its GPU memory and RAM. It comes back about 45 seconds after the game closes, so quick game switches don't cause reloads.
- **A clear answer while the AI is off.** Apps get an HTTP 503 saying why ("a game is running") and when to try again, so an app with a backup provider can switch to it.
- **A status page** for any browser, including a phone. It shows whether the AI is ready, what game is running, memory use and recent events. With a password set, it can also:
  - switch models
  - change the context length
  - download and delete models
  - pause, resume and restart the AI
  - run speed tests
- **Simple commands:** `status`, `doctor` (finds problems and suggests fixes), `model`, `config`, `restart`, `pause`/`resume`.
- **Crash protection:**
  - It refuses or unloads models that don't fit in GPU memory where that has crashed PCs.
  - It caps Ollama's RAM.
  - It recovers from Ollama crashes and hangs.
  - It restarts the AI after sleep.
- **Wake-on-LAN:** `gpu-yield wake` from another computer wakes the PC and waits until the AI answers.
- **Safe updates:** `gpu-yield update` checks the new version before using it, and `rollback` undoes it. Automatic updates are optional and never interrupt a game or a conversation.
- **Support for the AMD BC-250** and other chips where the GPU shares system memory.

## Why use it

- **One PC does both jobs.** The GPU you already own for gaming runs your AI the rest of the time, so there's no second machine to buy or power.
- **Games always come first.** You don't have to remember to stop the AI before playing, and a game never shares the GPU with it.
- **Your AI apps keep working without reconfiguration** across model changes, games and reboots.
- **It's hard to break.** Mistakes in the config, out-of-memory errors and crashes are caught and explained, not left to guess at.
- **It stays out of the system.** Everything lives in your home folder and runs as your user, and `uninstall` removes it cleanly.

---

## How it works

**Spotting games.** Steam starts every game through a small helper process called `reaper`, which lives exactly as long as the game. gpu-yield checks for it every 2 seconds.

Windows games outside Steam run through Wine. A Windows program counts as a game once it's using the GPU and has run for 5 seconds, so installers and Wine's helper programs don't stop the AI.

Native Linux games outside Steam can't be told apart from other programs. Add them by name: `gpu-yield config extra_processes "factorio"`.

**Why it stops Ollama instead of just unloading the model.** Stopping the process is the only way to be sure every byte of GPU memory, including the driver's own share, is given back to the game. While Ollama is stopped, a tiny stand-in answers on its port with the 503 and the reason.

**Why it checks that models fit.** Ollama can run part of a model from system RAM. On Vulkan that only costs speed. On ROCm, the AMD driver used on newer cards, the PC this was built on stalled and shut down when that happened. So on ROCm, gpu-yield checks every model as it loads. If any of it lands outside the card's memory, gpu-yield unloads it and goes back to the last model that fit.

If an app keeps loading a model or context that doesn't fit, gpu-yield pauses the AI and says why, until you fix that app's setting and run `gpu-yield resume`. Set `allow_partial_gpu = true` to allow models that don't fit anyway.

**Why it limits Ollama's RAM.** On ROCm, Ollama may use at most a third of the PC's RAM, and it's kept out of swap. If it goes over, Linux stops Ollama alone rather than the whole PC slowing down, and gpu-yield reloads the model. If that repeats, gpu-yield stops and tells you instead of looping.

**Why it restarts after sleep.** GPU compute can be unreliable after the PC wakes, so Ollama is restarted.

## Choosing a model

```bash
gpu-yield model              # pick from a list sized for your GPU
gpu-yield model qwen3.5:9b   # or name any Ollama model
```

| Model | Download | Memory at 64K context | Notes |
|---|---|---|---|
| `qwen3.5:9b` | ~6.6 GB | ~10.5 GB | Recommended. Fits a 12 GB card, fast, accepts images |
| `gemma4:12b-it-qat` | ~7.2 GB | ~12 GB | Good tool calling and image input; needs a 16 GB card |
| `qwen3.5:4b` | ~3.4 GB | ~5 GB | Smallest; weak at multi-step tasks |

**What has to fit is the model plus its context**, the conversation memory, which grows with `context_length`. AI agents such as Hermes want 64K. Depending on the model's design, that costs anywhere from 2 GB to 10 GB or more, so a download that looks small enough can still be too big. Steam's own interface also uses up to about 2 GB of GPU memory, so leave at least 1.5 GB free.

| GPU memory | Starting point at 64K context |
|---|---|
| 8 GB | `qwen3.5:4b`, or `qwen3.5:9b` at `context_length` 32768 |
| 12 GB | `qwen3.5:9b` (32768 if the card also drives a desktop) |
| 16 GB | `qwen3.5:9b` with room to spare, or `gemma4:12b-it-qat` |
| 24 GB+ | Bigger models, such as `qwen3.6:27b` |

Avoid models whose download is close to your card's memory. On a 16 GB card, `gpt-oss:20b` uses about 15 GB at 64K and did poorly on real agent tasks.

To compare models, use the speed test on the status page or `gpu-yield speed`. It measures how fast the model reads a long prompt, which matters most for agents, and how fast it writes.

## Connecting apps

**Use the model name `gpu-yield`.** It's a name gpu-yield keeps pointed at the loaded model, so apps follow every switch. It shares the model's files and takes no extra space. Change or turn it off with the `alias` setting.

**Match the context length.** If an app asks for a different size, Ollama reloads the model to suit it, which is slow and may not fit. The status page shows a note when an app does this. Leaving the context unset in the app also works, because Ollama then uses gpu-yield's.

**Example: [Hermes Agent](https://github.com/NousResearch/hermes-agent)**, in `~/.hermes/config.yaml` (setup prints this with your address filled in):

```yaml
model:
  default: gpu-yield
  provider: custom
  base_url: http://<gaming-pc>:11434/v1
  context_length: 65536
  ollama_num_ctx: 65536
```

## Status page and controls

Open `http://<gaming-pc>:11435` in any browser. It updates live.

To turn on the controls, set a password on the gaming PC:

```bash
gpu-yield config control_password -
```

It asks without showing what you type; press Enter and it makes up a strong one. The page is plain HTTP on your local network, so don't reuse an important password. After 10 wrong guesses, that device is locked out for a minute, doubling up to an hour. Other devices aren't affected.

Downloads accept Ollama names (`qwen3.5:9b`) and Hugging Face GGUF repos (`hf.co/user/repo:Q4_K_M`), or their web links. Changes made during a game are saved and take effect when the AI comes back.

Don't want the page? `gpu-yield config status_page false`.

<details>
<summary>The page's data as JSON, for scripts and agents (<code>/status</code>)</summary>

| Field | Meaning |
|---|---|
| `ai_available` | `true` when the model is loaded and requests will be answered |
| `state` | `ai`, `gaming`, `cooldown` (game just closed) or `paused` |
| `retry_after` | Seconds to wait before trying again (0 when available) |
| `games` | Running games, by name |
| `model`, `context_length`, `model_status` | The configured model and its load state |
| `in_use`, `last_used` | Whether another machine is using the AI now, and when it last did (Unix time) |
| `notice` | A problem worth fixing, such as an app loading a different context |
| `cooldown_remaining`, `since` | Seconds until the model reloads after a game; when the current state began |
| `alias`, `loaded_name` | The fixed model name for apps, and the name the model is loaded under |
| `memory` | Bytes, from the driver and kernel: `vram_used`, `vram_total`, `gtt_used` (system RAM used by the GPU), `ram_used`, `ram_total` |
| `events` | Recent events, newest first: `at`, `text`, `kind` (`info`, `warn`, `error`) |
| `speed_results` | Recent speed tests: `model`, `read_tps`, `write_tps`, `context_length`, `at` |
| `host`, `version`, `port`, `mac` | The PC's name, gpu-yield version, AI port, and network card address |

Websites can't read this from your browser, since it names the PC and its network card.
</details>

## From other computers, sleep and Wake-on-LAN

The remote commands need only the script, and should work on macOS and Windows too:

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/gpu-yield -o ~/.local/bin/gpu-yield
chmod +x ~/.local/bin/gpu-yield
gpu-yield status --host gaming-pc.local   # that PC's status
gpu-yield wake gaming-pc.local            # wake it and wait until the AI answers
```

If the gaming PC sleeps, nothing can reach the AI. You have two options:

- **Wake it when needed.** Run `gpu-yield wol` once on the gaming PC to turn on Wake-on-LAN. Then use `gpu-yield wake` from other computers. It exits with 0 once the AI is ready, so `gpu-yield wake gaming-pc.local && my-script` only runs when the AI can answer.
  - A wired connection is most reliable. Some PCs also need Wake-on-LAN turned on in the BIOS.
  - On Wi-Fi it works only if the card supports it, and only from sleep; `gpu-yield wol` checks.
- **Don't let it sleep.** Set Steam's sleep timer to *Never* (Steam → Settings → Power). gpu-yield can't block sleep itself, because Steam runs as the same user and can override it.

## AMD BC-250 and shared-memory chips

Some chips have no graphics memory of their own and borrow system memory instead: the AMD BC-250 board and most APUs. gpu-yield notices and adjusts:

- **Readings** show the shared memory and how much of it the GPU uses.
- **A stricter fit check.** The model and Linux share one pool, and running it out freezes the whole board.
  - A model file over the pool minus about 3 GB is refused. That's about 12.5 GB on a BC-250.
  - A model that leaves Linux under 0.75 GB is unloaded.
  - While a model runs, two low readings in a row also unload it. An app's model is blamed only if it uses more memory than gpu-yield's own.
- **Ollama is allowed to use the GPU.** Ollama ignores integrated GPUs by default and would otherwise run everything on the processor.
- **Smaller memory overheads.** The model runner's prompt cache is capped at 512 MB instead of 8 GB, and models load without filling the file cache.

On a BC-250, gpu-yield also stores context at 8 bits, and `gpu-yield doctor` checks the board's known pitfalls: kernel versions, memory limits, BIOS split, clock governor, swap and logs. It only reports and never changes system settings.

For running Muse Glimmer 30B text-only on these boards, see [extras/glimmer-text](extras/README-glimmer-text.md).

## Updating

```bash
gpu-yield update --check   # what's new
gpu-yield update           # install the latest gpu-yield
gpu-yield update --ollama  # also update Ollama
gpu-yield rollback         # go back to the previous version
```

Updates show what changed (from the [changelog](CHANGELOG.md)). Before an update is used:

- The new version tests itself, and a failing one is refused.
- A new Ollama is unpacked alongside the old one and swapped in only once it runs.

The model reloads once after an update.

**Automatic updates** are off by default. Turn them on with `gpu-yield config auto_update gpu-yield` (or `all` to include Ollama). gpu-yield then checks once a day and installs only when:

- no game is running
- the AI has been idle for 15 minutes
- nothing is downloading

## Concerns and limits

- **Ollama has no password.** Anyone who can reach port 11434 can use the AI. Setup opens it to your local network only; never forward it on your router.
- **Short gaps.** Connections are refused for under a second each time the AI stops or starts.
- **Native Linux games outside Steam** have to be added to `extra_processes`.
- **Hardware coverage:**
  - Real-world use is on an RX 7800 XT with ROCm, on Bazzite. Vulkan cards such as the RX 6700 XT are covered by the tests but less used.
  - NVIDIA and Intel GPUs are untested.
  - The BC-250 support was built from simulated readings and is now in early use on a real board.
- **Made with AI by a non-developer** (see the note at the top). If something looks wrong in the code, please [open an issue](https://github.com/AnonyMap/gpu-yield/issues).

## Reference

### Commands

| Command | What it does |
|---|---|
| `gpu-yield setup` | Install or repair. Options: `--model NAME`, `--context TOKENS`, `--backend auto\|rocm\|vulkan`, `--reinstall`, `--skip-pull`, `--no-firewall`, `-y` |
| `gpu-yield status [--host HOST]` | Current state, here or on another PC |
| `gpu-yield doctor` | Check everything and suggest fixes |
| `gpu-yield pause` / `resume` | Keep the AI off until resumed (survives reboots) |
| `gpu-yield restart` | Unload the model, free GPU memory, reload it, and name anything else holding GPU memory |
| `gpu-yield model [NAME]` | Choose or change the model |
| `gpu-yield config [KEY [VALUE]]` | Show or change settings |
| `gpu-yield update [--check] [--ollama] [--file PATH]` | Update gpu-yield, and optionally Ollama |
| `gpu-yield rollback` | Undo the last update |
| `gpu-yield speed` | Measure how fast the loaded model reads and writes |
| `gpu-yield wol` / `wake [HOST\|MAC]` | Turn on Wake-on-LAN / wake a PC and wait for the AI |
| `gpu-yield firewall` | Open the AI and status ports to the local network |
| `gpu-yield uninstall [--purge]` | Remove it; `--purge` also removes Ollama, the config and state. Models stay in `~/.ollama/models` |

### Settings

Change settings with `gpu-yield config NAME VALUE`. Values are checked before they're saved, and changes take effect right away. Settings live in `~/.config/gpu-yield/config.toml`, which explains each one.

| Setting | Default | What it does |
|---|---|---|
| `model` | none | The model kept loaded (set with `gpu-yield model`) |
| `context_length` | `65536` | Context window in tokens; keep apps set to the same |
| `backend` | `"auto"` | `rocm` on cards ROCm supports (RX 7000/9000, RX 6800/6900, Radeon VII, Ryzen AI Max), `vulkan` on the rest. Apply a change with `setup --reinstall` |
| `port`, `status_port` | `11434`, `11435` | The AI's port and the status page's port |
| `status_page` | `true` | Serve the status page |
| `control_password` | none | Turns on the page's controls; at least 8 characters |
| `cooldown_seconds` | `45` | Wait after a game closes before reloading |
| `poll_seconds` | `2` | How often to check for games |
| `restart_after_suspend` | `true` | Restart Ollama after sleep |
| `extra_processes` | `[]` | Extra programs that count as games |
| `ignore_appids` | `[]` | Steam games that may run alongside the AI (AppId from `status`) |
| `detect_wine` | `true` | Treat Windows programs outside Steam that use the GPU as games |
| `auto_update` | `"off"` | `off`, `gpu-yield`, or `all` |
| `alias` | `"gpu-yield"` | The fixed model name for apps; empty turns it off |
| `ollama_ram_limit` | `"auto"` | Most RAM Ollama may use: `auto` (a third of RAM on ROCm), `off`, or GB |
| `allow_partial_gpu` | `false` | Allow models that don't fit in GPU memory. Not recommended on ROCm |
| `update_repo`, `update_branch`, `github_token` | this repo | Where updates come from, for forks |
| `ollama_unit` | managed | Leave as is, unless you run Ollama yourself under another service |
| `[ollama_env]` | none | Extra Ollama variables: `gpu-yield config env.NAME VALUE` (`""` removes one) |

### Files

| Path | What |
|---|---|
| `~/.local/bin/gpu-yield` | The tool |
| `~/.local/opt/ollama/` | Ollama |
| `~/.ollama/models/` | Downloaded models |
| `~/.config/gpu-yield/config.toml` | Settings (readable only by you) |
| `~/.config/systemd/user/gpu-yield*.service` | The services |
| `~/.local/state/gpu-yield/` | Pause flag, previous version for rollback |

### Logs

```bash
journalctl --user -u gpu-yield -u gpu-yield-ollama -f
```

### Tests

The tests run on GitHub for every change, against simulated games, a simulated Ollama and simulated hardware. To run them yourself you need Python 3.11+ and `zstd`:

```bash
python3 tests/run_tests.py
```

They use a throwaway home folder and don't touch a real install. Things that can't be simulated, like Wake-on-LAN and real games, have been checked on the hardware listed under [Concerns and limits](#concerns-and-limits). On new hardware, run `gpu-yield doctor`, then start a game and check `gpu-yield status`.
