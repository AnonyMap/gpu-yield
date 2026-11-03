# glimmer-text

Sets up a **text-only** build of Meta's Muse Glimmer 30B in Ollama, sized for your PC.

Muse Glimmer's Hugging Face repos keep the language model and its image-understanding part (about 4 GB) in separate files. Pulling the repo through Ollama (`ollama pull hf.co/...`) fetches and loads both. On a 16 GB card that pushed part of the model into system RAM, which on ROCm crashed the PC. This script downloads only the language-model file you pick, checks it, imports it into Ollama, and loads it.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AnonyMap/gpu-yield/main/extras/glimmer-text -o ~/.local/bin/glimmer-text
chmod +x ~/.local/bin/glimmer-text
```

Run it as your normal user on the PC that runs Ollama. It needs Python 3.11+ and Ollama; gpu-yield is optional but recommended.

## Use

```bash
glimmer-text list                  # the builds, with a fit verdict for this PC
glimmer-text install               # pick one interactively (defaults to the biggest that fits)
glimmer-text install AD-IQ3_XXS    # or name it
glimmer-text remove muse-glimmer-text-ad-iq3_xxs
```

`list` shows each build's size, what it is (for example "3-bit: noticeable on demanding tasks", "standard 4-bit", "the original (full precision)"), and whether it **fits**, is **tight**, or **won't fit** on this PC. The estimate is the file plus the model's context memory plus working space. Muse Glimmer's context is cheap: about 0.9 GB at 64K, because only 13 of its 52 layers keep the whole conversation.

- **Graphics cards:** the budget is the card's memory minus 1.5 GB for the desktop or Gaming Mode. On a 16 GB card, `AD-IQ3_XXS` (12.2 GB) fits; 4-bit and the original don't.
- **Shared-memory boards** like the BC-250: the budget is RAM minus 2.5 GB for Linux.

`install`:

1. Checks there's disk space for the file twice (the download, and Ollama's copy of it).
2. Downloads only that file, continuing a partial download if one is there.
3. Verifies its SHA256 against Hugging Face's.
4. Imports it with a one-line recipe (`FROM <file>`), so there's no image part.
5. Deletes the download (`--keep` to keep it).
6. Loads it through `gpu-yield model`, which checks it fits and switches gpu-yield to it. Apps set to the model name `gpu-yield` follow automatically, and the summary says how to switch back.

Without gpu-yield, it loads the model directly and tells you how much of it is on the GPU.

Options: `--repo bartowski/Muse-Glimmer-30B-GGUF` for another publisher's builds, `--context N` to plan for a different context length, `--dir` for the download folder, `--name` for the Ollama model name, `--no-load`, and `--force` to install a build that won't fit (not recommended on ROCm).

Ctrl-C keeps the partial download; run the same command again to continue. Builds split into several files (the full-precision original usually is) can't be imported by Ollama directly, so they're listed but refused.

## Build names

| Part | Meaning |
|---|---|
| `IQ2`, `IQ3`, `IQ4` | 2-, 3-, 4-bit "i-quants": small groups of weights stored as an index into a table of common patterns; the most accurate per bit |
| `Q4_K_M`, `Q8_0` | Older K-quant / 8-bit formats; `Q4_K_M` is the standard 4-bit, `Q8_0` is practically the original |
| `BF16` | The original, full precision |
| `XXS`, `M`, `XS` | Smaller or larger variants within the same bit level |
| `AD-`, `UD-` | The publisher's recipe for which parts of the model get more bits (AtomicChat, Unsloth) |
