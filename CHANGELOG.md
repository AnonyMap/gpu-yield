# Changelog

`gpu-yield update` shows the entries between your version and the new one.

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
