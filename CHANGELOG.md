# Changelog

`gpu-yield update` shows the entries between your version and the new one.

## 1.11.1 (2026-11-02)
- Setup's model list now offers only models that fit at an agent's 64K context: `qwen3.5:9b` (recommended; about 10.5 GB, so it fits 12 GB cards), `gemma4:12b-it-qat` (needs a 16 GB card) and `qwen3.5:4b`. It flags a model too big for this PC's card. `gpt-oss:20b` is gone from the list: at 64K it nearly fills a 16 GB card, so loads could run out of memory, and it did poorly on real agent tasks. `qwen3.6:35b-a3b` is gone too: it doesn't fit a 16 GB card, and running it partly from system RAM on ROCm stalled the driver. Any model can still be chosen by name.
- The README explains why context, not download size, decides what fits, and which models to avoid on 16 GB cards.

## 1.11.0 (2026-11-02)
Fixes from a security audit, a bug review and a performance review.

Security:
- The stand-in that answers on Ollama's port during games could be made to use up the PC's memory by any device on the network. It now throws request bodies away as they arrive, and limits connections per device and how long each may take.
- Wrong control passwords lock out only the device guessing, for a minute, then two, four and so on up to an hour. Before, one device could keep everyone locked out.
- `gpu-yield config control_password -` asks for the password without showing it, or makes up a strong one, so it stays out of shell history and process lists. Error messages never print a password or token from the config.
- Websites can no longer read `/status` from your browser (it names the PC and its network card), and the page can't be shown inside another site.
- `status --host` and `wake` check what another PC sends back: size, shape and network card address, and they strip escape codes that could retitle your terminal, write to your clipboard or fake output.
- A request with an unexpected host name no longer runs a program on the PC each time.

Fixes:
- When gpu-yield started or resumed during a Windows game (Heroic, Lutris), it began loading the model for 5 seconds before noticing the game. Windows games now count at once whenever the AI isn't already running, or when they've been running a while.
- `setup --model NAME` saves the new model only once it has downloaded; a typo or failed download keeps the working one. Model names typed at setup are checked like everywhere else.
- Choosing the ROCm backend without its separate download kept Ollama on the CPU until a reinstall. Ollama stays on Vulkan until then, setup downloads ROCm when it's missing, and `doctor` says so.
- A config file error now also stops your own Ollama unit (if you use one), not just gpu-yield's.
- The automatic restart after running out of GPU memory could be skipped if the model or context was changed during a restart.
- `wake` waits until the AI is ready, not just the PC, and its exit code says whether it is.
- `status --host` and `wake` give a message instead of a Python error when something other than gpu-yield answers.
- The stand-in reads chunked request bodies too, so those clients get the 503 and its retry time instead of a dropped connection.
- `gpu-yield config` accepts values containing emoji.

Performance (about 70% less CPU time while a game runs):
- The status page and stand-in sleep until a connection arrives instead of waking twice a second.
- The status file is only rewritten when something besides memory readings changed.
- While a game runs, the watcher checks every 5 seconds instead of 2; a game ending only starts the cooldown anyway. Game starts are still caught within 2 seconds.
- Kernel threads, which make up about half the process list, are skipped after the first look.
- The watcher runs at a lower CPU priority than games.
- The status page stops polling while its browser tab is hidden.

## 1.10.0 (2026-10-31)
- Restart the AI: `gpu-yield restart` or the status page's *Restart the AI* button unloads the model, stops Ollama, waits until the GPU memory is actually free again, and reloads the model. It lists any other program still holding GPU memory, which is what a restart can't fix. It won't start while a download or speed test is running; the automatic one waits for it to finish.
- When a model fails to load because the GPU ran out of memory, gpu-yield restarts Ollama by itself once and tries again. If it still doesn't fit, it says which programs hold the memory and suggests a smaller context, instead of retrying the same failure.
- `gpu-yield doctor` lists the programs using GPU memory.

## 1.9.1 (2026-10-31)
Fixes from three independent reviews:
- Setup and `gpu-yield model` check settings before saving them. A bad value, such as `setup --context 64`, used to stay in the config and keep the AI off after the next restart. `model` refuses the fixed app name before downloading anything.
- A failed `setup --reinstall` no longer leaves the AI off until the next reboot.
- Port and context settings must be whole numbers: `port 11434.0` used to break the AI. A value out of range (such as `poll_seconds 0.01`) is refused instead of being saved.
- `gpu-yield config` can change a list written over several lines in the file, and keeps the file readable only by you.
- Automatic updates wait for status-page downloads and speed tests to finish, and "once a day" now counts time the PC spends asleep.
- `gpu-yield update` gives a message instead of a Python error when the connection drops mid-download.
- Status page: a website could reach the controls through your browser using a name like `<your-pc>.example.com`. Only the PC's own local names are accepted now.
- Status page: a few deliberately slow connections could keep it unreachable. Each request now has 15 seconds, and one device can have at most 6 connections open.
- Status page: a model chosen to switch to, or downloading with "use it" ticked, can no longer be deleted in the moment before the switch happens.
- Status page: the model list says why if it can't load, instead of showing "Loading…" forever. The "> VRAM" tag compares sizes in the same units. Times no longer show as "1h 60m".
- `wake` keeps waiting while a PC boots, instead of stopping at the first answer from a PC whose gpu-yield hasn't started yet.
- The fixed app name could get stuck on an old model if a game started at the moment it was being moved. Now it can't.
- An Ollama that takes more than a minute to start (after an update, or slow GPU setup) is no longer restarted over and over.
- A Windows game starting during the cooldown after another game counts at once, so the model doesn't start loading while it launches.
- Turning the status page off while gpu-yield was starting up could be missed.

