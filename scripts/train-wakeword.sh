#!/usr/bin/env bash
# Train a custom "jervis" wake word.
#
# Not automated: openwakeword's training pipeline wants a GPU-hours-scale synthetic
# data run, and pretending otherwise with a one-liner would waste your afternoon.
# This prints the actual steps and where the output goes.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

MODEL_DIR="$JERVIS_HOME/models/openwakeword"

cat <<TEXT
Training a custom "jervis" wake word
====================================

Until this is done, Jervis answers to the built-in "Hey Jarvis" model. That is a
real wake word, not a placeholder - the only thing you gain by training is being
able to say "Jervis" instead.

The pipeline lives in openwakeword itself:

  1. Open the automatic training notebook:
       https://github.com/dscripka/openWakeWord
     (notebooks/automatic_model_training.ipynb - it runs on a free Colab GPU)

  2. Set the target phrase to "jervis" and generate ~1000 synthetic utterances
     across varied TTS voices, plus openwakeword's published negative sets and
     room-noise augmentation.

  3. Train, then sweep the detection threshold on held-out audio. Note the value
     that gives you roughly one false accept per day - that goes in
     voice.wake_threshold.

  4. Download jervis.onnx and drop it here:
       $MODEL_DIR/jervis.onnx

  5. Point Jervis at it:
       voice:
         wake_model: jervis
         wake_threshold: <the value from step 3>

  6. Check it: scripts/test-voice.sh

Before retraining because it misfires, try raising voice.wake_threshold - that is
the cheaper lever and usually the right one.
TEXT

mkdir -p "$MODEL_DIR"
say ""
info "custom models go in: $MODEL_DIR"
