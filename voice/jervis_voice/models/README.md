# Wake-word models

Phase 3 ships with openwakeword's built-in **`hey_jarvis`** model so nothing is
blocked on training. A custom `jervis.onnx` goes in this directory and is
selected via `voice.wake_model: jervis` in `~/.jervis/config.yaml`.

`.onnx` / `.tflite` files here are gitignored - they are build artefacts, not source.

## Training a `jervis` model

`scripts/train-wakeword.sh` (Phase 3) wraps openwakeword's synthetic-data
training pipeline. The short version:

1. Generate ~1000 synthetic utterances of "Jervis" with varied TTS voices.
2. Mix in negative data (openwakeword's published negative sets + room noise).
3. Train, then sweep the threshold on a held-out set; put the chosen value in
   `voice.wake_threshold`.
4. Drop the resulting `jervis.onnx` here and flip `wake_model`.

Retrain whenever the false-accept rate becomes annoying in daily use - the
threshold is the cheaper first lever.