## 1.9.0 (2026-10-31)
- The status page can be turned off: `gpu-yield config status_page false` (and `true` to bring it back). It takes effect within a few seconds without restarting the AI. `status --host` and `wake` from another PC say when they reach a PC whose page is off, instead of treating it as asleep.

## 1.8.0 (2026-10-31)
- A fixed model name for your apps: set your app's model to `gpu-yield` and it always gets whichever model is loaded, so switching models no longer means editing the app's settings. It's an Ollama copy of the model, so it takes no extra download or disk space. Change or turn it off with the `alias` setting. It never overwrites a model gpu-yield didn't create.
- Real memory readings: the status page, `status` and `doctor` show actual GPU memory and RAM use from the driver, including system RAM the driver has mapped for the GPU, instead of relying on Ollama's estimate.
- Speed test: a button on the status page (and `gpu-yield speed`) measures how fast the loaded model reads a long prompt and writes an answer, and keeps results so models can be compared. It asks first if the AI is in use, since a test replaces Ollama's cached conversation.
- Recent events: the status page and `status` list what happened lately, such as games starting (by program name), the AI coming back, model switches, downloads and errors.

## 1.7.1 (2026-10-31)
- The status page says which folder's drive the free space is for, and follows `OLLAMA_MODELS` if you've moved Ollama's model folder (it used to always measure the default `~/.ollama/models`).

## 1.7.0 (2026-10-31)
- The status page has controls: switch models, change the context length, download models by Ollama name or Hugging Face repo (or either site's URL) with live progress and cancel, delete models, and pause or resume the AI. Turn them on with `gpu-yield config control_password YOUR-PASSWORD` (at least 8 characters); without a password the page stays read-only. Ten wrong passwords in a minute lock the controls for the rest of that minute, and requests addressed to a name other than the PC's are refused.
- Wake on Wireless LAN: on Wi-Fi, `gpu-yield wol` checks whether the card can wake the PC and turns it on if so. `wake` also sends the wake-up signal directly to the PC's address, which some Wi-Fi routers need.
- The status page updates live instead of reloading every 5 seconds.

## 1.6.1 (2026-10-31)
- Fixed: the AI stopped for about a minute whenever Wine ran briefly for something that wasn't a game, such as Proton's `xalia.exe` helper or a Steam background task. A Windows program outside Steam now counts as a game only if it is using the GPU and keeps running for 5 seconds. Steam games are still detected instantly. If you turned `detect_wine` off because of this, you can turn it back on: `gpu-yield config detect_wine true`.

## 1.6.0 (2026-10-31)
- Status page for other computers: open `http://<this-pc>:11435` in a browser, or `gpu-yield status --host <this-pc>` from another PC running gpu-yield. JSON at `/status`. Open the port with `gpu-yield firewall`.
- Wake-on-LAN: `gpu-yield wol` turns it on for this PC; `gpu-yield wake <this-pc>` from another computer wakes it and waits until the AI is reachable.
- `gpu-yield config` lists settings; `gpu-yield config KEY VALUE` changes one, checks it, and applies it.
- New installs choose the GPU backend automatically (`backend = "auto"`): ROCm on GPUs ROCm supports, Vulkan otherwise. `doctor` warns when a GPU isn't supported by ROCm.
- Optional automatic updates: `gpu-yield config auto_update gpu-yield` (or `all` to include Ollama). Checked daily; installs only while no game is running and the AI hasn't been used for 15 minutes, and never reinstalls a version you rolled back from.
- `status` shows when the AI was last used.
- Config values that older versions accepted (numbers in quotes, a single AppId without brackets, a very short poll interval) are read with a warning instead of keeping the AI off.
- Tests run on GitHub for every change.

## 1.5.0 (2026-10-31)
- Ollama updates download next to the working install and only replace it once they run.
- Ollama can't keep the GPU if the watcher dies; a broken config keeps the AI off and tells clients why instead of crash-looping.
- The stand-in closes every connection, so clients reach Ollama as soon as it's back.
- Health checks every 15 s: reload a dropped model, restart a hung Ollama, note when a client loads a different model or context.
- `status` shows game names instead of AppIds.
- Faster game detection; status written to memory instead of disk.
- Test suite in `tests/`.

## 1.4.0 (2026-10-31)
- Setup asks which model to use instead of always installing gpt-oss:20b.
- `gpu-yield model` chooses or changes the model.

## 1.3.0 (2026-10-31)
- Windows games started outside Steam (Heroic, Lutris) are detected through Wine.

## 1.2.0 (2026-10-30)
- `gpu-yield update --file PATH` installs a local copy.

## 1.1.0 (2026-10-30)
- `gpu-yield update` and `gpu-yield rollback`.

## 1.0.0 (2026-10-30)
- First release: stops Ollama while a Steam game runs and reloads the model afterwards, with a 503 stand-in for clients.
